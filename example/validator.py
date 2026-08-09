def Check_required_fileds(payload):
    """
    this is for check required filed
    """
    errors = []

    required_filed = {
        "device_id": str,
        "timestamp": (int, float),
        "pv_production_w": (int, float),
        "house_consumption_w": (int, float),
        "battery_power_w": (int, float),
        "grid_power_w": (int, float),

    }
    # Checking missing fields
    for field, expected_type in required_filed.items():
        if field not in payload:
            errors.append(f"Missing required field: {field}")
            continue
        value = payload[field]

        # device_id validation
        if field == "device_id":
            if not isinstance(value, str) or not value.strip():
            #if not isinstance(value, str) or value.strip():
                errors.append("device_id must be a non-empty string")

        # timestamp validation

        elif field == "timestamp":
            if not isinstance(value, expected_type):
                errors.append("timestamp must be int or float")
            elif value <= 0:
                errors.append("timestamp must be positive")

        # numeric filed validation

        else:
            if not isinstance(value, expected_type):
                errors.append(f"{field} must be int or float")

    return errors

def validate_ranges(payload):
    """
    This is for validate range to check pv = 0
    """

    errors = []

    if payload["pv_production_w"] < 0:
        errors.append("pv_production_w must be >= 0")

    if payload["house_consumption_w"] < 0:
        errors.append("house_consumption_w must be >= 0")

    if payload["house_consumption_w"] > 50000:
        errors.append("house_consumption_w must be <= 50000")

    return errors

def validate_energy_formulla(payload, tolerance_w= 10):
    """
    validating engery formulla
    """

    errors = []

    pv = payload["pv_production_w"]
    battery = payload["battery_power_w"]
    grid = payload["grid_power_w"]
    actual_house = payload["house_consumption_w"]

    ac_output = pv - battery
    expected_house_consumption = ac_output - grid

    diffrence = abs(actual_house - expected_house_consumption)

    if diffrence > tolerance_w:
        errors.append(
            f"house_consumption_w mismatch: expected approx"
            f"{expected_house_consumption}w, got{actual_house}w, diffrence {diffrence}w"
            )

    return errors

def validate_payload(payload):
    """
    validate payload
    """

    errors = []

    errors.extend(Check_required_fileds(payload))

    # stop early if required feilds or types are invalid
    # rhis avoids keyError or typeError in lcater validation

    if errors:
        return errors

    errors.extend(validate_ranges(payload))
    errors.extend(validate_energy_formulla(payload))

    return errors





