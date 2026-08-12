# tests/integration/test_iot_integration.py

import time
import pytest
from simulators.coordinator import SystemCoordinator
from iot_device.iot_device import IoTDevice
from iot_device.energy_accounting import EnergyAccumulator


class RecordingPublisher:
    """In-memory cloud adapter used by integration tests."""

    def __init__(self):
        self.connected = False
        self.measurements = []
        self.faults = []

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def publish_measurement(self, measurement):
        self.measurements.append(measurement)
        return True

    def publish_fault(self, fault_code):
        self.faults.append(fault_code)


# ─────────────────────────────────────────────────────────────
# FIXTURES
# Pytest fixtures set up and tear down the test environment.
# Each test gets fresh simulators and a fresh IoT device.
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def coordinator():
    """
    Start both simulators once for all tests in this module.
    scope="module" means simulators start once, not per test.
    Saves startup time.
    """
    coord = SystemCoordinator()
    coord.start()
    time.sleep(1)  # Give simulators time to start
    yield coord
    coord.stop()


@pytest.fixture
def iot(coordinator):
    """
    Fresh IoT device for each test.
    Connects to the running simulators.
    Disconnects after each test.
    """
    publisher = RecordingPublisher()
    device = IoTDevice(
        inverter_host="localhost",
        inverter_port=5020,
        powermeter_host="localhost",
        powermeter_port=5021,
        poll_interval=1.0,
        publisher=publisher
    )
    device.connect()
    yield device
    device.disconnect()


# ─────────────────────────────────────────────────────────────
# SECTION 1 — Golden Dataset Tests
# Inject known values → assert exact output
# This is the most important integration test category.
# Catches silent failures invisible to unit tests.
# ─────────────────────────────────────────────────────────────

class TestGoldenDataset:
    """
    Golden dataset tests inject a known scenario into both
    simulators simultaneously and assert the IoT device
    reports the exact expected values.

    Any discrepancy reveals a silent failure somewhere
    in the read → decode → calculate → report chain.
    """

    def test_sunny_day_grid_importing(self, coordinator, iot):
        """
        QUADRANT 1 — Grid importing.
        PV insufficient for house demand.
        Grid covers the deficit.

        Inject: PV=2000, House=3000, Battery=0
        Expected: Grid=+1000 (importing), House=3000
        """
        state, grid_power = coordinator.inject_scenario(
            pv=2000,
            house=3000,
            battery=0
        )
        time.sleep(0.5)  # Allow registers to update

        measurement = iot.poll_once()

        assert measurement is not None
        assert measurement.pv_production == pytest.approx(
            2000, abs=10
        )
        assert measurement.house_consumption == pytest.approx(
            3000, abs=10
        )
        assert measurement.grid_power > 0  # importing

    def test_sunny_day_grid_exporting(self, coordinator, iot):
        """
        QUADRANT 2 — Grid exporting.
        PV produces more than house needs.
        Excess exported to grid.

        Inject: PV=6000, House=2000, Battery=0
        Expected: Grid=-4000 (exporting), House=2000
        """
        state, grid_power = coordinator.inject_scenario(
            pv=6000,
            house=2000,
            battery=0
        )
        time.sleep(0.5)

        measurement = iot.poll_once()

        assert measurement is not None
        assert measurement.pv_production == pytest.approx(
            6000, abs=10
        )
        assert measurement.house_consumption == pytest.approx(
            2000, abs=10
        )
        assert measurement.grid_power < 0  # exporting

    def test_battery_discharging_at_night(self, coordinator, iot):
        """
        QUADRANT 3 — Night, battery discharging.
        PV=0, battery covers house demand.

        Inject: PV=0, House=1500, Battery=+1500
        Expected: AC=1500, Grid=0, House=1500
        """
        state, grid_power = coordinator.inject_scenario(
            pv=0,
            house=1500,
            battery=1500
        )
        time.sleep(0.5)

        measurement = iot.poll_once()

        assert measurement is not None
        assert measurement.pv_production == pytest.approx(0, abs=5)
        assert measurement.house_consumption == pytest.approx(
            1500, abs=10
        )
        assert measurement.grid_power == pytest.approx(0, abs=10)

    def test_battery_charging_surplus(self, coordinator, iot):
        """
        QUADRANT 4 — Battery charging from PV surplus.
        PV > House, excess charges battery.

        Inject: PV=5000, House=2000, Battery=-2000
        Expected: AC=3000, House=2000
        """
        state, grid_power = coordinator.inject_scenario(
            pv=5000,
            house=2000,
            battery=-2000
        )
        time.sleep(0.5)

        measurement = iot.poll_once()

        assert measurement is not None
        assert measurement.pv_production == pytest.approx(
            5000, abs=10
        )
        assert measurement.house_consumption == pytest.approx(
            2000, abs=10
        )

    def test_house_consumption_never_negative(
        self, coordinator, iot
    ):
        """
        CRITICAL SILENT FAILURE TEST.
        House Consumption must never be negative.
        Negative = sign convention bug or inconsistent data.
        IoT must catch this before it reaches the cloud.
        """
        # Inject scenario that could cause negative house
        # if sign convention is wrong
        coordinator.inject_scenario(
            pv=1000,
            house=500,
            battery=800
        )
        time.sleep(0.5)

        measurement = iot.poll_once()

        if measurement is not None:
            assert measurement.house_consumption >= 0
        else:
            # IoT correctly rejected invalid measurement
            assert iot.has_fault("invalid_measurement")


