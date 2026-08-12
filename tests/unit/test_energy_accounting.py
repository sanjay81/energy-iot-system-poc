from datetime import datetime, timezone

import pytest

from iot_device.energy_accounting import (
    BatteryProvenance,
    EnergyAccumulator,
    PowerSample,
)


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


def test_self_consumption_counts_direct_pv_and_pv_battery_charge(tmp_path):
    accounting = accumulator(tmp_path)
    accounting.process(
        sample(1000, pv_w=3000, house_w=2000, grid_w=-500, battery_w=-500)
    )
    result = accounting.process(
        sample(1060, pv_w=3000, house_w=2000, grid_w=-500, battery_w=-500)
    )

    assert result.flows.pv_direct_to_house_wh == pytest.approx(
        2000 / 60, rel=0.001
    )
    assert result.flows.pv_to_battery_wh == pytest.approx(
        500 / 60, rel=0.001
    )
    assert result.kpis.self_consumption_percent == pytest.approx(
        83.333333, rel=0.001
    )
    assert result.kpis.self_sufficiency_percent == pytest.approx(
        100.0, rel=0.001
    )


def test_mixed_battery_discharge_uses_proportional_provenance(tmp_path):
    accounting = accumulator(tmp_path)
    accounting.battery_provenance = BatteryProvenance(
        pv_origin_wh=70,
        grid_origin_wh=20,
        unknown_origin_wh=10,
    )
    accounting.process(sample(1000, house_w=3600, battery_w=3600))
    result = accounting.process(sample(1060, house_w=3600, battery_w=3600))

    assert result.flows.pv_battery_to_house_wh == pytest.approx(42, rel=0.001)
    assert result.flows.grid_battery_to_house_wh == pytest.approx(12, rel=0.001)
    assert result.flows.unknown_battery_to_house_wh == pytest.approx(6, rel=0.001)
    assert result.kpis.self_sufficiency_percent == pytest.approx(70, rel=0.001)
    assert result.battery_provenance.pv_origin_wh == pytest.approx(28, rel=0.001)


def test_unknown_battery_discharge_does_not_inflate_self_sufficiency(tmp_path):
    accounting = accumulator(tmp_path)
    accounting.process(sample(1000, house_w=1800, battery_w=1800))
    result = accounting.process(sample(1060, house_w=1800, battery_w=1800))

    assert result.flows.unknown_battery_to_house_wh == pytest.approx(
        30, rel=0.001
    )
    assert result.kpis.self_sufficiency_percent == 0


def test_grid_charged_battery_is_not_local_supply(tmp_path):
    accounting = accumulator(tmp_path)
    accounting.process(
        sample(1000, house_w=1000, grid_w=2000, battery_w=-1000)
    )
    charged = accounting.process(
        sample(1060, house_w=1000, grid_w=2000, battery_w=-1000)
    )

    assert charged.battery_provenance.grid_origin_wh == pytest.approx(
        1000 / 60, rel=0.001
    )
    assert charged.battery_provenance.pv_origin_wh == 0


def test_battery_sign_change_is_split_at_zero_crossing(tmp_path):
    accounting = accumulator(tmp_path)
    accounting.process(
        sample(1000, pv_w=2000, house_w=1000, battery_w=-1000)
    )
    result = accounting.process(
        sample(1060, pv_w=0, house_w=1000, battery_w=1000)
    )

    # Each triangular half lasts 30 seconds: 0.5 * 1000 W * 30/3600 h.
    expected_each_direction_wh = 1000 / 240
    assert result.totals.battery_charge_wh == pytest.approx(
        expected_each_direction_wh, rel=0.001
    )
    assert result.totals.battery_discharge_wh == pytest.approx(
        expected_each_direction_wh, rel=0.001
    )
    assert result.flows.pv_to_battery_wh == pytest.approx(
        expected_each_direction_wh, rel=0.001
    )
    assert result.totals.integrated_intervals == 1


