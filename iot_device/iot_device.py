# iot_device/iot_device.py

import time
import logging
import os
import threading
from dataclasses import asdict
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
from iot_device.energy_accounting import EnergyAccumulator, PowerSample
from iot_device.battery_control import BatteryController
from iot_device.self_consumption_controller import SelfConsumptionController

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
        publisher: MeasurementPublisher | None = None,
        energy_accumulator: EnergyAccumulator | None = None,
        device_id: str = "energy_iot_001",
        battery_control_state_file: str | None = None,
        controller_mode: str = "manual",
        controller_state_file: str | None = None,
        controller_max_power_w: float = 3000.0,
        controller_stale_after_seconds: float = 15.0,
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
        self.energy_accumulator = energy_accumulator
        self.device_id = device_id
        self._modbus_lock = threading.RLock()
        self.battery_controller = BatteryController(
            self.inverter_client,
            device_id,
            state_file=battery_control_state_file,
            modbus_lock=self._modbus_lock,
        )
        if hasattr(self.publisher, "set_battery_command_handler"):
            self.publisher.set_battery_command_handler(
                self.battery_controller.handle
            )
        self.self_consumption_controller = SelfConsumptionController(
            device_id=device_id,
            command_handler=self.battery_controller.handle,
            mode=controller_mode,
            max_power_w=controller_max_power_w,
            stale_after_seconds=controller_stale_after_seconds,
            state_file=controller_state_file,
        )

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
        with self._modbus_lock:
            inverter_data = self.inverter_client.read_measurements()

        if inverter_data is None:
            self.self_consumption_controller.fail_safe(
                "inverter_measurement_unavailable"
            )
            self._raise_fault("inverter_read_failed")
            self._publish_active_faults()
            return None
        self._clear_fault("inverter_read_failed")

        # Read Power Meter second
        # NOTE: There is a timing gap between these two reads.
        # This is the timing gap risk from our architecture analysis.
        pm_data = self.powermeter_client.read_grid_power()

        if pm_data is None:
            self.self_consumption_controller.fail_safe(
                "powermeter_measurement_unavailable"
            )
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
        battery_telemetry = self.battery_controller.telemetry()
        if battery_telemetry:
            measurement.battery_state = battery_telemetry.to_payload()

        # Validate — catch silent failures before cloud
        if not measurement.is_valid:
            self.self_consumption_controller.fail_safe(
                "invalid_measurement"
            )
            self._raise_fault("invalid_measurement")
            logger.error(
                f"[IoT] Invalid measurement detected: {measurement}"
            )
            self._publish_active_faults()
            return None
        self._clear_fault("invalid_measurement")

        controller_decision = self.self_consumption_controller.evaluate(
            measurement
        )
        measurement.controller_state = asdict(controller_decision)

        if self.energy_accumulator:
            accounting = self.energy_accumulator.process(
                PowerSample(
                    source=self.device_id,
                    timestamp=measurement.inverter_timestamp,
                    pv_w=measurement.pv_production,
                    house_w=measurement.house_consumption,
                    grid_w=measurement.grid_power,
                    battery_w=measurement.battery_power,
                )
            )
            measurement.accounting_status = accounting.status
            measurement.daily_energy = asdict(accounting.totals)
            measurement.daily_energy_flows = asdict(accounting.flows)
            measurement.battery_provenance = asdict(
                accounting.battery_provenance
            )
            measurement.daily_kpis = asdict(accounting.kpis)
            measurement.completed_day = accounting.completed_day

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

    device_id = os.getenv("DEVICE_ID", "energy_iot_001")
    poll_interval = float(os.getenv("POLL_INTERVAL", "5.0"))
    device = IoTDevice(
        inverter_host=os.getenv("INVERTER_HOST", "localhost"),
        inverter_port=int(os.getenv("INVERTER_PORT", "5020")),
        powermeter_host=os.getenv("POWERMETER_HOST", "localhost"),
        powermeter_port=int(os.getenv("POWERMETER_PORT", "5021")),
        poll_interval=poll_interval,
        device_id=device_id,
        energy_accumulator=EnergyAccumulator(
            state_file=os.getenv(
                "ENERGY_STATE_FILE", "energy_accounting.json"
            ),
            timezone_name=os.getenv("ENERGY_TIMEZONE", "Europe/Berlin"),
        ),
        battery_control_state_file=os.getenv(
            "BATTERY_CONTROL_STATE_FILE", "battery_control.json"
        ),
        controller_mode=os.getenv("EMS_MODE", "manual"),
        controller_state_file=os.getenv(
            "CONTROLLER_STATE_FILE", "self_consumption_controller.json"
        ),
        controller_max_power_w=float(
            os.getenv("CONTROLLER_MAX_POWER_W", "3000")
        ),
        controller_stale_after_seconds=float(
            os.getenv("CONTROLLER_STALE_AFTER_SECONDS", "15")
        ),
        publisher=MQTTPublisher(
            broker_host=os.getenv("MQTT_HOST", "localhost"),
            broker_port=int(os.getenv("MQTT_PORT", "1883")),
            device_id=device_id,
            buffer_file=os.getenv("BUFFER_FILE", "iot_buffer.json")
        )
    )

    if device.connect():
        print(f"IoT Device connected. Polling every {poll_interval:g} seconds.")
        print("Make sure both simulators are running first.")
        try:
            device.run()
        except KeyboardInterrupt:
            device.stop()
    else:
        print("Connection failed — are both simulators running?")
