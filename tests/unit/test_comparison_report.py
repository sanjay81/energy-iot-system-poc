import json
from pathlib import Path

from simulations.comparison_config import FROZEN_CONFIG
from simulations.smart_comparison import build_comparison


def case(grid_import, grid_export, self_consumption, self_sufficiency, peak):
    return {
        "sample_count": 289,
        "start_timestamp_utc": 1,
        "end_timestamp_utc": 2,
        "ending_soc_percent": 50,
        "energy_kwh": {
            "grid_import": grid_import,
            "grid_export": grid_export,
        },
        "kpis": {
            "self_consumption_percent": self_consumption,
            "self_sufficiency_percent": self_sufficiency,
            "peak_grid_demand_kw": peak,
        },
    }


def test_comparison_uses_standard_improvement_formulas():
    report = build_comparison(
        case(10, 20, 30, 40, 2),
        case(5, 10, 60, 70, 1),
    )

    assert report["improvement"]["grid_import_reduction_percent"] == 50
    assert report["improvement"]["grid_export_reduction_percent"] == 50
    assert report["improvement"]["self_consumption_increase_percentage_points"] == 30
    assert report["improvement"]["self_sufficiency_increase_percentage_points"] == 30
    assert report["improvement"]["peak_grid_demand_reduction_percent"] == 50


def test_frozen_configuration_records_fairness_inputs():
    config = FROZEN_CONFIG.to_dict()

    assert config["initial_soc_percent"] == 50
    assert config["usable_capacity_kwh"] == 10
    assert config["controller_max_power_w"] == 3000
    assert config["controller_deadband_w"] == 50
    assert config["battery_efficiency_percent"] is None


def test_golden_comparison_preserves_step3_and_reports_end_soc():
    root = Path(__file__).parents[2]
    comparison = json.loads(
        (root / "simulations/golden/comparison_report.json").read_text()
    )
    step3 = json.loads(
        (root / "simulations/golden/baseline_report.json").read_text()
    )
    baseline = dict(comparison["baseline"])
    baseline.pop("ending_soc_percent")
    baseline.pop("commands")

    assert baseline == step3
    assert comparison["fairness"]["identical_profile"]
    assert not comparison["fairness"]["policy_tuned_during_comparison"]
    assert comparison["smart"]["ending_soc_percent"] == 27.5
    assert comparison["improvement"]["smart_battery_stored_energy_change_kwh"] == -2.25
    assert comparison["improvement"]["soc_adjusted_grid_import_reduction_percent"] < comparison["improvement"]["grid_import_reduction_percent"]
