"""Frozen Step 6 experiment configuration. Change only by versioning."""

from dataclasses import asdict, dataclass

EXPERIMENT_VERSION = "baseline_vs_smart_v1"
CONTROLLER_VERSION = "step5_self_consumption_v1"


@dataclass(frozen=True)
class FrozenComparisonConfig:
    initial_soc_percent: float = 50.0
    usable_capacity_kwh: float = 10.0
    min_soc_percent: float = 10.0
    max_soc_percent: float = 90.0
    max_charge_power_w: float = 3000.0
    max_discharge_power_w: float = 3000.0
    controller_max_power_w: float = 3000.0
    controller_deadband_w: float = 50.0
    controller_stale_after_seconds: float = 15.0
    battery_efficiency_percent: float | None = None
    sample_interval_seconds: int = 300
    accounting_max_gap_seconds: float = 300.0

    def to_dict(self) -> dict:
        return asdict(self)


FROZEN_CONFIG = FrozenComparisonConfig()
