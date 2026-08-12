import pytest

from simulators.battery_device import BatteryDevice


def test_accepts_charge_discharge_and_idle_commands():
    battery = BatteryDevice()

    assert battery.command("charge", -2000)["status"] == "accepted"
    assert battery.snapshot()["actual_power_w"] == -2000
    assert battery.command("discharge", 1500)["status"] == "accepted"
    assert battery.snapshot()["actual_power_w"] == 1500
    assert battery.command("idle", 0)["status"] == "accepted"
    assert battery.snapshot()["actual_power_w"] == 0


@pytest.mark.parametrize(
    "requested,reason",
    [
        (-3001, "charge_power_limit_exceeded"),
        (3001, "discharge_power_limit_exceeded"),
    ],
)
def test_rejects_power_above_device_limits(requested, reason):
    battery = BatteryDevice()

    acknowledgement = battery.command("unsafe", requested)

    assert acknowledgement["status"] == "rejected"
    assert acknowledgement["rejection_reason"] == reason
    assert battery.snapshot()["actual_power_w"] == 0


def test_enforces_soc_limits_and_stops_at_boundary():
    full = BatteryDevice(soc_percent=90)
    empty = BatteryDevice(soc_percent=10)

    assert full.command("charge", -100)["rejection_reason"] == "maximum_soc_reached"
    assert empty.command("discharge", 100)["rejection_reason"] == "minimum_soc_reached"

    battery = BatteryDevice(soc_percent=10.1, usable_capacity_kwh=10)
    battery.command("drain", 3000)
    battery.advance(60)
    assert battery.snapshot()["soc_percent"] == 10
    assert battery.snapshot()["actual_power_w"] == 0


def test_duplicate_command_is_idempotent():
    battery = BatteryDevice()
    first = battery.command("same", 1000)
    battery.advance(60)
    replay = battery.command("same", -1000)

    assert replay == first
    assert battery.snapshot()["requested_power_w"] == 1000


def test_rejected_duplicate_remains_rejected_after_device_state_changes():
    battery = BatteryDevice()
    first = battery.command("too-large", 5000)
    battery.state.max_discharge_power_w = 6000

    replay = battery.command("too-large", 5000)

    assert replay == first
    assert replay["status"] == "rejected"


def test_unavailable_device_rejects_and_persists_state(tmp_path):
    path = tmp_path / "battery.json"
    battery = BatteryDevice(state_file=str(path), soc_percent=55)
    battery.command("accepted", -1000)
    battery.advance(3600)
    expected_soc = battery.snapshot()["soc_percent"]

    restored = BatteryDevice(state_file=str(path))
    assert restored.snapshot()["soc_percent"] == pytest.approx(expected_soc)
    assert restored.snapshot()["actual_power_w"] == -1000
    restored.set_available(False)
    acknowledgement = restored.command("offline", 100)
    assert acknowledgement["rejection_reason"] == "device_unavailable"