# ─────────────────────────────────────────────────────────────
# SECTION 2 — Timing Gap Tests
# Tests for the gap between Inverter and PowerMeter reads.
# This emergent failure only appears when both run together.
# ─────────────────────────────────────────────────────────────

class TestTimingGap:
    """
    The IoT reads Inverter first, then Power Meter.
    There is always a gap between these two reads.
    During rapidly changing conditions this gap causes
    House Consumption to be calculated from two different
    moments in time — producing a value that never existed.
    """

    def test_timestamp_delta_is_small(self, coordinator, iot):
        """
        Under normal conditions the timing gap between
        Inverter read and Power Meter read should be
        well under 1000ms.
        """
        coordinator.inject_scenario(
            pv=3000, house=2000, battery=500
        )
        time.sleep(0.5)

        measurement = iot.poll_once()

        assert measurement is not None
        assert measurement.timestamp_delta_ms < 1000, (
            f"Timing gap too large: "
            f"{measurement.timestamp_delta_ms:.0f}ms"
        )

    def test_timestamp_delta_recorded(self, coordinator, iot):
        """
        Timestamp delta must always be recorded.
        Required for debugging timing-related value errors.
        """
        coordinator.inject_scenario(
            pv=3000, house=2000, battery=0
        )
        time.sleep(0.5)

        measurement = iot.poll_once()

        assert measurement is not None
        assert measurement.inverter_timestamp > 0
        assert measurement.powermeter_timestamp > 0
        assert measurement.timestamp_delta_ms >= 0

    def test_multiple_polls_consistent(self, coordinator, iot):
        """
        With stable injected values, multiple polls should
        return consistent House Consumption values.
        High variance = timing gap causing calculation errors.
        """
        coordinator.inject_scenario(
            pv=4000, house=2500, battery=500
        )
        time.sleep(0.5)

        readings = []
        for _ in range(5):
            m = iot.poll_once()
            if m:
                readings.append(m.house_consumption)
            time.sleep(0.2)

        assert len(readings) >= 3

        # All readings should be close to each other
        avg = sum(readings) / len(readings)
        for r in readings:
            assert abs(r - avg) < 50, (
                f"High variance in House Consumption: "
                f"{readings} — timing gap suspected"
            )


class TestEnergyAccounting:
    def test_modbus_measurements_integrate_within_one_percent(
        self, coordinator, tmp_path
    ):
        coordinator.inject_scenario(
            pv=3600, house=1800, battery=0
        )
        publisher = RecordingPublisher()
        device = IoTDevice(
            publisher=publisher,
            energy_accumulator=EnergyAccumulator(
                state_file=str(tmp_path / "energy.json")
            ),
            device_id="integration-device",
        )
        assert device.connect()
        try:
            first = device.poll_once()
            time.sleep(0.5)
            second = device.poll_once()
        finally:
            device.disconnect()

        elapsed_hours = (
            second.inverter_timestamp - first.inverter_timestamp
        ) / 3600
        expected_pv_wh = 3600 * elapsed_hours
        expected_house_wh = 1800 * elapsed_hours

        assert second.daily_energy["pv_generation_wh"] == pytest.approx(
            expected_pv_wh, rel=0.01
        )
        assert second.daily_energy["house_consumption_wh"] == pytest.approx(
            expected_house_wh, rel=0.01
        )
        assert second.daily_kpis["self_consumption_percent"] == pytest.approx(
            50.0, rel=0.01
        )
        assert second.daily_kpis["self_sufficiency_percent"] == pytest.approx(
            100.0, rel=0.01
        )
        assert second.daily_kpis[
            "energy_balance_error_percent"
        ] == pytest.approx(0.0, abs=0.01)


