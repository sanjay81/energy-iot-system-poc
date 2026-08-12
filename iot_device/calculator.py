# iot_device/calculator.py

from dataclasses import dataclass
from typing import Any


@dataclass
class EnergyMeasurement:
    """
    Complete energy measurement at one point in time.
    All values in Watts.
    This is what gets sent to the cloud.
    """
    pv_production: float        # from Inverter register 40001
    ac_output: float            # from Inverter register 40002
    battery_power: float        # positive discharge, negative charge
    grid_power: float           # from Power Meter register 30001
    house_consumption: float    # calculated — never directly measured
    inverter_timestamp: float   # when Inverter was read
    powermeter_timestamp: float # when Power Meter was read
    daily_energy: dict[str, Any] | None = None
    daily_energy_flows: dict[str, Any] | None = None
    battery_provenance: dict[str, Any] | None = None
    daily_kpis: dict[str, Any] | None = None
    completed_day: dict[str, Any] | None = None
    accounting_status: str | None = None

    @property
    def timestamp_delta_ms(self) -> float:
        """
        Time gap between Inverter read and Power Meter read.
        This is the timing gap risk we discussed.
        Should be minimised — ideally under 500ms.
        """
        return abs(
            self.powermeter_timestamp - self.inverter_timestamp
        ) * 1000

    @property
    def is_valid(self) -> bool:
        """
        Basic sanity checks — catches silent failures
        before they reach the cloud.
        """
        if self.pv_production < 0:
            return False  # PV cannot be negative
        if self.ac_output < 0:
            return False  # AC output cannot be negative
        if self.house_consumption < 0:
            return False  # House consumption cannot be negative
        if self.house_consumption > 50000:
            return False  # Unrealistic value
        return True


def calculate_house_consumption(
    ac_output: float,
    grid_power: float
) -> float:
    """
    The most critical calculation in the system.
    House Consumption is NEVER directly measured.

    Sign convention:
    - Positive Grid Power means importing energy from the grid.
    - Negative Grid Power means exporting energy to the grid.

    Formula:
    House Consumption = AC Output + Grid Power

    When grid_power is positive (importing):
    House = AC Output + what we import from grid

    When grid_power is negative (exporting):
    House = AC Output - what we export to grid

    Examples:
    AC=2000W, Grid=+1000W (import) -> House=3000W
    AC=5000W, Grid=-3000W (export) -> House=2000W
    """
    return ac_output + grid_power
