"""Step 6 deterministic baseline-versus-smart full-pipeline experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from iot_device.energy_accounting import EnergyAccumulator
from iot_device.iot_device import IoTDevice
from iot_device.mqtt_publisher import MQTTPublisher
from simulations.baseline_day import MQTTCollector, _wait_for_port, _wait_until, run_baseline
from simulations.baseline_profile import SCENARIO_NAME, baseline_readings
from simulations.baseline_report import _rounded, build_report, write_report
from simulations.comparison_config import (
    CONTROLLER_VERSION,
    EXPERIMENT_VERSION,
    FROZEN_CONFIG,
)
from simulators.coordinator import SystemCoordinator

SMART_DEVICE_ID = "smart_self_consumption_v1"
REPORT_SCHEMA_VERSION = 1


def run_smart(
    mqtt_host="localhost",
    mqtt_port=1883,
    inverter_port=15030,
    powermeter_port=15031,
    work_directory=None,
):
    readings = baseline_readings()
    workspace = Path(work_directory or tempfile.mkdtemp(prefix="energy-smart-"))
    workspace.mkdir(parents=True, exist_ok=True)
    config = FROZEN_CONFIG
    battery_config = {
        "soc_percent": config.initial_soc_percent,
        "usable_capacity_kwh": config.usable_capacity_kwh,
        "min_soc_percent": config.min_soc_percent,
        "max_soc_percent": config.max_soc_percent,
        "max_charge_power_w": config.max_charge_power_w,
        "max_discharge_power_w": config.max_discharge_power_w,
    }
    clock = {"now": readings[0].timestamp}
    coordinator = SystemCoordinator(
        host="127.0.0.1",
        inverter_port=inverter_port,
        powermeter_port=powermeter_port,
        battery_state_file=str(workspace / "battery.json"),
        battery_config=battery_config,
    )
    collector = MQTTCollector(mqtt_host, mqtt_port, SMART_DEVICE_ID)
    publisher = MQTTPublisher(
        broker_host=mqtt_host,
        broker_port=mqtt_port,
        device_id=SMART_DEVICE_ID,
        buffer_file=str(workspace / "mqtt_buffer.json"),
    )
    device = IoTDevice(
        inverter_host="127.0.0.1",
        inverter_port=inverter_port,
        powermeter_host="127.0.0.1",
        powermeter_port=powermeter_port,
        publisher=publisher,
        device_id=SMART_DEVICE_ID,
        energy_accumulator=EnergyAccumulator(
            state_file=str(workspace / "accounting.json"),
            max_gap_seconds=config.accounting_max_gap_seconds,
        ),
        battery_control_state_file=str(workspace / "commands.json"),
        controller_mode="automatic",
        controller_state_file=str(workspace / "controller.json"),
        controller_max_power_w=config.controller_max_power_w,
        controller_stale_after_seconds=config.controller_stale_after_seconds,
        controller_deadband_w=config.controller_deadband_w,
        controller_clock=lambda: clock["now"],
    )

    measurements = []
    coordinator.start_manual()
    _wait_for_port("127.0.0.1", inverter_port)
    _wait_for_port("127.0.0.1", powermeter_port)
    if not collector.connected.wait(5):
        raise RuntimeError("Smart-run MQTT collector did not connect")
    if not device.connect():
        raise RuntimeError("Smart-run gateway could not connect")
    _wait_until(lambda: publisher.is_connected, "smart MQTT publisher")
    try:
        for index, reading in enumerate(readings):
            if index:
                coordinator.advance_battery(config.sample_interval_seconds)
            battery = coordinator.battery.snapshot()
            clock["now"] = reading.timestamp
            coordinator.inject_timestamped_scenario(
                pv=reading.pv_w,
                house=reading.house_w,
                battery=battery["actual_power_w"],
                timestamp=reading.timestamp,
            )
            measurement = device.poll_once()
            if measurement is None:
                raise RuntimeError(f"Invalid smart reading: {reading}")
            measurements.append(measurement)

        _wait_until(
            lambda: len(collector.messages) >= len(readings),
            "all smart MQTT messages",
            timeout=15,
        )
        final_payload = collector.messages[readings[-1].timestamp]
        if final_payload.get("completed_day") != measurements[-1].completed_day:
            raise RuntimeError("Smart completed day changed across MQTT")
        report = build_report(
            measurements,
            mqtt_message_count=len(collector.messages),
            max_gap_seconds=config.accounting_max_gap_seconds,
        )
        decisions = [item.controller_state or {} for item in measurements]
        report["scenario"] = "smart_self_consumption_v1"
        report["smart_control_enabled"] = True
        report["ending_soc_percent"] = measurements[-1].battery_state["soc_percent"]
        report["commands"] = {
            "accepted": sum(item.get("command_status") == "accepted" for item in decisions),
            "rejected": sum(item.get("command_status") == "rejected" for item in decisions),
            "emitted": sum(bool(item.get("command_id")) for item in decisions),
        }
        return _rounded(report)
    finally:
        device.disconnect()
        collector.close()
        coordinator.stop()


def build_comparison(baseline: dict, smart: dict) -> dict:
    def reduction(baseline_value, smart_value):
        return (baseline_value - smart_value) / baseline_value * 100

    baseline_case = dict(baseline)
    baseline_case["ending_soc_percent"] = FROZEN_CONFIG.initial_soc_percent
    baseline_case["commands"] = {"accepted": 0, "rejected": 0, "emitted": 0}
    smart_stored_change = (
        smart["ending_soc_percent"] - FROZEN_CONFIG.initial_soc_percent
    ) / 100 * FROZEN_CONFIG.usable_capacity_kwh
    smart_depletion = max(-smart_stored_change, 0.0)
    soc_adjusted_import = smart["energy_kwh"]["grid_import"] + smart_depletion
    profile_payload = [
        [item.timestamp, item.pv_w, item.house_w]
        for item in baseline_readings()
    ]
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "experiment_version": EXPERIMENT_VERSION,
        "source_profile": SCENARIO_NAME,
        "profile_fingerprint_sha256": hashlib.sha256(
            json.dumps(profile_payload, separators=(",", ":")).encode()
        ).hexdigest(),
        "controller_version": CONTROLLER_VERSION,
        "frozen_configuration": FROZEN_CONFIG.to_dict(),
        "fairness": {
            "identical_profile": baseline["sample_count"] == smart["sample_count"]
            and baseline["start_timestamp_utc"] == smart["start_timestamp_utc"]
            and baseline["end_timestamp_utc"] == smart["end_timestamp_utc"],
            "step3_baseline_unchanged": True,
            "uses_actual_battery_power": True,
            "policy_tuned_during_comparison": False,
        },
        "baseline": baseline_case,
        "smart": smart,
        "improvement": {
            "grid_import_reduction_percent": reduction(
                baseline["energy_kwh"]["grid_import"], smart["energy_kwh"]["grid_import"]
            ),
            "grid_export_reduction_percent": reduction(
                baseline["energy_kwh"]["grid_export"], smart["energy_kwh"]["grid_export"]
            ),
            "self_consumption_increase_percentage_points": (
                smart["kpis"]["self_consumption_percent"]
                - baseline["kpis"]["self_consumption_percent"]
            ),
            "self_sufficiency_increase_percentage_points": (
                smart["kpis"]["self_sufficiency_percent"]
                - baseline["kpis"]["self_sufficiency_percent"]
            ),
            "peak_grid_demand_reduction_percent": reduction(
                baseline["kpis"]["peak_grid_demand_kw"],
                smart["kpis"]["peak_grid_demand_kw"],
            ),
            "smart_battery_stored_energy_change_kwh": smart_stored_change,
            "smart_soc_adjusted_grid_import_kwh": soc_adjusted_import,
            "soc_adjusted_grid_import_reduction_percent": reduction(
                baseline["energy_kwh"]["grid_import"], soc_adjusted_import
            ),
        },
    }
    return _rounded(report)


def run_comparison(mqtt_host="localhost", mqtt_port=1883):
    baseline = run_baseline(
        mqtt_host, mqtt_port, inverter_port=15020, powermeter_port=15021
    )
    smart = run_smart(
        mqtt_host, mqtt_port, inverter_port=15030, powermeter_port=15031
    )
    return build_comparison(baseline, smart)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="results/comparison_report.json")
    parser.add_argument("--mqtt-host", default=os.getenv("MQTT_HOST", "localhost"))
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--verify")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    if args.headless:
        logging.getLogger().setLevel(logging.WARNING)
    report = run_comparison(args.mqtt_host, args.mqtt_port)
    write_report(report, args.output)
    if args.verify and report != json.loads(Path(args.verify).read_text()):
        raise SystemExit("Comparison report differs from the golden report")
    if not args.headless:
        print(json.dumps(report["improvement"], indent=2))


if __name__ == "__main__":
    main()
