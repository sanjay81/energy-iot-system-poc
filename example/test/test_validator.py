from example.validator import validate_payload

def test_valid_payload():
    payload = {
        "device_id": "energy_iot_001",
        "timestamp": 1779008267.28,
        "pv_production_w": 3500.0,
        "house_consumption_w": 2000.0,
        "battery_power_w": 1000.0,
        "grid_power_w": 500.0
    }

    result = validate_payload(payload)

    assert result == []

def test_missing_device_id():
    payload = {
        "timestamp": 1779008267.28,
        "pv_production_w": 3500.0,
        "house_consumption_w": 2000.0,
        "battery_power_w": 1000.0,
        "grid_power_w": 500.0
    }

    result = validate_payload(payload)

    assert "Missing required field: device_id" in result

def test_negative_pv():
    payload = {
        "device_id": "energy_iot_001",
        "timestamp": 1779008267.28,
        "pv_production_w": -100.0,
        "house_consumption_w": 2000.0,
        "battery_power_w": 1000.0,
        "grid_power_w": 500.0
    }

    result = validate_payload(payload)

    assert "pv_production_w must be >= 0" in result

def test_formula_mistmatch():
    payload = {
        "device_id": "energy_iot_001",
        "timestamp": 1779008267.28,
        "pv_production_w": 3500,
        "house_consumption_w": 999.0, # expected 2000, got 999 → 1001W off
        "battery_power_w": 1000.0,
        "grid_power_w": 500.0
    }

    result = validate_payload(payload)

    assert any("house_consumption_w mismatch" in e for e in result)


# Test 5 invalid timestamp

def test_for_timestamp_invalid():
    payload = {
        "device_id": "energy_iot_001",
        "timestamp": -1,
        "pv_production_w": 3500,
        "house_consumption_w": 2000.0,
        "battery_power_w": 1000.0,
        "grid_power_w": 500.0
    }

    result = validate_payload(payload)

    assert "timestamp must be positive" in result
