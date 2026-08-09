# tests/unit/test_calculator.py

import pytest
from iot_device.modbus_client import registers_to_watts_signed_32


def test_signed_32_bit_grid_export_supports_more_than_3276_watts():
    raw = int(-4000 * 10) & 0xFFFFFFFF
    high, low = (raw >> 16) & 0xFFFF, raw & 0xFFFF

    assert registers_to_watts_signed_32(high, low) == -4000
from iot_device.calculator import calculate_house_consumption
from simulators.scenarios import (
    EnergyState,
    get_energy_state,
    get_battery_power,
    get_pv_production
)


class TestHouseConsumptionCalculation:
    """Lock down the production grid-power sign convention."""

    def test_grid_import_is_added_to_ac_output(self):
        assert calculate_house_consumption(2000, 1000) == 3000

    def test_grid_export_is_subtracted_from_ac_output(self):
        assert calculate_house_consumption(5000, -3000) == 2000

    def test_balanced_grid_leaves_ac_output_unchanged(self):
        assert calculate_house_consumption(3000, 0) == 3000

    def test_battery_supplies_house_at_night(self):
        assert calculate_house_consumption(1500, 0) == 1500

# ─────────────────────────────────────────────────────────────
# SECTION 1 — EnergyState calculation tests
# These test the core formula:
# House Consumption = Inverter AC Output + Grid Power
# AC Output = PV Production − Battery Power
# ─────────────────────────────────────────────────────────────

class TestInverterACOutput:
    """
    Tests for inverter_ac_output property.
    This is the value stored in register 40002.
    Must never be negative — inverter cannot produce negative AC.
    """

    def test_ac_output_when_battery_charging(self):
        """
        Sunny day — battery charging from PV surplus.
        AC Output should be PV minus what goes to battery.
        """
        state = EnergyState(
            pv_production=5000,
            house_consumption=2000,
            battery_power=1500   # charging
        )
        assert state.inverter_ac_output == 3500

    def test_ac_output_when_battery_discharging(self):
        """
        Evening — battery discharging to supplement low PV.
        AC Output should be PV PLUS what battery contributes.
        This is why AC Output > PV Production when discharging.
        """
        state = EnergyState(
            pv_production=1000,
            house_consumption=3000,
            battery_power=-1500  # discharging
        )
        assert state.inverter_ac_output == 2500

    def test_ac_output_never_negative(self):
        """
        CRITICAL: AC Output must never be negative.
        If battery charging rate exceeds PV production
        in our model, clamp to zero.
        This was our first bug — caught by this test.
        """
        state = EnergyState(
            pv_production=1000,
            house_consumption=2000,
            battery_power=3000   # charging more than PV produces
        )
        assert state.inverter_ac_output >= 0

    def test_ac_output_at_night(self):
        """
        Night — PV = 0, battery discharging.
        AC Output = battery discharge only.
        """
        state = EnergyState(
            pv_production=0,
            house_consumption=1500,
            battery_power=-1500  # discharging
        )
        assert state.inverter_ac_output == 1500

    def test_ac_output_all_zero(self):
        """
        Edge case — everything zero.
        Night, battery idle, no consumption.
        """
        state = EnergyState(
            pv_production=0,
            house_consumption=0,
            battery_power=0
        )
        assert state.inverter_ac_output == 0


class TestGridPower:
    """
    Tests for grid_power property.
    Positive = importing from grid.
    Negative = exporting to grid.

    These are the FOUR QUADRANTS we discussed.
    Every sign convention bug lives in one of these four tests.
    """

    def test_grid_importing_when_pv_insufficient(self):
        """
        QUADRANT 1 — Grid importing.
        House needs more than PV produces.
        Grid makes up the difference.
        """
        state = EnergyState(
            pv_production=2000,
            house_consumption=3000,
            battery_power=0
        )
        # Grid = House - AC Output = 3000 - 2000 = 1000W importing
        assert state.grid_power == 1000
        assert state.grid_power > 0  # positive = importing

    def test_grid_exporting_when_pv_surplus(self):
        """
        QUADRANT 2 — Grid exporting.
        PV produces more than house needs.
        Excess goes to grid.
        """
        state = EnergyState(
            pv_production=5000,
            house_consumption=2000,
            battery_power=0
        )
        # Grid = House - AC Output = 2000 - 5000 = -3000W exporting
        assert state.grid_power == -3000
        assert state.grid_power < 0  # negative = exporting

    def test_grid_zero_when_perfectly_balanced(self):
        """
        QUADRANT 3 — Perfect balance.
        PV exactly matches house consumption.
        No grid interaction.
        """
        state = EnergyState(
            pv_production=3000,
            house_consumption=3000,
            battery_power=0
        )
        assert state.grid_power == 0

    def test_grid_when_battery_discharging_at_night(self):
        """
        QUADRANT 4 — Night, battery discharging.
        No PV, house powered by battery.
        Grid covers what battery cannot.
        """
        state = EnergyState(
            pv_production=0,
            house_consumption=2000,
            battery_power=-1500  # discharging 1500W
        )
        # AC Output = 0 - (-1500) = 1500W
        # Grid = House - AC Output = 2000 - 1500 = 500W importing
        assert state.grid_power == 500
        assert state.grid_power > 0  # still importing from grid


