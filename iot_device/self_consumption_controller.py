"""Step 5 deterministic self-consumption decision layer."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass

CONTROLLER_SCHEMA_VERSION = 1
AUTOMATIC_MODE = "automatic"
MANUAL_MODE = "manual"


@dataclass(frozen=True)
class ControllerDecision:
    mode: str
    status: str
    reason: str
    desired_power_w: float
    measurement_timestamp: float
    command_id: str | None = None
    command_status: str | None = None
    actual_power_w: float | None = None
    rejection_reason: str | None = None
    schema_version: int = CONTROLLER_SCHEMA_VERSION


class SelfConsumptionController:
    """Choose desired power; delegate all execution safety to Step 4."""

    def __init__(
        self,
        device_id: str,
        command_handler,
        mode: str = MANUAL_MODE,
        max_power_w: float = 3000.0,
        stale_after_seconds: float = 15.0,
        deadband_w: float = 50.0,
        state_file: str | None = None,
    ):
        if mode not in {MANUAL_MODE, AUTOMATIC_MODE}:
            raise ValueError("mode must be 'manual' or 'automatic'")
        self.device_id = device_id
        self.command_handler = command_handler
        self.mode = mode
        self.max_power_w = max_power_w
        self.stale_after_seconds = stale_after_seconds
        self.deadband_w = deadband_w
        self.state_file = state_file
        self.last_measurement_timestamp: float | None = None
        self.last_desired_power_w: float | None = None
        self.last_decision: dict | None = None
        self._load()

    def evaluate(self, measurement, now: float | None = None) -> ControllerDecision:
        now = time.time() if now is None else now
        timestamp = measurement.inverter_timestamp
        if self.mode == MANUAL_MODE:
            return self._decision("inactive", "manual_mode", 0.0, timestamp)
        if self.last_measurement_timestamp is not None and timestamp <= self.last_measurement_timestamp:
            return self._decision("ignored", "duplicate_or_out_of_order", 0.0, timestamp)

        age = now - timestamp
        if age > self.stale_after_seconds or age < -1.0:
            desired = 0.0
            reason = "stale_measurement"
        else:
            balance_w = measurement.pv_production - measurement.house_consumption
            if abs(balance_w) <= self.deadband_w:
                desired = 0.0
                reason = "balanced_deadband"
            elif balance_w > 0:
                desired = -min(balance_w, self.max_power_w)
                reason = "pv_surplus"
            else:
                desired = min(-balance_w, self.max_power_w)
                reason = "household_deficit"

        self.last_measurement_timestamp = timestamp
        if desired == self.last_desired_power_w:
            decision = self._decision("unchanged", reason, desired, timestamp)
            self._persist(decision)
            return decision

        command_id = self._command_id(timestamp, desired)
        acknowledgement = self.command_handler(
            {
                "schema_version": 1,
                "command_id": command_id,
                "device_id": self.device_id,
                "requested_power_w": desired,
                "issued_at": now,
                "expires_at": now + 10.0,
            },
            now=now,
        )
        self.last_desired_power_w = desired
        decision = ControllerDecision(
            mode=self.mode,
            status="commanded",
            reason=reason,
            desired_power_w=desired,
            measurement_timestamp=timestamp,
            command_id=command_id,
            command_status=acknowledgement["status"],
            actual_power_w=acknowledgement["actual_power_w"],
            rejection_reason=acknowledgement.get("rejection_reason"),
        )
        self._persist(decision)
        return decision

    def fail_safe(
        self, reason: str = "measurement_unavailable", now: float | None = None
    ) -> ControllerDecision:
        """Request idle after loss of valid input; Step 4 still executes it."""
        now = time.time() if now is None else now
        if self.mode == MANUAL_MODE:
            return self._decision("inactive", "manual_mode", 0.0, now)
        if self.last_desired_power_w in {None, 0.0}:
            return self._decision("unchanged", reason, 0.0, now)
        command_id = f"step5:{self.device_id}:failsafe:{int(now * 1000)}"
        acknowledgement = self.command_handler(
            {
                "schema_version": 1,
                "command_id": command_id,
                "device_id": self.device_id,
                "requested_power_w": 0.0,
                "issued_at": now,
                "expires_at": now + 10.0,
            },
            now=now,
        )
        self.last_desired_power_w = 0.0
        decision = ControllerDecision(
            mode=self.mode,
            status="fail_safe_commanded",
            reason=reason,
            desired_power_w=0.0,
            measurement_timestamp=now,
            command_id=command_id,
            command_status=acknowledgement["status"],
            actual_power_w=acknowledgement["actual_power_w"],
            rejection_reason=acknowledgement.get("rejection_reason"),
        )
        self._persist(decision)
        return decision

    def _command_id(self, timestamp: float, desired: float) -> str:
        return f"step5:{self.device_id}:{int(timestamp * 1000)}:{int(desired * 10)}"

    def _decision(self, status, reason, desired, timestamp):
        return ControllerDecision(
            mode=self.mode,
            status=status,
            reason=reason,
            desired_power_w=desired,
            measurement_timestamp=timestamp,
        )

    def _persist(self, decision: ControllerDecision):
        self.last_decision = asdict(decision)
        if not self.state_file:
            return
        directory = os.path.dirname(os.path.abspath(self.state_file))
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(
                    {
                        "last_measurement_timestamp": self.last_measurement_timestamp,
                        "last_desired_power_w": self.last_desired_power_w,
                        "last_decision": self.last_decision,
                    },
                    handle,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.state_file)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def _load(self):
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file) as handle:
                state = json.load(handle)
            self.last_measurement_timestamp = state.get("last_measurement_timestamp")
            self.last_desired_power_w = state.get("last_desired_power_w")
            self.last_decision = state.get("last_decision")
        except (OSError, ValueError, TypeError):
            return
