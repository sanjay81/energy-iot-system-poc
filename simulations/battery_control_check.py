"""Docker/CI smoke test for MQTT -> gateway -> Modbus battery control."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

import paho.mqtt.client as mqtt

from iot_device.mqtt_publisher import (
    TOPIC_MEASUREMENTS,
    battery_ack_topic,
    battery_command_topic,
)

DEVICE_ID = os.getenv("DEVICE_ID", "energy_iot_001")


class Observer:
    def __init__(self):
        self.acks: dict[str, dict] = {}
        self.measurements: list[dict] = []
        self.connected = threading.Event()
        self.client = mqtt.Client(client_id=f"battery-check-{uuid.uuid4()}")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(battery_ack_topic(DEVICE_ID), qos=1)
            client.subscribe(TOPIC_MEASUREMENTS, qos=1)
            self.connected.set()

    def _on_message(self, client, userdata, message):
        payload = json.loads(message.payload.decode())
        if message.topic == battery_ack_topic(DEVICE_ID):
            self.acks[payload["command_id"]] = payload
        elif payload.get("device_id") == DEVICE_ID:
            self.measurements.append(payload)


def main():
    host = os.getenv("MQTT_HOST", "localhost")
    observer = Observer()
    observer.client.connect(host, int(os.getenv("MQTT_PORT", "1883")), 60)
    observer.client.loop_start()
    try:
        if not observer.connected.wait(5):
            raise SystemExit("battery check could not connect to MQTT")
        accepted = _command(observer, -1200)
        if accepted["status"] != "accepted":
            raise SystemExit(f"safe battery command rejected: {accepted}")
        _wait_until(
            lambda: any(
                item.get("battery_state", {}).get("actual_power_w") == -1200
                for item in observer.measurements
            ),
            "actual charge power telemetry",
            timeout=10,
        )
        rejected = _command(observer, 5000)
        if rejected.get("rejection_reason") != "discharge_power_limit_exceeded":
            raise SystemExit(f"unsafe command was not rejected: {rejected}")
        stopped = _command(observer, 0)
        if stopped["status"] != "accepted":
            raise SystemExit(f"idle command rejected: {stopped}")
        print("Battery control MQTT/Modbus check passed")
    finally:
        observer.client.disconnect()
        observer.client.loop_stop()


def _command(observer: Observer, requested_power_w: float) -> dict:
    command_id = str(uuid.uuid4())
    now = time.time()
    payload = {
        "schema_version": 1,
        "command_id": command_id,
        "device_id": DEVICE_ID,
        "requested_power_w": requested_power_w,
        "issued_at": now,
        "expires_at": now + 30,
    }
    observer.client.publish(
        battery_command_topic(DEVICE_ID), json.dumps(payload), qos=1
    )
    _wait_until(lambda: command_id in observer.acks, "command acknowledgement")
    return observer.acks[command_id]


def _wait_until(predicate, description: str, timeout: float = 5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise SystemExit(f"timed out waiting for {description}")


if __name__ == "__main__":
    main()
