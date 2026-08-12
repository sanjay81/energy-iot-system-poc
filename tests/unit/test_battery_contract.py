import pytest

from iot_device.battery_contract import BatteryCommand


def test_command_contract_defaults_to_sixty_second_expiry():
    command = BatteryCommand.from_payload(
        {
            "schema_version": 1,
            "command_id": "cmd-1",
            "device_id": "home-1",
            "requested_power_w": -1500,
            "issued_at": 1000,
        },
        expected_device_id="home-1",
        now=1010,
    )

    assert command.requested_power_w == -1500
    assert command.expires_at == 1060


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"schema_version": 2}, "unsupported_schema_version"),
        ({"device_id": "someone-else"}, "wrong_device"),
        ({"expires_at": 999}, "invalid_expiry"),
    ],
)
def test_command_contract_rejects_invalid_payload(change, reason):
    payload = {
        "schema_version": 1,
        "command_id": "cmd-1",
        "device_id": "home-1",
        "requested_power_w": 500,
        "issued_at": 1000,
    }
    payload.update(change)

    with pytest.raises(ValueError, match=reason):
        BatteryCommand.from_payload(payload, "home-1", now=1001)


def test_command_contract_rejects_stale_command():
    with pytest.raises(ValueError, match="stale_command"):
        BatteryCommand.from_payload(
            {
                "command_id": "cmd-1",
                "device_id": "home-1",
                "requested_power_w": 0,
                "issued_at": 1000,
                "expires_at": 1010,
            },
            "home-1",
            now=1011,
        )
