"""Persistent, command-driven battery model with local safety enforcement."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass


@dataclass
class BatteryState:
    soc_percent: float = 50.0
    usable_capacity_kwh: float = 10.0
    max_charge_power_w: float = 3000.0
    max_discharge_power_w: float = 3000.0
    min_soc_percent: float = 10.0
    max_soc_percent: float = 90.0
    requested_power_w: float = 0.0
    actual_power_w: float = 0.0
    available: bool = True
    latest_command_id: str | None = None


class BatteryDevice:
    """The simulated battery is the final authority on safe power."""

    def __init__(self, state_file: str | None = None, **overrides):
        self.state_file = state_file
        self._lock = threading.RLock()
        self.state = BatteryState(**overrides)
        self._last_ack: dict | None = None
        self._load()

    def command(self, command_id: str, requested_power_w: float) -> dict:
        with self._lock:
            if command_id == self.state.latest_command_id and self._last_ack:
                return dict(self._last_ack)
            reason = self._rejection_reason(requested_power_w)
            self.state.latest_command_id = command_id
            if reason:
                acknowledgement = self._ack(
                    command_id, "rejected", requested_power_w, reason
                )
            else:
                self.state.requested_power_w = requested_power_w
                self.state.actual_power_w = requested_power_w
                acknowledgement = self._ack(
                    command_id, "accepted", requested_power_w, None
                )
            self._last_ack = acknowledgement
            self._save()
            return dict(acknowledgement)

    def advance(self, seconds: float) -> None:
        with self._lock:
            if seconds <= 0:
                return
            if not self.state.available:
                self.state.actual_power_w = 0.0
                return
            energy_kwh = self.state.actual_power_w * seconds / 3_600_000
            next_soc = self.state.soc_percent - (
                energy_kwh / self.state.usable_capacity_kwh * 100
            )
            if next_soc <= self.state.min_soc_percent:
                self.state.soc_percent = self.state.min_soc_percent
                if self.state.actual_power_w > 0:
                    self.state.actual_power_w = 0.0
                    self.state.requested_power_w = 0.0
            elif next_soc >= self.state.max_soc_percent:
                self.state.soc_percent = self.state.max_soc_percent
                if self.state.actual_power_w < 0:
                    self.state.actual_power_w = 0.0
                    self.state.requested_power_w = 0.0
            else:
                self.state.soc_percent = next_soc
            self._save()

    def set_available(self, available: bool) -> None:
        with self._lock:
            self.state.available = available
            if not available:
                self.state.actual_power_w = 0.0
                self.state.requested_power_w = 0.0
            self._save()

    def snapshot(self) -> dict:
        with self._lock:
            return asdict(self.state)

    def _rejection_reason(self, requested: float) -> str | None:
        if not self.state.available:
            return "device_unavailable"
        if requested < -self.state.max_charge_power_w:
            return "charge_power_limit_exceeded"
        if requested > self.state.max_discharge_power_w:
            return "discharge_power_limit_exceeded"
        if requested < 0 and self.state.soc_percent >= self.state.max_soc_percent:
            return "maximum_soc_reached"
        if requested > 0 and self.state.soc_percent <= self.state.min_soc_percent:
            return "minimum_soc_reached"
        return None

    def _ack(self, command_id, status, requested, reason) -> dict:
        return {
            "command_id": command_id,
            "status": status,
            "requested_power_w": requested,
            "actual_power_w": self.state.actual_power_w,
            "rejection_reason": reason,
        }

    def _load(self) -> None:
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file) as handle:
                payload = json.load(handle)
            self.state = BatteryState(**payload["state"])
            self._last_ack = payload.get("last_ack")
        except (OSError, ValueError, TypeError, KeyError):
            return

    def _save(self) -> None:
        if not self.state_file:
            return
        directory = os.path.dirname(os.path.abspath(self.state_file))
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(
                    {"state": asdict(self.state), "last_ack": self._last_ack},
                    handle,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.state_file)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
