def validate_payload(payload: dict) ->list[str]:
    """
    Validate one Modbus RTU telemetry payload
    """
    errors = []
    required_fileds = {
        "device_id": int,
        "register": int,
        "raw_value": (int, float),
        "timestamp": int,
        "scale": (int, float)
    }
    for field, expected_type in required_fileds.items():
        if field not in payload:
            errors.append(f"payload {field} is missing")
        elif not isinstance(payload[field], expected_type):
            errors.append(f"{field} has invalid type")

    # If required/type errors exist, avoid unsafe calculations
    if errors:
        return errors

    device_id = payload.get("device_id")
    register = payload.get("register")
    raw_value = payload.get("raw_value")
    timestamp = payload.get("timestamp")
    scale = payload.get("scale")

    if device_id <= 0:
        errors.append(f"device_id must be greater than zero")

    supported_register = [30001, 30002, 30003]

    if register not in supported_register:
        errors.append(f"register {register} is not supported")

    if scale <= 0:
        errors.append(f"scale must greater then zero")

    scale_value = raw_value * scale
    if scale_value < 0:
        errors.append(f"scale value  {scale_value} must not be negative")

    if timestamp <= 0:
        errors.append(f"timestamp  {timestamp} must be greater than zero")

    return errors


if __name__=="__main__":

    payload = {
        "device_id": 1,
        "register": 30006,
        "raw_value": 35000,
        "timestamp": 1711001000,
        "scale": 0.01
    }

    result = validate_payload(payload)
    print(result)