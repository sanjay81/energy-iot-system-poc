import pytest
from example.validate_modbus_rtu import validate_payload

payload = {
        "device_id": 1,
        "register": 30001,
        "raw_value": 35000,
        "timestamp": 1711001000,
        "scale": 0.01
    }

def test_valid_payload():
    result = validate_payload(payload)
    assert result == []

@pytest.mark.parametrize("device_id", [0, -1])
def test_invalid_payload(device_id):

    payloads = payload.copy()
    payloads["device_id"] = device_id
    result = validate_payload(payloads)
    assert "device_id must be greater than zero" in result