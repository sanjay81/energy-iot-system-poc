# The Energy IoT device sends a device status payload every 60 seconds.
# Write a function that validates the device status.

import re

def validate_device_status(payload: dict) -> list[str]:
    errors = []

    required_fields = {
        "device_id": str,
        "uptime_seconds": int,
        "status": str,
        "firmware_version": str,
        "battery_level_pct": (int, float),
        "signal_strength": (int, float),
       }

   # Step 1: check missing fields and types

    for field, expected_type in required_fields.items():
        if field not in payload:
            errors.append(f"missing {field} in payload")
        elif not isinstance(payload[field],expected_type):
            errors.append(f"expected_type {field} is invalid ")

    # Step 2: if errors exist, return errors
    if errors:
        return errors

    # Step 3: extract values using payload.get()

    device_id = payload.get("device_id")
    uptime_seconds = payload.get("uptime_seconds")
    status = payload.get("status")
    firmware_version = payload.get("firmware_version")
    battery_level_pct = payload.get("battery_level_pct")
    signal_strength = payload.get("signal_strength")

    # Step 4: business validation

    if not device_id.strip():
        errors.append(f"device_id must be not empty")

    if uptime_seconds <0:
        errors.append(f"uptime_seconds {uptime_seconds} must be greater than zero")

    if uptime_seconds < 60:
        errors.append(f"device recently rebooted")

    supported_status = ["online", "offline", "error", "maintenance"]

    if status not in supported_status:
        errors.append(f"Invalid {status} status")

    if not firmware_version.strip():
        errors.append(f"firmware {firmware_version} is not negative")


    if battery_level_pct < 0 or battery_level_pct > 100:
        errors.append(f"battery {battery_level_pct} is range in 0 to 100")

    if signal_strength < -120 or signal_strength > 0:
        errors.append("signal_strength must be between -120 and 0")

    pattern = r"\d+\.\d+\.\d+"
    if not re.fullmatch(pattern, firmware_version):
        errors.append(f"firmware version must match pattern X.Y.Z")

    return errors

if __name__=="__main__":

    payload = {
        "device_id": "iot-001",
        "uptime_seconds": 1711001000,
        "status": "online",
        "firmware_version": "1.2.3",
        "battery_level_pct": 85,
        "signal_strength": -65
    }
    result = validate_device_status(payload)
    print(result)



