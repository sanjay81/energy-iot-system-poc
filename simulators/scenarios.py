# simulators/scenarios.py
# Build the energy scenarios first.
# Before the simulator, define what a realistic day looks like.

import math
from dataclasses import dataclass

@dataclass
class EnergyState:
    """
    Represents the current energy state of the system.
    All values in Watts.
    Positive battery = charging, negative = discharging.
    Positive grid = importing, negative = exporting.
    """
    pv_production: float
    house_consumption: float
    battery_power: float

    @property
    def grid_power(self) -> float:
        """
        Grid power is derived — not a direct measurement.
        Positive = importing from grid.
        Negative = exporting to grid.

        Formula:
        Grid = House Consumption - PV Production - Battery Discharge

        Example 1 — sunny day, battery charging:
        PV=5000, House=2000, Battery charging=1000
        Grid = 2000 - 5000 + 1000 = -2000 (exporting 2000W)

        Example 2 — evening, battery discharging:
        PV=0, House=2000, Battery discharging=-1500
        Grid = 2000 - 0 - (-1500) ... wait

        Simpler model:
        Inverter AC Output = PV Production + Battery Discharge
        Grid = House Consumption - Inverter AC Output
        """
        inverter_ac_output = self.pv_production - self.battery_power
        return self.house_consumption - inverter_ac_output

    @property
    def inverter_ac_output(self) -> float:
        """
        What the inverter puts on the AC bus.
        = PV production adjusted for battery charging/discharging
        """
        output = self.pv_production - self.battery_power
        return max(0.0, output)  # cannot be negative
        #return self.pv_production - self.battery_power


def get_pv_production(hour: float) -> float:
    """
    Simulates PV production across a day using a sine curve.
    Peak at noon (hour 12), zero at night.
    Max production: 6000W
    """
    if hour < 6 or hour > 20:
        return 0.0

    # Sine curve between 6am and 8pm
    angle = math.pi * (hour - 6) / 14
    raw = math.sin(angle)

    # Add slight randomness for realism
    import random
    noise = random.uniform(0.95, 1.05)

    return round(raw * 6000 * noise, 1)


def get_house_consumption(hour: float) -> float:
    """
    Simulates house power demand across a day.
    Morning peak, midday dip, evening peak.
    """
    # Base load always present
    base = 500.0

    # Morning peak 7-9am
    morning = 1500 * math.exp(-0.5 * ((hour - 8) / 1.0) ** 2)

    # Evening peak 6-9pm
    evening = 2000 * math.exp(-0.5 * ((hour - 19) / 1.5) ** 2)

    import random
    noise = random.uniform(0.9, 1.1)

    return round((base + morning + evening) * noise, 1)


def get_battery_power(pv: float, house: float,
                       battery_soc: float) -> float:
    """
    Simple battery strategy:
    - If PV > House and battery not full → charge battery
    - If PV < House and battery not empty → discharge battery
    - Otherwise → 0

    Positive = charging, Negative = discharging.
    Max charge/discharge rate: 3000W

    Scenario 1 → PV high, house low, battery not full
             surplus > 0 → battery charges
             → Postive number

    Scenario 2 → PV low, house high, battery not empty
             surplus < 0 → battery discharges
             → Negative number

    Scenario 3 → PV = 0 (night), battery empty
             surplus < 0 but soc ≤ 5 → no discharge
             → Zero → grid covers everything

    Scenario 4 → PV high, house low, battery full
             surplus > 0 but soc ≥ 95 → no charging
             → Path Zero → excess exported to grid
    """
    surplus = pv - house

    # Only charge if PV is actually producing
    #if surplus > 0 and battery_soc < 95:
    if surplus > 0 and battery_soc < 95 and pv > 0:

        # Charge with surplus, up to 3000W
        return min(surplus, 3000.0)

    elif surplus < 0 and battery_soc > 5:
        # Discharge to cover deficit, up to 3000W
        return max(surplus, -3000.0)

    return 0.0


def get_energy_state(hour: float,
                      battery_soc: float = 50.0) -> EnergyState:
    """
    Returns the complete energy state for a given hour.
    This is what we inject into the simulator registers.
    """
    pv = get_pv_production(hour)
    house = get_house_consumption(hour)
    battery = get_battery_power(pv, house, battery_soc)

    return EnergyState(
        pv_production=pv,
        house_consumption=house,
        battery_power=battery
    )