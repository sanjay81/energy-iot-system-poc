# iot_device/iot_device.py

import time
import logging
import os
from typing import Optional

from iot_device.modbus_client import (
    InverterModbusClient,
    PowerMeterModbusClient
)
from iot_device.calculator import (
    EnergyMeasurement,
    calculate_house_consumption
)
from iot_device.publisher import MeasurementPublisher, NullPublisher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Polling interval in seconds
POLL_INTERVAL = 5.0

# Maximum acceptable timestamp delta between reads
MAX_TIMESTAMP_DELTA_MS = 1000.0


class IoTDevice:
    """
    The IoT Device — hub of the entire architecture.
    Sits on every interface except CAN.

    Responsibilities:
    1. Poll Inverter via Modbus TCP (simulating RTU)
    2. Poll Power Meter via Modbus TCP
    3. Calculate House Consumption
    4. Validate measurements
    5. Forward to cloud via MQTT (Phase 3)
    """

    def __init__(
        self,
        inverter_host: str = "localhost",
        inverter_port: int = 5020,
        powermeter_host: str = "localhost",
        powermeter_port: int = 5021,
        poll_interval: float = POLL_INTERVAL,
        publisher: MeasurementPublisher | None = None
    ):
        self.inverter_client = InverterModbusClient(
            host=inverter_host,
            port=inverter_port
        )
        self.powermeter_client = PowerMeterModbusClient(
            host=powermeter_host,
            port=powermeter_port
        )
        self.poll_interval = poll_interval
        self._running = False
        self._last_measurement: Optional[EnergyMeasurement] = None
        self._fault_flags: list[str] = []

        # MQTT is an adapter, not a hard dependency of the gateway.
        # Local runs and tests use a no-op publisher unless one is injected.
        self.publisher = publisher or NullPublisher()

    def connect(self) -> bool:
        """Connect to both Modbus devices."""
        inv_ok = self.inverter_client.connect()
        pm_ok  = self.powermeter_client.connect()

        if not inv_ok:
            logger.error("[IoT] Cannot connect to Inverter")
            self._raise_fault("inverter_connection_failed")

        if not pm_ok:
            logger.error("[IoT] Cannot connect to Power Meter")
            self._raise_fault("powermeter_connection_failed")

        self.publisher.connect()

        self._publish_active_faults()

        return inv_ok and pm_ok

    def disconnect(self):
        self.inverter_client.disconnect()
        self.powermeter_client.disconnect()
        self.publisher.disconnect()

    def _raise_fault(self, fault_code: str):
        """
        Raise a fault flag.
        In production this triggers an alert to operations.
        Critical — prevents silent failures reaching the cloud.
        """
        if fault_code not in self._fault_flags:
            self._fault_flags.append(fault_code)
            logger.warning(f"[IoT] FAULT RAISED: {fault_code}")

    def _clear_fault(self, fault_code: str):
        if fault_code in self._fault_flags:
            self._fault_flags.remove(fault_code)

    def _publish_active_faults(self):
        """Forward current faults even when a polling cycle cannot finish."""
        for fault in self._fault_flags:
            self.publisher.publish_fault(fault)

    def has_fault(self, fault_code: str) -> bool:
        return fault_code in self._fault_flags

    def poll_once(self) -> Optional[EnergyMeasurement]:
        """
        Single polling cycle.
        Reads Inverter, then Power Meter.
        Calculates House Consumption.
        Validates result.

        Returns EnergyMeasurement or None if failed.
        """
        # Read Inverter first
        inverter_data = self.inverter_client.read_measurements()

        if inverter_data is None:
            self._raise_fault("inverter_read_failed")
            self._publish_active_faults()
            return None
        self._clear_fault("inverter_read_failed")

        # Read Power Meter second
        # NOTE: There is a timing gap between these two reads.
        # This is the timing gap risk from our architecture analysis.
        pm_data = self.powermeter_client.read_grid_power()

        if pm_data is None:
            self._raise_fault("powermeter_read_failed")
            self._publish_active_faults()
            return None
        self._clear_fault("powermeter_read_failed")

        # Calculate House Consumption
        house = calculate_house_consumption(
            ac_output=inverter_data["ac_output"],
            grid_power=pm_data["grid_power"]
        )

        # Build measurement object
        measurement = EnergyMeasurement(
            pv_production=inverter_data["pv_production"],
            ac_output=inverter_data["ac_output"],
            battery_power=inverter_data["battery_power"],
            grid_power=pm_data["grid_power"],
            house_consumption=house,
            inverter_timestamp=inverter_data["timestamp"],
            powermeter_timestamp=pm_data["timestamp"]
        )

        # Validate — catch silent failures before cloud
        if not measurement.is_valid:
            self._raise_fault("invalid_measurement")
            logger.error(
                f"[IoT] Invalid measurement detected: {measurement}"
            )
            self._publish_active_faults()
            return None
        self._clear_fault("invalid_measurement")

        # Warn if timing gap too large
        if measurement.timestamp_delta_ms > MAX_TIMESTAMP_DELTA_MS:
            logger.warning(
                f"[IoT] Large timing gap: "
                f"{measurement.timestamp_delta_ms:.0f}ms "
                f"between Inverter and PowerMeter reads"
            )

        self._last_measurement = measurement

        logger.info(
            f"[IoT] PV={measurement.pv_production:.0f}W | "
            f"AC={measurement.ac_output:.0f}W | "
            f"Battery={measurement.battery_power:.0f}W | "
            f"Grid={measurement.grid_power:.0f}W | "
            f"House={measurement.house_consumption:.0f}W | "
            f"Δt={measurement.timestamp_delta_ms:.0f}ms"
        )
        # Publish to cloud
        self.publisher.publish_measurement(measurement)

        # Publish any active faults
        self._publish_active_faults()

        return measurement

    def run(self):
        """
        Continuous polling loop.
        Runs until stopped.
        """
        self._running = True
        logger.info(
            f"[IoT] Starting polling loop "
            f"(interval={self.poll_interval}s)"
        )

        while self._running:
            self.poll_once()
            time.sleep(self.poll_interval)

    def stop(self):
        self._running = False
        self.disconnect()
        logger.info("[IoT] Stopped")


if __name__ == "__main__":
    # The executable uses MQTT explicitly. Library consumers and tests can
    # inject another publisher or leave it disabled.
    from iot_device.mqtt_publisher import MQTTPublisher

    device = IoTDevice(
        inverter_host=os.getenv("INVERTER_HOST", "localhost"),
        inverter_port=int(os.getenv("INVERTER_PORT", "5020")),
        powermeter_host=os.getenv("POWERMETER_HOST", "localhost"),
        powermeter_port=int(os.getenv("POWERMETER_PORT", "5021")),
        poll_interval=float(os.getenv("POLL_INTERVAL", "2.0")),
        publisher=MQTTPublisher(
            broker_host=os.getenv("MQTT_HOST", "localhost"),
            broker_port=int(os.getenv("MQTT_PORT", "1883")),
            device_id=os.getenv("DEVICE_ID", "energy_iot_001"),
            buffer_file=os.getenv("BUFFER_FILE", "iot_buffer.json")
        )
    )

    if device.connect():
        print("IoT Device connected. Polling every 2 seconds.")
        print("Make sure both simulators are running first.")
        try:
            device.run()
        except KeyboardInterrupt:
            device.stop()
    else:
        print("Connection failed — are both simulators running?")
