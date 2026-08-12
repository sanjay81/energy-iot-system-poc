"""Create the versioned Step 3 baseline report."""

from __future__ import annotations

import json
from pathlib import Path

from iot_device.calculator import EnergyMeasurement
from simulations.baseline_profile import (
    INTERVAL_MINUTES,
    SCENARIO_DATE,
    SCENARIO_NAME,
    SCENARIO_TIMEZONE,
)

REPORT_SCHEMA_VERSION = 1


def build_report(
    measurements: list[EnergyMeasurement],
    mqtt_message_count: int,
    max_gap_seconds: float,
) -> dict:
    if not measurements:
        raise ValueError("Cannot build a baseline report without measurements")
    final = measurements[-1]
    if not final.completed_day:
        raise ValueError("Baseline did not close a complete local day")
    totals = final.completed_day["totals"]
    kpis = final.completed_day["kpis"]

    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "scenario": SCENARIO_NAME,
        "scenario_date": SCENARIO_DATE.isoformat(),
        "timezone": SCENARIO_TIMEZONE,
        "start_timestamp_utc": measurements[0].inverter_timestamp,
        "end_timestamp_utc": final.inverter_timestamp,
        "duration_hours": 24,
        "sample_interval_minutes": INTERVAL_MINUTES,
        "accounting_max_gap_seconds": max_gap_seconds,
        "sample_count": len(measurements),
        "mqtt_message_count": mqtt_message_count,
        "smart_control_enabled": False,
        "energy_kwh": {
            "pv_generation": totals.get("pv_generation_wh", 0) / 1000,
            "house_consumption": totals.get("house_consumption_wh", 0) / 1000,
            "grid_import": totals.get("grid_import_wh", 0) / 1000,
            "grid_export": totals.get("grid_export_wh", 0) / 1000,
            "battery_charge": totals.get("battery_charge_wh", 0) / 1000,
            "battery_discharge": totals.get("battery_discharge_wh", 0) / 1000,
        },
        "kpis": {
            "self_consumption_percent": kpis.get("self_consumption_percent"),
            "self_sufficiency_percent": kpis.get("self_sufficiency_percent"),
            "peak_grid_demand_kw": kpis.get("peak_grid_demand_w", 0) / 1000,
            "net_grid_energy_kwh": kpis.get("net_grid_energy_wh", 0) / 1000,
            "battery_throughput_kwh": (
                kpis.get("battery_throughput_wh", 0) / 1000
            ),
            "energy_balance_error_kwh": (
                kpis.get("energy_balance_error_wh", 0) / 1000
            ),
            "energy_balance_error_percent": kpis.get(
                "energy_balance_error_percent", 0
            ),
        },
        "data_quality": {
            "integrated_intervals": totals.get("integrated_intervals", 0),
            "stale_intervals": totals.get("stale_intervals", 0),
            "duplicate_readings": totals.get("duplicate_readings", 0),
            "out_of_order_readings": totals.get("out_of_order_readings", 0),
            "energy_balance_pass": abs(
                kpis.get("energy_balance_error_percent", 0)
            ) < 1.0,
        },
    }
    return _rounded(report)


def write_report(report: dict, output: str | Path) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return path


def render_summary(report: dict) -> str:
    energy = report["energy_kwh"]
    kpis = report["kpis"]
    return "\n".join(
        [
            f"Scenario: {report['scenario']}",
            f"Samples: {report['sample_count']} at "
            f"{report['sample_interval_minutes']}-minute intervals",
            f"PV generation: {energy['pv_generation']:.3f} kWh",
            f"House consumption: {energy['house_consumption']:.3f} kWh",
            f"Grid import/export: {energy['grid_import']:.3f} / "
            f"{energy['grid_export']:.3f} kWh",
            f"Self-consumption: {kpis['self_consumption_percent']:.2f}%",
            f"Self-sufficiency: {kpis['self_sufficiency_percent']:.2f}%",
            f"Peak grid demand: {kpis['peak_grid_demand_kw']:.3f} kW",
            f"Energy balance error: "
            f"{kpis['energy_balance_error_percent']:.4f}%",
        ]
    )


def _rounded(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value
