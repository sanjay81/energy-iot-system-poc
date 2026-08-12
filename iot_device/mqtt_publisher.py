# iot_device/mqtt_publisher.py

import json
import time
import logging
import threading
import paho.mqtt.client as mqtt
from iot_device.buffer import LocalBuffer
from iot_device.calculator import EnergyMeasurement

logger = logging.getLogger(__name__)

# MQTT Topics
TOPIC_MEASUREMENTS = "energy-iot/measurements"
TOPIC_STATUS       = "energy-iot/status"
TOPIC_FAULTS       = "energy-iot/faults"

# QoS level — at least once delivery
QOS = 1

# Reconnect settings
RECONNECT_DELAY_MIN = 1    # seconds
RECONNECT_DELAY_MAX = 60   # seconds


class MQTTPublisher:
    """
    Publishes energy measurements to MQTT broker.

    Handles:
    - Connection and reconnection
    - QoS 1 delivery guarantee
    - Local buffering during outage
    - Buffer upload on reconnect
    - Back-pressure from broker
    """

    def __init__(
        self,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        device_id: str = "energy_iot_001",
        buffer_file: str = "iot_buffer.json"
    ):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.device_id = device_id
        self.buffer = LocalBuffer(buffer_file=buffer_file)
        self._connected = False
        self._reconnect_delay = RECONNECT_DELAY_MIN
        self._lock = threading.Lock()
        self._pending_replay_mid = None

        # Setup MQTT client
        self.client = mqtt.Client(
            client_id=device_id,
            clean_session=False  # persist session across reconnects
        )

        # Last Will — published by broker if IoT disconnects unexpectedly
        self.client.will_set(
            topic=TOPIC_STATUS,
            payload=json.dumps({
                "device_id": device_id,
                "status": "offline",
                "timestamp": time.time()
            }),
            qos=QOS,
            retain=True
        )

        # Callbacks
        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish    = self._on_publish

    def _on_connect(self, client, userdata, flags, rc):
        """Called when connected to broker."""
        if rc == 0:
            self._connected = True
            self._reconnect_delay = RECONNECT_DELAY_MIN
            logger.info(
                f"[MQTT] Connected to broker "
                f"{self.broker_host}:{self.broker_port}"
            )

            # Publish online status
            self.client.publish(
                TOPIC_STATUS,
                json.dumps({
                    "device_id": self.device_id,
                    "status": "online",
                    "timestamp": time.time()
                }),
                qos=QOS,
                retain=True
            )

            # Upload any buffered measurements
            self._upload_buffer()

        else:
            logger.error(f"[MQTT] Connection failed — code {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """Called when disconnected from broker."""
        self._connected = False
        if rc != 0:
            logger.warning(
                f"[MQTT] Unexpected disconnect — "
                f"will retry in {self._reconnect_delay}s"
            )

    def _on_publish(self, client, userdata, mid):
        """Remove one replayed record only after its QoS acknowledgement."""
        if mid == self._pending_replay_mid:
            self.buffer.remove_uploaded(1)
            self._pending_replay_mid = None
            self._upload_buffer()
        logger.debug(f"[MQTT] Message {mid} published")

    def _upload_buffer(self):
        """
        Upload the oldest buffered measurement. The next record is sent only
        after ``on_publish`` confirms this QoS 1 delivery.
        """
        if self.buffer.is_empty or self._pending_replay_mid is not None:
            return

        buffered = self.buffer.get_all()
        result = self.client.publish(
            TOPIC_MEASUREMENTS,
            json.dumps(buffered[0]),
            qos=QOS
        )
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            self._pending_replay_mid = result.mid
            logger.info(
                f"[MQTT] Replaying buffered measurement "
                f"({len(buffered)} remaining)"
            )
        else:
            logger.error("[MQTT] Could not enqueue buffered measurement")

    def connect(self):
        """Connect to broker and start network loop."""
        try:
            self.client.connect(
                self.broker_host,
                self.broker_port,
                keepalive=60
            )
            self.client.loop_start()
        except Exception as e:
            logger.error(f"[MQTT] Cannot connect: {e}")

    def disconnect(self):
        """Disconnect cleanly."""
        if self._connected:
            self.client.disconnect()
        self.client.loop_stop()
        self._connected = False

    def publish_measurement(
        self,
        measurement: EnergyMeasurement
    ) -> bool:
        """
        Publish a measurement to the broker.

        If connected → publish directly via MQTT
        If not connected → buffer locally

        Returns True if published or buffered successfully.
        """
        payload = {
            "device_id":        self.device_id,
            "timestamp":        measurement.inverter_timestamp,
            "pv_production_w":  round(measurement.pv_production, 1),
            "ac_output_w":      round(measurement.ac_output, 1),
            "battery_power_w":  round(measurement.battery_power, 1),
            "grid_power_w":     round(measurement.grid_power, 1),
            "house_consumption_w": round(
                measurement.house_consumption, 1
            ),
            "timestamp_delta_ms": round(
                measurement.timestamp_delta_ms, 1
            ),
            "buffered": False
        }
        if measurement.daily_energy is not None:
            payload["daily_energy"] = measurement.daily_energy
            payload["daily_energy_flows"] = measurement.daily_energy_flows
            payload["battery_provenance"] = measurement.battery_provenance
            payload["daily_kpis"] = measurement.daily_kpis
            if measurement.completed_day is not None:
                payload["completed_day"] = measurement.completed_day
            payload["accounting_status"] = measurement.accounting_status

        with self._lock:
            if self._connected:
                result = self.client.publish(
                    TOPIC_MEASUREMENTS,
                    json.dumps(payload),
                    qos=QOS
                )
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    logger.debug(
                        f"[MQTT] Published — "
                        f"House={measurement.house_consumption:.0f}W"
                    )
                    return True
                else:
                    logger.warning(
                        "[MQTT] Publish failed — buffering"
                    )

            # Not connected or publish failed → buffer
            payload["buffered"] = True
            self.buffer.add(payload)
            logger.info(
                f"[MQTT] Buffered measurement — "
                f"buffer size: {self.buffer.size}"
            )
            return False

    def publish_fault(self, fault_code: str):
        """Publish fault flag to broker."""
        payload = json.dumps({
            "device_id":  self.device_id,
            "fault_code": fault_code,
            "timestamp":  time.time()
        })
        if self._connected:
            self.client.publish(TOPIC_FAULTS, payload, qos=QOS)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def buffer_size(self) -> int:
        return self.buffer.size