# ─────────────────────────────────────────────────────────────
# SECTION 2 — Raw register encoding/decoding tests
# These test the scaling factor and sign convention
# in the Modbus layer.
# Our second bug lived here.
# ─────────────────────────────────────────────────────────────

class TestRegisterEncoding:
    """
    Tests for raw register value encoding and decoding.
    Scaling factor = 10.
    Unsigned registers: PV Production, AC Output (always positive)
    Signed registers: Battery Power (can be negative)
    """

    SCALING_FACTOR = 10

    def raw_to_watts_unsigned(self, raw: int) -> float:
        """Decode unsigned register — PV, AC Output."""
        return raw / self.SCALING_FACTOR

    def raw_to_watts_signed(self, raw: int) -> float:
        """Decode signed register — Battery Power."""
        if raw > 32767:
            raw = raw - 65536
        return raw / self.SCALING_FACTOR

    def watts_to_raw(self, watts: float) -> int:
        """Encode watts to raw register value."""
        raw = int(watts * self.SCALING_FACTOR)
        if raw < 0:
            raw = raw & 0xFFFF
        return raw

    def test_pv_large_value_decodes_correctly(self):
        """
        CRITICAL: This is the bug we found.
        PV = 5791W → raw = 57910
        57910 > 32767 but it is NOT negative
        Must NOT apply two's complement to PV register.
        """
        pv_watts = 5791.0
        raw = self.watts_to_raw(pv_watts)
        decoded = self.raw_to_watts_unsigned(raw)
        assert decoded == pytest.approx(pv_watts, abs=0.1)

    def test_battery_negative_encodes_and_decodes(self):
        """
        Battery discharging → negative watts.
        Must survive encode → raw → decode round trip.
        """
        battery_watts = -1500.0
        raw = self.watts_to_raw(battery_watts)
        decoded = self.raw_to_watts_signed(raw)
        assert decoded == pytest.approx(battery_watts, abs=0.1)

    def test_battery_positive_encodes_and_decodes(self):
        """Battery charging → positive watts."""
        battery_watts = 2000.0
        raw = self.watts_to_raw(battery_watts)
        decoded = self.raw_to_watts_signed(raw)
        assert decoded == pytest.approx(battery_watts, abs=0.1)

    def test_zero_encodes_and_decodes(self):
        """Zero watts — battery idle or no PV."""
        raw = self.watts_to_raw(0)
        assert self.raw_to_watts_unsigned(raw) == 0
        assert self.raw_to_watts_signed(raw) == 0

    def test_wrong_scaling_factor_detected(self):
        """
        If wrong scaling factor used (100 instead of 10)
        value will be 10x too small.
        This test documents the correct behaviour.
        """
        pv_watts = 3500.0
        raw = self.watts_to_raw(pv_watts)

        # Correct scaling
        correct = raw / 10
        assert correct == pytest.approx(3500.0, abs=0.1)

        # Wrong scaling — silent failure
        wrong = raw / 100
        assert wrong == pytest.approx(350.0, abs=0.1)
        assert wrong != pytest.approx(3500.0, abs=1.0)


# ─────────────────────────────────────────────────────────────
# SECTION 3 — Battery strategy tests
# Tests for get_battery_power function.
# ─────────────────────────────────────────────────────────────

class TestBatteryStrategy:

    def test_charges_when_pv_surplus_and_not_full(self):
        result = get_battery_power(
            pv=5000, house=2000, battery_soc=50
        )
        assert result > 0

    def test_discharges_when_deficit_and_not_empty(self):
        result = get_battery_power(
            pv=0, house=2000, battery_soc=50
        )
        assert result < 0

    def test_idle_when_battery_empty_and_deficit(self):
        result = get_battery_power(
            pv=0, house=2000, battery_soc=5
        )
        assert result == 0.0

    def test_idle_when_battery_full_and_surplus(self):
        result = get_battery_power(
            pv=5000, house=2000, battery_soc=95
        )
        assert result == 0.0

    def test_does_not_charge_at_night(self):
        """
        CRITICAL: Battery must not charge when PV = 0.
        This was our first bug.
        """
        result = get_battery_power(
            pv=0, house=500, battery_soc=50
        )
        assert result <= 0

    def test_charge_capped_at_3000w(self):
        """Battery charge rate cannot exceed hardware limit."""
        result = get_battery_power(
            pv=6000, house=500, battery_soc=50
        )
        assert result <= 3000

    def test_discharge_capped_at_3000w(self):
        """Battery discharge rate cannot exceed hardware limit."""
        result = get_battery_power(
            pv=0, house=6000, battery_soc=50
        )
        assert result >= -3000
