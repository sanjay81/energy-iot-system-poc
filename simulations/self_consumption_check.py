"""Docker smoke test for the Step 5 automatic decision path."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

import paho.mqtt.client as mqtt

from iot_device.mqtt_publisher import TOPIC_MEASUREMENTS

DEVICE_ID = os.getenv("DEVICE_ID", "energy_iot_001")


def main():
    received = []
    connected = threading.Event()
    client = mqtt.Client(client_id=f"step5-check-{uuid.uuid4()}")

    def on_connect(mqtt_client, userdata, flags, rc):
        if rc == 0:
            mqtt_client.subscribe(TOPIC_MEASUREMENTS, qos=1)
            connected.set()

    def on_message(mqtt_client, userdata, message):
        payload = json.loads(message.payload.decode())
        if payload.get("device_id") == DEVICE_ID:
            received.append(payload)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(os.getenv("MQTT_HOST", "localhost"), 1883, 60)
    client.loop_start()
    try:
        if not connected.wait(5):
            raise SystemExit("Step 5 check could not connect to MQTT")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            decisions = [item.get("controller_state", {}) for item in received]
            commanded = [
                item for item in decisions
                if item.get("status") == "commanded"
                and item.get("command_status") == "accepted"
            ]
            if commanded:
                decision = commanded[-1]
                if decision["reason"] == "pv_surplus" and decision["desired_power_w"] >= 0:
                    raise SystemExit("PV surplus did not request charging")
                if decision["reason"] == "household_deficit" and decision["desired_power_w"] <= 0:
                    raise SystemExit("household deficit did not request discharge")
                print("Step 5 automatic self-consumption check passed")
                return
            time.sleep(0.05)
        raise SystemExit("No accepted automatic controller command observed")
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
