payload = {
    "device_id": "energy_iot_001",
    "timestamp": 1779008267.28,
    "pv_production_w": 3500.0,
    "house_consumption_w": 2000.0,
    "battery_power_w": 1000.0,
    "grid_power_w": -500.0
}

print(check_required_fields(payload))
