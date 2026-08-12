"""Versioned, transport-independent battery control contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

BATTERY_SCHEMA_VERSION = 1
COMMAND_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class BatteryCommand:
    command_id: str
    device_id: str
    requested_power_w: float
    issued_at: float
    expires_at: float
    schema_version: int = BATTERY_SCHEMA_VERSION

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], expected_device_id: str, now: float
    ) -> "BatteryCommand":
        required = {"command_id", "device_id", "requested_power_w", "issued_at"}
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError(f"missing_fields:{','.join(missing)}")
        if payload.get("schema_version", BATTERY_SCHEMA_VERSION) != BATTERY_SCHEMA_VERSION:
            raise ValueError("unsupported_schema_version")
        if payload["device_id"] != expected_device_id:
            raise ValueError("wrong_device")
        command_id = str(payload["command_id"]).strip()
        if not command_id:
            raise ValueError("invalid_command_id")
        try:
            requested = float(payload["requested_power_w"])
            issued_at = float(payload["issued_at"])
            expires_at = float(
                payload.get("expires_at", issued_at + COMMAND_TTL_SECONDS)
            )
        except (TypeError, ValueError) as error:
            raise ValueError("invalid_numeric_field") from error
        if expires_at <= issued_at:
            raise ValueError("invalid_expiry")
        if now > expires_at:
            raise ValueError("stale_command")
        return cls(
            command_id=command_id,
            device_id=expected_device_id,
            requested_power_w=requested,
            issued_at=issued_at,
            expires_at=expires_at,
        )


@dataclass(frozen=True)
class BatteryAcknowledgement:
    command_id: str
    device_id: str
    status: str
    requested_power_w: float | None
    actual_power_w: float
    timestamp: float
    rejection_reason: str | None = None
    schema_version: int = BATTERY_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BatteryTelemetry:
    device_id: str
    actual_power_w: float
    requested_power_w: float
    soc_percent: float
    usable_capacity_kwh: float
    max_charge_power_w: float
    max_discharge_power_w: float
    min_soc_percent: float
    max_soc_percent: float
    available: bool
    operating_mode: str
    timestamp: float
    efficiency_percent: float | None = None
    schema_version: int = BATTERY_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)
