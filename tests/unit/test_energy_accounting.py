from datetime import datetime, timezone

import pytest

from iot_device.energy_accounting import EnergyAccumulator, PowerSample


def timestamp(value: str) -> float:
    return datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()


def sample(at: float, **values) -> PowerSample:
    defaults = {
        "pv_w": 0.0,
        "house_w": 0.0,
        "grid_w": 0.0,
        "battery_w": 0.0,
    }
    defaults.update(values)
    return PowerSample(source="device-1", timestamp=at, **defaults)


def accumulator(tmp_path) -> EnergyAccumulator:
    return EnergyAccumulator(state_file=str(tmp_path / "energy.json"))


def test_constant_power_is_integrated_between_event_timestamps(tmp_path):
    accounting = accumulator(tmp_path)
    accounting.process(sample(1000, pv_w=3600, house_w=1800))
    result = accounting.process(sample(1005, pv_w=3600, house_w=1800))

    assert result.status == "integrated"
    assert result.totals.pv_generation_wh == pytest.approx(5.0, rel=0.001)
    assert result.totals.house_consumption_wh == pytest.approx(2.5, rel=0.001)


def test_trapezoidal_integration_handles_changing_power(tmp_path):
    accounting = accumulator(tmp_path)
    accounting.process(sample(1000, pv_w=0))
    result = accounting.process(sample(1010, pv_w=3600))

    assert result.totals.pv_generation_wh == pytest.approx(5.0, rel=0.001)


def test_grid_and_battery_directions_are_accumulated_separately(tmp_path):
    accounting = accumulator(tmp_path)
    accounting.process(sample(1000, grid_w=3600, battery_w=-1800))
    imported = accounting.process(sample(1010, grid_w=3600, battery_w=-1800))
    exported = accounting.process(sample(1020, grid_w=-3600, battery_w=1800))

    assert imported.totals.grid_import_wh == pytest.approx(10.0, rel=0.001)
    assert imported.totals.battery_charge_wh == pytest.approx(5.0, rel=0.001)
    assert exported.totals.grid_export_wh > 0
    assert exported.totals.battery_discharge_wh > 0


def test_gap_over_sixty_seconds_is_not_integrated(tmp_path):
    accounting = accumulator(tmp_path)
    accounting.process(sample(1000, pv_w=3600))
    result = accounting.process(sample(1061, pv_w=3600))

    assert result.status == "stale"
    assert result.totals.pv_generation_wh == 0
    assert result.totals.stale_intervals == 1


def test_exact_duplicate_and_out_of_order_readings_do_not_change_energy(tmp_path):
    accounting = accumulator(tmp_path)
    accounting.process(sample(1000, pv_w=3600))
    accounting.process(sample(1005, pv_w=3600))

    duplicate = accounting.process(sample(1005, pv_w=9999))
    historical = accounting.process(sample(1002, pv_w=9999))

    assert duplicate.status == "duplicate"
    assert historical.status == "out_of_order"
    assert historical.totals.pv_generation_wh == pytest.approx(5.0, rel=0.001)
    assert historical.totals.duplicate_readings == 1
    assert historical.totals.out_of_order_readings == 1


def test_state_survives_restart(tmp_path):
    path = tmp_path / "energy.json"
    first = EnergyAccumulator(state_file=str(path))
    first.process(sample(1000, house_w=3600))
    first.process(sample(1005, house_w=3600))

    restarted = EnergyAccumulator(state_file=str(path))
    result = restarted.process(sample(1010, house_w=3600))

    assert result.totals.house_consumption_wh == pytest.approx(10.0, rel=0.001)
    assert result.totals.integrated_intervals == 2


def test_daily_rollover_uses_europe_berlin_midnight(tmp_path):
    accounting = accumulator(tmp_path)
    before = timestamp("2026-01-01T22:59:55+00:00")  # 23:59:55 in Berlin
    after = timestamp("2026-01-01T23:00:05+00:00")   # 00:00:05 in Berlin

    accounting.process(sample(before, house_w=3600))
    result = accounting.process(sample(after, house_w=3600))

    assert result.totals.local_date == "2026-01-02"
    assert result.totals.house_consumption_wh == pytest.approx(5.0, rel=0.001)
    assert result.totals.integrated_intervals == 1