class TestSafeBatteryControl:
    def test_gateway_commands_device_and_reads_actual_state(
        self, coordinator, iot
    ):
        now = time.time()
        acknowledgement = iot.battery_controller.handle(
            {
                "schema_version": 1,
                "command_id": "integration-discharge",
                "device_id": "energy_iot_001",
                "requested_power_w": 1200,
                "issued_at": now,
            },
            now=now,
        )
        try:
            assert acknowledgement["status"] == "accepted"
            assert acknowledgement["actual_power_w"] == pytest.approx(1200)
            telemetry = iot.battery_controller.telemetry()
            assert telemetry.actual_power_w == pytest.approx(1200)
            assert telemetry.operating_mode == "discharging"
            assert telemetry.usable_capacity_kwh == 10
        finally:
            stop_time = time.time()
            iot.battery_controller.handle(
                {
                    "command_id": "integration-idle",
                    "device_id": "energy_iot_001",
                    "requested_power_w": 0,
                    "issued_at": stop_time,
                },
                now=stop_time,
            )

    def test_device_rejects_unsafe_and_gateway_replays_duplicate(
        self, iot
    ):
        now = time.time()
        payload = {
            "command_id": "integration-unsafe",
            "device_id": "energy_iot_001",
            "requested_power_w": 5000,
            "issued_at": now,
        }

        first = iot.battery_controller.handle(payload, now=now)
        replay = iot.battery_controller.handle(
            {**payload, "requested_power_w": -500}, now=now
        )

        assert first["status"] == "rejected"
        assert first["rejection_reason"] == "discharge_power_limit_exceeded"
        assert replay == first


class TestAutomaticSelfConsumption:
    def test_surplus_decision_executes_only_through_step4(
        self, coordinator, tmp_path
    ):
        coordinator.inject_scenario(pv=5000, house=2000, battery=0)
        device = IoTDevice(
            publisher=RecordingPublisher(),
            device_id="automatic-integration",
            controller_mode="automatic",
            controller_state_file=str(tmp_path / "controller.json"),
            battery_control_state_file=str(tmp_path / "commands.json"),
        )
        assert device.connect()
        try:
            measurement = device.poll_once()
            assert measurement.controller_state["reason"] == "pv_surplus"
            assert measurement.controller_state["desired_power_w"] == pytest.approx(-3000)
            assert measurement.controller_state["command_status"] == "accepted"
            telemetry = device.battery_controller.telemetry()
            assert telemetry.actual_power_w == pytest.approx(-3000)
            assert telemetry.operating_mode == "charging"
        finally:
            now = time.time()
            device.battery_controller.handle(
                {
                    "command_id": "automatic-integration-idle",
                    "device_id": "automatic-integration",
                    "requested_power_w": 0,
                    "issued_at": now,
                },
                now=now,
            )
            device.disconnect()


# ─────────────────────────────────────────────────────────────
# SECTION 3 — Connection Failure Tests
# Tests for IoT behaviour when one device goes offline.
# Silent failure risk: IoT reports stale data instead of fault.
# ─────────────────────────────────────────────────────────────

class TestConnectionFailure:
    """
    Tests IoT behaviour when Inverter or Power Meter
    becomes unavailable.

    CRITICAL: IoT must raise a fault and stop reporting
    rather than forwarding stale or incorrect data silently.
    """

    def test_successful_connection(self, iot):
        """
        Baseline — both devices connected.
        IoT should have no faults.
        """
        assert not iot.has_fault("inverter_connection_failed")
        assert not iot.has_fault("powermeter_connection_failed")
        assert iot.publisher.connected

    def test_measurement_is_sent_to_in_memory_publisher(
        self, coordinator, iot
    ):
        coordinator.inject_scenario(
            pv=3000, house=2000, battery=0
        )
        time.sleep(0.5)

        before = len(iot.publisher.measurements)
        measurement = iot.poll_once()

        assert measurement is not None
        assert iot.publisher.measurements[before] is measurement

    def test_poll_returns_measurement_when_connected(
        self, coordinator, iot
    ):
        """
        Normal operation — poll returns valid measurement.
        """
        coordinator.inject_scenario(
            pv=3000, house=2000, battery=0
        )
        time.sleep(0.5)

        measurement = iot.poll_once()
        assert measurement is not None

    def test_invalid_measurement_raises_fault(
        self, coordinator, iot
    ):
        """
        When measurement fails validation —
        IoT must raise fault, not forward bad data.
        This is the silent failure prevention mechanism.
        """
        # Inject scenario that produces invalid measurement
        # by directly setting inconsistent register values
        coordinator.inverter.set_register_value(0, 100)
        coordinator.power_meter.set_grid_power(99999)
        time.sleep(0.3)

        measurement = iot.poll_once()

        # Either measurement is None (rejected)
        # or fault was raised
        if measurement is not None:
            assert measurement.is_valid
        else:
            assert iot.has_fault("invalid_measurement") or \
                   iot.has_fault("powermeter_read_failed") or \
                   iot.has_fault("inverter_read_failed")

    def test_measurement_is_valid_under_normal_conditions(
        self, coordinator, iot
    ):
        """
        All four measurements must pass validation
        under normal operating conditions.
        """
        coordinator.inject_scenario(
            pv=3000, house=2000, battery=500
        )
        time.sleep(0.5)

        measurement = iot.poll_once()

        assert measurement is not None
        assert measurement.is_valid
        assert measurement.pv_production >= 0
        assert measurement.ac_output >= 0
        assert measurement.house_consumption >= 0


