"""Gateway-side orchestration for safe battery commands."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time

from iot_device.battery_contract import BatteryAcknowledgement, BatteryCommand, BatteryTelemetry


class BatteryController:
    def __init__(self, modbus_client, device_id: str, state_file: str | None = None, modbus_lock=None):
        self.modbus_client = modbus_client
        self.device_id = device_id
        self.state_file = state_file
        self.modbus_lock = modbus_lock or threading.RLock()
        self._sequence = 0
        self._acknowledgements: dict[str, dict] = {}
        self._load()

    def handle(self, payload: dict, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        command_id = str(payload.get("command_id", "unknown"))
        if command_id in self._acknowledgements:
            return dict(self._acknowledgements[command_id])
        try:
            command = BatteryCommand.from_payload(payload, self.device_id, now)
        except ValueError as error:
            return self._record(BatteryAcknowledgement(
                command_id, self.device_id, "rejected",
                _optional_float(payload.get("requested_power_w")), 0.0, now,
                str(error),
            ))

        with self.modbus_lock:
            current = self.modbus_client.read_battery_telemetry()
            if current is None:
                return self._record(self._unavailable(command, now))
            self._sequence = current["command_sequence"]
            self._sequence = (self._sequence + 1) & 0xFFFF or 1
            if not self.modbus_client.write_battery_request(command.requested_power_w, self._sequence):
                return self._record(self._unavailable(command, now))
            telemetry = self._wait_for_ack(self._sequence)
        if telemetry is None:
            return self._record(self._unavailable(command, now))
        return self._record(BatteryAcknowledgement(
            command.command_id, self.device_id, telemetry["command_status"],
            command.requested_power_w, telemetry["actual_power_w"], now,
            telemetry["rejection_reason"],
        ))

    def telemetry(self) -> BatteryTelemetry | None:
        with self.modbus_lock:
            data = self.modbus_client.read_battery_telemetry()
        if data is None:
            return None
        power = data["actual_power_w"]
        mode = "discharging" if power > 0 else "charging" if power < 0 else "idle"
        return BatteryTelemetry(
            self.device_id, power, data["requested_power_w"], data["soc_percent"],
            data["usable_capacity_kwh"], data["max_charge_power_w"],
            data["max_discharge_power_w"], data["min_soc_percent"],
            data["max_soc_percent"], data["available"], mode, data["timestamp"],
        )

    def _wait_for_ack(self, sequence: int) -> dict | None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            telemetry = self.modbus_client.read_battery_telemetry()
            if telemetry and telemetry["ack_sequence"] == sequence and telemetry["command_status"] in {"accepted", "rejected"}:
                return telemetry
            time.sleep(0.02)
        return None

    def _unavailable(self, command, now):
        return BatteryAcknowledgement(
            command.command_id, self.device_id, "rejected",
            command.requested_power_w, 0.0, now, "device_unavailable",
        )

    def _record(self, acknowledgement: BatteryAcknowledgement) -> dict:
        payload = acknowledgement.to_payload()
        self._acknowledgements[acknowledgement.command_id] = payload
        while len(self._acknowledgements) > 100:
            self._acknowledgements.pop(next(iter(self._acknowledgements)))
        self._save()
        return dict(payload)

    def _load(self):
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file) as handle:
                state = json.load(handle)
            self._sequence = state.get("sequence", 0)
            self._acknowledgements = state.get("acknowledgements", {})
        except (OSError, ValueError, TypeError):
            return

    def _save(self):
        if not self.state_file:
            return
        directory = os.path.dirname(os.path.abspath(self.state_file))
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump({"sequence": self._sequence, "acknowledgements": self._acknowledgements}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.state_file)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)


def _optional_float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
