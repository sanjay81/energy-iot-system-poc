from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from iot_device.energy_accounting import EnergyAccumulator, PowerSample
from simulations.baseline_profile import (
    EXPECTED_READING_COUNT,
    baseline_readings,
)


def test_baseline_has_289_deterministic_five_minute_readings():
    first = baseline_readings()
    second = baseline_readings()

    assert first == second
    assert len(first) == EXPECTED_READING_COUNT == 289
    assert all(
        current.timestamp - previous.timestamp == 300
        for previous, current in zip(first, first[1:])
    )
    assert first[0].local_time == "2026-06-15T00:00:00+02:00"
    assert first[-1].local_time == "2026-06-16T00:00:00+02:00"


def test_baseline_contains_expected_energy_states():
    readings = baseline_readings()
    midnight = readings[0]
    midday = readings[12 * 12]
    evening = readings[19 * 12]

    assert midnight.pv_w == 0
    assert midnight.grid_w > 0
    assert midday.pv_w > midday.house_w
    assert midday.grid_w < 0
    assert evening.house_w > midnight.house_w
    assert all(reading.battery_w == 0 for reading in readings)


def test_profile_produces_reproducible_balanced_daily_accounting(tmp_path):
    readings = baseline_readings()

    def execute(path):
        accounting = EnergyAccumulator(
            state_file=str(path), max_gap_seconds=300
        )
        result = None
        for reading in readings:
            result = accounting.process(
                PowerSample(
                    source="baseline_no_control_v1",
                    timestamp=reading.timestamp,
                    pv_w=reading.pv_w,
                    house_w=reading.house_w,
                    grid_w=reading.grid_w,
                    battery_w=0,
                )
            )
        return result

    first = execute(tmp_path / "first.json")
    second = execute(tmp_path / "second.json")

    assert first == second
    completed = first.completed_day
    assert completed is not None
    assert completed["totals"]["local_date"] == "2026-06-15"
    assert completed["totals"]["integrated_intervals"] == 288
    assert completed["totals"]["stale_intervals"] == 0
    assert completed["kpis"]["battery_throughput_wh"] == 0
    assert completed["kpis"]["energy_balance_error_percent"] == pytest.approx(
        0, abs=0.001
    )
    assert 0 <= completed["kpis"]["self_consumption_percent"] <= 100
    assert 0 <= completed["kpis"]["self_sufficiency_percent"] <= 100
