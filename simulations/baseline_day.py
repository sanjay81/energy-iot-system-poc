"""Accelerated full-pipeline Step 3 baseline runner."""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from iot_device.energy_accounting import EnergyAccumulator
from iot_device.iot_device import IoTDevice
from iot_device.mqtt_publisher import MQTTPublisher, TOPIC_MEASUREMENTS
from simulations.baseline_profile import baseline_readings
from simulations.baseline_report import build_report, render_summary, write_report
from simulators.coordinator import SystemCoordinator

BASELINE_MAX_GAP_SECONDS = 300.0
DEVICE_ID = "baseline_no_control_v1"


class MQTTCollector:
    def __init__(self, host: str, port: int, device_id: str = DEVICE_ID):
        self.device_id = device_id
        self.messages: dict[float, dict] = {}
        self.connected = threading.Event()
        self.client = mqtt.Client(client_id=f"{device_id}_collector")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(host, port, keepalive=60)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(TOPIC_MEASUREMENTS, qos=1)
            self.connected.set()

    def _on_message(self, client, userdata, message):
        payload = json.loads(message.payload.decode())
        if payload.get("device_id") == self.device_id:
            self.messages[payload["timestamp"]] = payload

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()


def run_baseline(
    mqtt_host: str = "localhost",
    mqtt_port: int = 1883,
    inverter_port: int = 15020,
    powermeter_port: int = 15021,
    work_directory: str | Path | None = None,
) -> dict:
    readings = baseline_readings()
    workspace = Path(work_directory or tempfile.mkdtemp(prefix="energy-baseline-"))
    workspace.mkdir(parents=True, exist_ok=True)

    coordinator = SystemCoordinator(
        host="127.0.0.1",
        inverter_port=inverter_port,
        powermeter_port=powermeter_port,
    )
    collector = MQTTCollector(mqtt_host, mqtt_port)
    publisher = MQTTPublisher(
        broker_host=mqtt_host,
        broker_port=mqtt_port,
        device_id=DEVICE_ID,
        buffer_file=str(workspace / "mqtt_buffer.json"),
    )
    device = IoTDevice(
        inverter_host="127.0.0.1",
        inverter_port=inverter_port,
        powermeter_host="127.0.0.1",
        powermeter_port=powermeter_port,
        publisher=publisher,
        device_id=DEVICE_ID,
        energy_accumulator=EnergyAccumulator(
            state_file=str(workspace / "accounting.json"),
            max_gap_seconds=BASELINE_MAX_GAP_SECONDS,
        ),
    )

    measurements = []
    coordinator.start_manual()
    _wait_for_port("127.0.0.1", inverter_port)
    _wait_for_port("127.0.0.1", powermeter_port)
    if not collector.connected.wait(5):
        raise RuntimeError("MQTT collector did not connect")
    if not device.connect():
        raise RuntimeError("Baseline gateway could not connect")
    _wait_until(lambda: publisher.is_connected, "MQTT publisher connection")

    try:
        for reading in readings:
            coordinator.inject_timestamped_scenario(
                pv=reading.pv_w,
                house=reading.house_w,
                battery=reading.battery_w,
                timestamp=reading.timestamp,
            )
            measurement = device.poll_once()
            if measurement is None:
                raise RuntimeError(f"Invalid baseline reading: {reading}")
            measurements.append(measurement)

        _wait_until(
            lambda: len(collector.messages) >= len(readings),
            "all baseline MQTT messages",
            timeout=15,
        )
        final_payload = collector.messages[readings[-1].timestamp]
        if final_payload.get("completed_day") != measurements[-1].completed_day:
            raise RuntimeError("Completed-day accounting changed across MQTT")
        return build_report(
            measurements,
            mqtt_message_count=len(collector.messages),
            max_gap_seconds=BASELINE_MAX_GAP_SECONDS,
        )
    finally:
        device.disconnect()
        collector.close()
        coordinator.stop()


def _wait_for_port(host: str, port: int, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.02)
    raise RuntimeError(f"Port {host}:{port} did not become ready")


def _wait_until(predicate, description: str, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise RuntimeError(f"Timed out waiting for {description}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="results/baseline_report.json")
    parser.add_argument("--mqtt-host", default=os.getenv("MQTT_HOST", "localhost"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--verify", help="Golden JSON report to compare exactly")
    args = parser.parse_args()

    if args.headless:
        logging.getLogger().setLevel(logging.WARNING)
    report = run_baseline(args.mqtt_host, args.mqtt_port)
    write_report(report, args.output)
    if args.verify:
        expected = json.loads(Path(args.verify).read_text())
        if report != expected:
            raise SystemExit("Baseline report differs from the golden report")
    if not args.headless:
        print(render_summary(report))
        print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
