"""Persistent event-driven power-to-energy accounting."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MAX_INTEGRATION_GAP_SECONDS = 60.0
DEFAULT_TIMEZONE = "Europe/Berlin"


@dataclass(frozen=True)
class PowerSample:
    source: str
    timestamp: float
    pv_w: float
    house_w: float
    grid_w: float
    battery_w: float


@dataclass
class DailyEnergyTotals:
    local_date: str
    timezone: str = DEFAULT_TIMEZONE
    pv_generation_wh: float = 0.0
    house_consumption_wh: float = 0.0
    grid_import_wh: float = 0.0
    grid_export_wh: float = 0.0
    battery_charge_wh: float = 0.0
    battery_discharge_wh: float = 0.0
    integrated_intervals: int = 0
    stale_intervals: int = 0
    duplicate_readings: int = 0
    out_of_order_readings: int = 0


@dataclass(frozen=True)
class AccountingResult:
    status: str
    totals: DailyEnergyTotals


class EnergyAccumulator:
    """Integrate consecutive device timestamps using the trapezoidal rule."""

    def __init__(
        self,
        state_file: str = "energy_accounting.json",
        timezone_name: str = DEFAULT_TIMEZONE,
        max_gap_seconds: float = MAX_INTEGRATION_GAP_SECONDS,
    ):
        self.state_file = state_file
        self.timezone = ZoneInfo(timezone_name)
        self.max_gap_seconds = max_gap_seconds
        self._lock = threading.RLock()
        self._latest: PowerSample | None = None
        self.totals = DailyEnergyTotals(
            local_date=self._local_date(datetime.now(timezone.utc).timestamp()),
            timezone=timezone_name,
        )
        self._load()

    def process(self, sample: PowerSample) -> AccountingResult:
        with self._lock:
            sample_date = self._local_date(sample.timestamp)
            previous = self._latest

            if previous and sample.source == previous.source:
                if sample.timestamp == previous.timestamp:
                    self.totals.duplicate_readings += 1
                    self._save()
                    return AccountingResult("duplicate", self.snapshot())
                if sample.timestamp < previous.timestamp:
                    self.totals.out_of_order_readings += 1
                    self._save()
                    return AccountingResult("out_of_order", self.snapshot())

            if previous is None or sample.source != previous.source:
                self._roll_to(sample_date)
                self._latest = sample
                self._save()
                return AccountingResult("baseline", self.snapshot())

            gap = sample.timestamp - previous.timestamp
            if gap > self.max_gap_seconds:
                self._roll_to(sample_date)
                self.totals.stale_intervals += 1
                logger.warning(
                    "[Energy] Missing/stale interval for %s: %.1fs; not integrated",
                    sample.source,
                    gap,
                )
                self._latest = sample
                self._save()
                return AccountingResult("stale", self.snapshot())

            self._integrate_interval(previous, sample)
            self._latest = sample
            self._save()
            return AccountingResult("integrated", self.snapshot())

    def snapshot(self) -> DailyEnergyTotals:
        return DailyEnergyTotals(**asdict(self.totals))

    def _integrate_interval(self, start: PowerSample, end: PowerSample) -> None:
        boundary = self._next_midnight_utc(start.timestamp)
        if boundary < end.timestamp:
            at_boundary = self._interpolate(start, end, boundary)
            if self._local_date(start.timestamp) == self.totals.local_date:
                self._add_interval(start, at_boundary)
            self._roll_to(self._local_date(end.timestamp))
            self._add_interval(at_boundary, end)
            return

        self._roll_to(self._local_date(end.timestamp))
        self._add_interval(start, end)

    def _add_interval(self, start: PowerSample, end: PowerSample) -> None:
        hours = (end.timestamp - start.timestamp) / 3600.0
        if hours <= 0:
            return

        def energy(a: float, b: float) -> float:
            return (a + b) * 0.5 * hours

        self.totals.pv_generation_wh += energy(start.pv_w, end.pv_w)
        self.totals.house_consumption_wh += energy(start.house_w, end.house_w)
        self.totals.grid_import_wh += self._positive_energy(
            start.grid_w, end.grid_w, hours
        )
        self.totals.grid_export_wh += self._positive_energy(
            -start.grid_w, -end.grid_w, hours
        )
        self.totals.battery_discharge_wh += self._positive_energy(
            start.battery_w, end.battery_w, hours
        )
        self.totals.battery_charge_wh += self._positive_energy(
            -start.battery_w, -end.battery_w, hours
        )
        self.totals.integrated_intervals += 1

    def _roll_to(self, local_date: str) -> None:
        if self.totals.local_date != local_date:
            self.totals = DailyEnergyTotals(
                local_date=local_date,
                timezone=self.timezone.key,
            )

    def _local_date(self, timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(
            self.timezone
        ).date().isoformat()

    def _next_midnight_utc(self, timestamp: float) -> float:
        local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(
            self.timezone
        )
        next_date = local.date().fromordinal(local.date().toordinal() + 1)
        return datetime.combine(next_date, time.min, self.timezone).timestamp()

    @staticmethod
    def _positive_energy(start_w: float, end_w: float, hours: float) -> float:
        """Integrate only the positive part of a linearly changing signal."""
        if start_w >= 0 and end_w >= 0:
            return (start_w + end_w) * 0.5 * hours
        if start_w <= 0 and end_w <= 0:
            return 0.0
        positive = max(start_w, end_w)
        positive_fraction = positive / abs(end_w - start_w)
        return positive * 0.5 * hours * positive_fraction

    @staticmethod
    def _interpolate(start: PowerSample, end: PowerSample, timestamp: float) -> PowerSample:
        ratio = (timestamp - start.timestamp) / (end.timestamp - start.timestamp)

        def value(a: float, b: float) -> float:
            return a + (b - a) * ratio

        return PowerSample(
            source=start.source,
            timestamp=timestamp,
            pv_w=value(start.pv_w, end.pv_w),
            house_w=value(start.house_w, end.house_w),
            grid_w=value(start.grid_w, end.grid_w),
            battery_w=value(start.battery_w, end.battery_w),
        )

    def _load(self) -> None:
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file) as handle:
                state = json.load(handle)
            self.totals = DailyEnergyTotals(**state["totals"])
            if state.get("latest"):
                self._latest = PowerSample(**state["latest"])
        except (OSError, ValueError, TypeError, KeyError) as error:
            logger.error("[Energy] State load failed: %s", error)

    def _save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.state_file))
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(
                    {
                        "totals": asdict(self.totals),
                        "latest": asdict(self._latest) if self._latest else None,
                    },
                    handle,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.state_file)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
