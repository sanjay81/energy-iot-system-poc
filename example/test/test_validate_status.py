import pytest
from example.validate_device_status import validate_device_status

payload = {
    "device_id": "iot-001",
    "uptime_seconds": 1711001000,
    "status": "online",
    "firmware_version": "1.4.2",
    "battery_level_pct": 85,
    "signal_strength": -65
}

def test_happy_path():
    result = validate_device_status(payload)
    assert result == []

def test_reboot_warning():
    payloads = payload.copy()
    payloads["uptime_seconds"] = 30
    result = validate_device_status(payloads)
    assert "device recently rebooted" in result

@pytest.mark.parametrize("firmware", ["1.2.3", "10.4.22", "0.0.1"])
def test_valid_firmware(firmware):

    payloads = payload.copy()
    payloads["firmware_version"] = firmware
    result = validate_device_status(payloads)
    assert result == []

@pytest.mark.parametrize("firmware", ["v1.4.3", "abc", "1.4", "1.2.3.4"])
def test_invalid_firmware(firmware):
    payloads = payload.copy()
    payloads["firmware_version"] = firmware
    result = validate_device_status(payloads)
    assert any("firmware" in e for e in result)



