"""Frozen, deterministic no-control profile for Step 3."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import exp, pi, sin
from zoneinfo import ZoneInfo

SCENARIO_NAME = "baseline_no_control_v1"
SCENARIO_DATE = date(2026, 6, 15)
SCENARIO_TIMEZONE = "Europe/Berlin"
INTERVAL_MINUTES = 5
EXPECTED_READING_COUNT = 289


@dataclass(frozen=True)
class BaselineReading:
    timestamp: float
    local_time: str
    pv_w: float
    house_w: float
    battery_w: float = 0.0

    @property
    def grid_w(self) -> float:
        return self.house_w - self.pv_w


def pv_power_w(hour: float) -> float:
    """Clear-sky-like 6 kW PV curve between 06:00 and 20:00."""
    if hour <= 6 or hour >= 20:
        return 0.0
    return round(6000 * sin(pi * (hour - 6) / 14), 1)


def household_power_w(hour: float) -> float:
    """Deterministic base load with morning and evening demand peaks."""
    base = 450.0
    morning = 1700 * exp(-0.5 * ((hour - 7.75) / 0.9) ** 2)
    daytime = 350 * exp(-0.5 * ((hour - 13.0) / 2.3) ** 2)
    evening = 2300 * exp(-0.5 * ((hour - 19.0) / 1.4) ** 2)
    return round(base + morning + daytime + evening, 1)


def baseline_readings() -> tuple[BaselineReading, ...]:
    zone = ZoneInfo(SCENARIO_TIMEZONE)
    start = datetime.combine(SCENARIO_DATE, time.min, zone)
    readings = []
    for index in range(EXPECTED_READING_COUNT):
        local = start + timedelta(minutes=index * INTERVAL_MINUTES)
        hour = local.hour + local.minute / 60
        readings.append(
            BaselineReading(
                timestamp=local.astimezone(timezone.utc).timestamp(),
                local_time=local.isoformat(),
                pv_w=pv_power_w(hour),
                house_w=household_power_w(hour),
            )
        )
    return tuple(readings)
