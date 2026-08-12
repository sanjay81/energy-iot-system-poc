"""Persistent event-driven power-to-energy accounting."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MAX_INTEGRATION_GAP_SECONDS = 60.0
DEFAULT_TIMEZONE = "Europe/Berlin"
STATE_SCHEMA_VERSION = 2


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


@dataclass
class DailyEnergyFlows:
    pv_direct_to_house_wh: float = 0.0
    pv_to_battery_wh: float = 0.0
    grid_to_battery_wh: float = 0.0
    pv_battery_to_house_wh: float = 0.0
    grid_battery_to_house_wh: float = 0.0
    unknown_battery_to_house_wh: float = 0.0


@dataclass
class BatteryProvenance:
    pv_origin_wh: float = 0.0
    grid_origin_wh: float = 0.0
    unknown_origin_wh: float = 0.0


@dataclass(frozen=True)
class DailyEnergyKPIs:
    self_consumption_percent: float | None
    self_consumption_status: str
    self_sufficiency_percent: float | None
    self_sufficiency_status: str
    peak_grid_demand_w: float
    net_grid_energy_wh: float
    battery_throughput_wh: float
    energy_balance_error_wh: float
    energy_balance_error_percent: float


@dataclass(frozen=True)
class AccountingResult:
    status: str
    totals: DailyEnergyTotals
    flows: DailyEnergyFlows
    battery_provenance: BatteryProvenance
    kpis: DailyEnergyKPIs
    completed_day: dict | None = None


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
        self.flows = DailyEnergyFlows()
        self.battery_provenance = BatteryProvenance()
        self.peak_grid_demand_w = 0.0
        self.completed_day: dict | None = None
        self._load()

    def process(self, sample: PowerSample) -> AccountingResult:
        with self._lock:
            # A completed day is emitted once, on the reading that crosses
            # local midnight; it is not repeated on later measurements.
            self.completed_day = None
            sample_date = self._local_date(sample.timestamp)
            previous = self._latest

            if previous and sample.source == previous.source:
                if sample.timestamp == previous.timestamp:
                    self.totals.duplicate_readings += 1
                    self._save()
                    return self._result("duplicate")
                if sample.timestamp < previous.timestamp:
                    self.totals.out_of_order_readings += 1
                    self._save()
                    return self._result("out_of_order")

            if previous is None or sample.source != previous.source:
                self._roll_to(sample_date)
                self._observe_peak(sample)
                self._latest = sample
                self._save()
                return self._result("baseline")

            gap = sample.timestamp - previous.timestamp
            if gap > self.max_gap_seconds:
                self._roll_to(sample_date)
                self.totals.stale_intervals += 1
                self._observe_peak(sample)
                logger.warning(
                    "[Energy] Missing/stale interval for %s: %.1fs; not integrated",
                    sample.source,
                    gap,
                )
                self._latest = sample
                self._save()
                return self._result("stale")

            self._integrate_interval(previous, sample)
            self._latest = sample
            self._save()
            return self._result("integrated")

    def snapshot(self) -> DailyEnergyTotals:
        return DailyEnergyTotals(**asdict(self.totals))

    def _result(self, status: str) -> AccountingResult:
        return AccountingResult(
            status=status,
            totals=self.snapshot(),
            flows=DailyEnergyFlows(**asdict(self.flows)),
            battery_provenance=BatteryProvenance(
                **asdict(self.battery_provenance)
            ),
            kpis=self._calculate_kpis(),
            completed_day=self.completed_day,
        )

    def _integrate_interval(self, start: PowerSample, end: PowerSample) -> None:
        boundary = self._next_midnight_utc(start.timestamp)
        if boundary <= end.timestamp:
            at_boundary = self._interpolate(start, end, boundary)
            if self._local_date(start.timestamp) == self.totals.local_date:
                self._add_interval(start, at_boundary)
            self._roll_to(self._local_date(end.timestamp))
            if boundary < end.timestamp:
                self._add_interval(at_boundary, end)
            return

        self._roll_to(self._local_date(end.timestamp))
        self._add_interval(start, end)

    def _add_interval(self, start: PowerSample, end: PowerSample) -> None:
        split_ratios = {0.0, 1.0}
        for start_value, end_value in (
            (start.battery_w, end.battery_w),
            (start.pv_w - start.house_w, end.pv_w - end.house_w),
        ):
            if start_value * end_value < 0:
                split_ratios.add(
                    abs(start_value) / abs(end_value - start_value)
                )

        points = [
            self._interpolate(
                start,
                end,
                start.timestamp + ratio * (end.timestamp - start.timestamp),
            )
            for ratio in sorted(split_ratios)
        ]
        for segment_start, segment_end in zip(points, points[1:]):
            self._add_segment(segment_start, segment_end)

        self._observe_peak(start)
        self._observe_peak(end)
        self.totals.integrated_intervals += 1

    def _add_segment(self, start: PowerSample, end: PowerSample) -> None:
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
        battery_discharge_wh = self._positive_energy(
            start.battery_w, end.battery_w, hours
        )
        battery_charge_wh = self._positive_energy(
            -start.battery_w, -end.battery_w, hours
        )
        self.totals.battery_discharge_wh += battery_discharge_wh
        self.totals.battery_charge_wh += battery_charge_wh

        start_flows = self._flow_powers(start)
        end_flows = self._flow_powers(end)
        pv_direct_wh = energy(start_flows[0], end_flows[0])
        pv_to_battery_wh = energy(start_flows[1], end_flows[1])
        grid_to_battery_wh = energy(start_flows[2], end_flows[2])
        battery_to_house_wh = energy(start_flows[3], end_flows[3])

        self.flows.pv_direct_to_house_wh += pv_direct_wh
        self.flows.pv_to_battery_wh += pv_to_battery_wh
        self.flows.grid_to_battery_wh += grid_to_battery_wh

        if start.battery_w < 0:
            self._store_charge(pv_to_battery_wh, grid_to_battery_wh)
            origin = self._withdraw_discharge(battery_discharge_wh)
        else:
            origin = self._withdraw_discharge(battery_discharge_wh)
            self._store_charge(pv_to_battery_wh, grid_to_battery_wh)

        if battery_discharge_wh > 0:
            house_fraction = min(
                battery_to_house_wh / battery_discharge_wh, 1.0
            )
            self.flows.pv_battery_to_house_wh += origin[0] * house_fraction
            self.flows.grid_battery_to_house_wh += origin[1] * house_fraction
            self.flows.unknown_battery_to_house_wh += (
                origin[2] * house_fraction
            )


    @staticmethod
    def _flow_powers(sample: PowerSample) -> tuple[float, float, float, float]:
        charge_w = max(-sample.battery_w, 0.0)
        discharge_w = max(sample.battery_w, 0.0)
        pv_direct_w = min(max(sample.pv_w, 0.0), max(sample.house_w, 0.0))
        pv_surplus_w = max(sample.pv_w - sample.house_w, 0.0)
        pv_to_battery_w = min(charge_w, pv_surplus_w)
        grid_to_battery_w = max(charge_w - pv_to_battery_w, 0.0)
        remaining_house_w = max(sample.house_w - pv_direct_w, 0.0)
        battery_to_house_w = min(discharge_w, remaining_house_w)
        return (
            pv_direct_w,
            pv_to_battery_w,
            grid_to_battery_w,
            battery_to_house_w,
        )

    def _store_charge(self, pv_wh: float, grid_wh: float) -> None:
        self.battery_provenance.pv_origin_wh += pv_wh
        self.battery_provenance.grid_origin_wh += grid_wh

    def _withdraw_discharge(self, discharge_wh: float) -> tuple[float, float, float]:
        if discharge_wh <= 0:
            return (0.0, 0.0, 0.0)

        stored = (
            self.battery_provenance.pv_origin_wh
            + self.battery_provenance.grid_origin_wh
            + self.battery_provenance.unknown_origin_wh
        )
        if stored <= 0:
            return (0.0, 0.0, discharge_wh)

        withdrawn = min(discharge_wh, stored)
        pv_wh = withdrawn * self.battery_provenance.pv_origin_wh / stored
        grid_wh = withdrawn * self.battery_provenance.grid_origin_wh / stored
        unknown_wh = withdrawn * self.battery_provenance.unknown_origin_wh / stored
        implicit_unknown_wh = discharge_wh - withdrawn

        self.battery_provenance.pv_origin_wh = max(
            self.battery_provenance.pv_origin_wh - pv_wh, 0.0
        )
        self.battery_provenance.grid_origin_wh = max(
            self.battery_provenance.grid_origin_wh - grid_wh, 0.0
        )
        self.battery_provenance.unknown_origin_wh = max(
            self.battery_provenance.unknown_origin_wh - unknown_wh, 0.0
        )
        return (pv_wh, grid_wh, unknown_wh + implicit_unknown_wh)

    def _observe_peak(self, sample: PowerSample) -> None:
        self.peak_grid_demand_w = max(
            self.peak_grid_demand_w, sample.grid_w, 0.0
        )

    def _calculate_kpis(self) -> DailyEnergyKPIs:
        if self.totals.pv_generation_wh > 0:
            pv_used_locally = (
                self.flows.pv_direct_to_house_wh
                + self.flows.pv_to_battery_wh
            )
            self_consumption = self._bounded_percent(
                pv_used_locally / self.totals.pv_generation_wh * 100
            )
            self_consumption_status = "available"
        else:
            self_consumption = None
            self_consumption_status = "not_applicable:no_pv_generation"

        if self.totals.house_consumption_wh > 0:
            locally_supplied = (
                self.flows.pv_direct_to_house_wh
                + self.flows.pv_battery_to_house_wh
            )
            self_sufficiency = self._bounded_percent(
                locally_supplied / self.totals.house_consumption_wh * 100
            )
            self_sufficiency_status = "available"
        else:
            self_sufficiency = None
            self_sufficiency_status = "not_applicable:no_house_consumption"

        entering = (
            self.totals.pv_generation_wh
            + self.totals.grid_import_wh
            + self.totals.battery_discharge_wh
        )
        leaving = (
            self.totals.house_consumption_wh
            + self.totals.grid_export_wh
            + self.totals.battery_charge_wh
        )
        balance_error = entering - leaving
        denominator = max(entering, leaving)
        balance_percent = (
            abs(balance_error) / denominator * 100 if denominator else 0.0
        )

        return DailyEnergyKPIs(
            self_consumption_percent=self_consumption,
            self_consumption_status=self_consumption_status,
            self_sufficiency_percent=self_sufficiency,
            self_sufficiency_status=self_sufficiency_status,
            peak_grid_demand_w=self.peak_grid_demand_w,
            net_grid_energy_wh=(
                self.totals.grid_import_wh - self.totals.grid_export_wh
            ),
            battery_throughput_wh=(
                self.totals.battery_charge_wh
                + self.totals.battery_discharge_wh
            ),
            energy_balance_error_wh=balance_error,
            energy_balance_error_percent=balance_percent,
        )

    @staticmethod
    def _bounded_percent(value: float) -> float:
        return min(max(value, 0.0), 100.0)

    def _roll_to(self, local_date: str) -> None:
        if self.totals.local_date != local_date:
            self.completed_day = {
                "totals": asdict(self.totals),
                "flows": asdict(self.flows),
                "kpis": asdict(self._calculate_kpis()),
            }
            self.totals = DailyEnergyTotals(
                local_date=local_date,
                timezone=self.timezone.key,
            )
            self.flows = DailyEnergyFlows()
            self.peak_grid_demand_w = 0.0

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
            if state.get("latest"):
                self._latest = PowerSample(**state["latest"])
            if state.get("schema_version", 1) < STATE_SCHEMA_VERSION:
                legacy = state["totals"]
                self.totals = DailyEnergyTotals(
                    local_date=legacy["local_date"],
                    timezone=legacy.get("timezone", self.timezone.key),
                )
                logger.warning(
                    "[Energy] Reset daily totals while migrating accounting "
                    "state to schema %s; legacy totals have no flow provenance",
                    STATE_SCHEMA_VERSION,
                )
                return
            self.totals = DailyEnergyTotals(**state["totals"])
            self.flows = DailyEnergyFlows(**state.get("flows", {}))
            self.battery_provenance = BatteryProvenance(
                **state.get("battery_provenance", {})
            )
            self.peak_grid_demand_w = state.get("peak_grid_demand_w", 0.0)
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
                        "schema_version": STATE_SCHEMA_VERSION,
                        "totals": asdict(self.totals),
                        "flows": asdict(self.flows),
                        "battery_provenance": asdict(
                            self.battery_provenance
                        ),
                        "peak_grid_demand_w": self.peak_grid_demand_w,
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