def test_zero_denominators_have_explicit_not_applicable_status(tmp_path):
    accounting = accumulator(tmp_path)
    result = accounting.process(sample(1000))

    assert result.kpis.self_consumption_percent is None
    assert result.kpis.self_consumption_status == "not_applicable:no_pv_generation"
    assert result.kpis.self_sufficiency_percent is None
    assert result.kpis.self_sufficiency_status == (
        "not_applicable:no_house_consumption"
    )
    assert result.kpis.energy_balance_error_percent == 0


def test_peak_net_grid_throughput_and_balance_error(tmp_path):
    accounting = accumulator(tmp_path)
    accounting.process(
        sample(1000, pv_w=1000, house_w=2000, grid_w=1500, battery_w=-500)
    )
    result = accounting.process(
        sample(1060, pv_w=1000, house_w=2000, grid_w=1500, battery_w=-500)
    )

    assert result.kpis.peak_grid_demand_w == 1500
    assert result.kpis.net_grid_energy_wh == pytest.approx(25, rel=0.001)
    assert result.kpis.battery_throughput_wh == pytest.approx(
        500 / 60, rel=0.001
    )
    assert result.kpis.energy_balance_error_wh == pytest.approx(0, abs=1e-9)
    assert result.kpis.energy_balance_error_percent == pytest.approx(
        0, abs=1e-9
    )


def test_provenance_persists_across_restart_and_daily_rollover(tmp_path):
    path = tmp_path / "energy.json"
    first = EnergyAccumulator(state_file=str(path))
    first.battery_provenance = BatteryProvenance(pv_origin_wh=100)
    before = timestamp("2026-01-01T22:59:55+00:00")
    after = timestamp("2026-01-01T23:00:05+00:00")
    first.process(sample(before))
    first.process(sample(after))

    restarted = EnergyAccumulator(state_file=str(path))

    assert restarted.totals.local_date == "2026-01-02"
    assert restarted.battery_provenance.pv_origin_wh == 100


def test_yesterdays_pv_battery_energy_counts_for_todays_self_sufficiency(
    tmp_path,
):
    accounting = accumulator(tmp_path)
    accounting.battery_provenance = BatteryProvenance(pv_origin_wh=60)
    start = timestamp("2026-01-02T00:00:00+01:00")
    accounting.process(sample(start, house_w=3600, battery_w=3600))
    result = accounting.process(
        sample(start + 60, house_w=3600, battery_w=3600)
    )

    assert result.flows.pv_battery_to_house_wh == pytest.approx(60, rel=0.001)
    assert result.kpis.self_sufficiency_percent == pytest.approx(100, rel=0.001)
    assert result.kpis.self_consumption_percent is None


def test_energy_balance_error_exposes_inconsistent_measurements(tmp_path):
    accounting = accumulator(tmp_path)
    accounting.process(sample(1000, pv_w=1000, house_w=500, grid_w=1000))
    result = accounting.process(
        sample(1060, pv_w=1000, house_w=500, grid_w=1000)
    )

    assert result.kpis.energy_balance_error_wh == pytest.approx(25, rel=0.001)
    assert result.kpis.energy_balance_error_percent == pytest.approx(
        75, rel=0.001
    )


def test_step_one_state_file_migrates_with_empty_kpi_state(tmp_path):
    path = tmp_path / "energy.json"
    path.write_text(
        '{"totals":{"local_date":"2026-08-12",'
        '"timezone":"Europe/Berlin","pv_generation_wh":1, '
        '"house_consumption_wh":0,"grid_import_wh":0,"grid_export_wh":0,'
        '"battery_charge_wh":0,"battery_discharge_wh":0,'
        '"integrated_intervals":1,"stale_intervals":0,'
        '"duplicate_readings":0,"out_of_order_readings":0},'
        '"latest":null}'
    )

    accounting = EnergyAccumulator(state_file=str(path))
    result = accounting.process(sample(timestamp("2026-08-12T10:00:00+00:00")))

    assert result.flows.pv_direct_to_house_wh == 0
    assert result.battery_provenance.pv_origin_wh == 0
    assert result.totals.pv_generation_wh == 0