# ─────────────────────────────────────────────────────────────
# SECTION 4 — Silent Failure Detection Tests
# Tests specifically designed to catch silent failures
# that look normal but produce wrong data.
# ─────────────────────────────────────────────────────────────

class TestSilentFailureDetection:
    """
    Silent failures are the most dangerous failure mode
    in this architecture. The system continues operating,
    no errors are raised, but data reaching the cloud is wrong.

    These tests specifically hunt for them.
    """

    def test_pv_cannot_be_negative(self, coordinator, iot):
        """
        PV Production is always >= 0.
        Negative PV = decoding bug (our Bug 2 from Phase 1).
        """
        coordinator.inject_scenario(
            pv=5800,  # large value that caused Bug 2
            house=2000,
            battery=0
        )
        time.sleep(0.5)

        measurement = iot.poll_once()

        assert measurement is not None
        assert measurement.pv_production >= 0, (
            f"PV Production is negative: "
            f"{measurement.pv_production}W — "
            f"unsigned register decoded as signed"
        )

    def test_ac_output_cannot_be_negative(
        self, coordinator, iot
    ):
        """
        AC Output is always >= 0.
        Negative = firmware calculation bug (our Bug 1).
        """
        coordinator.inject_scenario(
            pv=1000,
            house=2000,
            battery=-3000  # charging more than PV
        )
        time.sleep(0.5)

        measurement = iot.poll_once()

        if measurement is not None:
            assert measurement.ac_output >= 0, (
                f"AC Output is negative: "
                f"{measurement.ac_output}W — "
                f"battery charging exceeds PV"
            )

    def test_scaling_factor_correct(self, coordinator, iot):
        """
        Scaling factor must be exactly 10.
        Wrong factor (e.g. 100) = 10x error silently.
        Inject 3500W, assert 3500W ± tolerance arrives.
        Not 350W (factor 100) or 35000W (factor 1).
        """
        coordinator.inject_scenario(
            pv=3500,
            house=2000,
            battery=0
        )
        time.sleep(0.5)

        measurement = iot.poll_once()

        assert measurement is not None
        assert measurement.pv_production == pytest.approx(
            3500, abs=50
        ), (
            f"PV={measurement.pv_production}W — "
            f"expected ~3500W — scaling factor wrong?"
        )

    def test_sign_convention_grid_export(
        self, coordinator, iot
    ):
        """
        CRITICAL: Grid export must be negative.
        If sign is flipped — House Consumption wildly wrong.
        This is the CT Clamp backwards scenario in software.
        """
        coordinator.inject_scenario(
            pv=6000,   # large PV → exporting to grid
            house=2000,
            battery=0
        )
        time.sleep(0.5)

        measurement = iot.poll_once()

        assert measurement is not None
        # With PV=6000, House=2000, Battery=0
        # Excess = 4000W → should export → grid negative
        assert measurement.grid_power < 0, (
            f"Grid should be negative (exporting) "
            f"but got {measurement.grid_power}W — "
            f"sign convention wrong"
        )

    def test_house_consumption_matches_expected(
        self, coordinator, iot
    ):
        """
        END TO END GOLDEN DATASET TEST.
        Inject known values → assert exact House Consumption.
        Any discrepancy = silent failure in the chain.
        """
        coordinator.inject_scenario(
            pv=4000,
            house=3000,
            battery=500
        )
        time.sleep(0.5)

        measurement = iot.poll_once()

        assert measurement is not None
        # House should be approximately 3000W
        assert measurement.house_consumption == pytest.approx(
            3000, abs=50
        ), (
            f"House Consumption mismatch: "
            f"expected ~3000W got {measurement.house_consumption}W"
        )
