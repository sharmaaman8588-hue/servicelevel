"""Seeded generator of half-hour contact-centre interval data.

This is a parametric generator, not a queue simulation. For every queue and half hour it

1. draws offered calls from a Poisson around an intraday double-peak profile (Monday heaviest);
2. builds a roster from the Erlang requirement at the *expected* volume, hour by hour, and takes a
   small random number of agents off it to get the logged-in headcount;
3. draws the outcomes of each call from Erlang C at the logged-in headcount, with a patience model
   in which the share of delayed calls that abandon is ASA / (ASA + patience), capped.

Planted patterns (the tests recover each of them):

* billing is rostered 3 agents below the Erlang requirement on Tuesdays and Wednesdays,
  11:00-14:00, and its overnight intervals carry 1-4 calls that are all answered within the
  threshold, which is what lets an average of interval percentages flatter the queue;
* technical has an IVR misroute that makes 10% of offered calls hang up within 10 seconds;
* sales is rostered 2 agents above the requirement, so abandonment stays very low.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from servicelevel import erlang

RAW_COLUMNS = [
    "queue",
    "interval_start",
    "offered",
    "answered",
    "answered_within_t",
    "abandoned",
    "abandoned_within_t",
    "handle_seconds",
    "wait_seconds",
    "rostered",
    "logged_in",
]
INTERVALS_PER_DAY = 48
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 7
MISROUTE_HANGUP_SECONDS = 10
CAPACITY_CEILING = 0.97
ASA_CAP_SECONDS = 600.0
DEFAULT_SEED = 42


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    text = resources.files("servicelevel").joinpath("queues.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def is_night(hour: float) -> bool:
    return hour < NIGHT_END_HOUR or hour >= NIGHT_START_HOUR


def day_profile(hour: float) -> float:
    """Shape of daytime demand, 1.0 at the late-morning peak, with an afternoon second peak."""
    morning = math.exp(-0.5 * ((hour - 11.5) / 1.3) ** 2)
    afternoon = 0.85 * math.exp(-0.5 * ((hour - 15.5) / 1.4) ** 2)
    return 0.06 + morning + afternoon


_PEAK_NORMALISER = max(day_profile(7 + k / 2) for k in range(30))


def expected_calls(q: dict[str, Any], dow_multiplier: float, hour: float) -> float:
    if is_night(hour):
        return float(q["night_calls"])
    shaped = q["peak_calls"] * dow_multiplier * day_profile(hour) / _PEAK_NORMALISER
    return max(float(q["night_calls"]), shaped)


def _roster_for_day(name: str, q: dict[str, Any], cfg: dict[str, Any], weekday: int) -> list[int]:
    """Rostered agents for the 48 intervals of one day (hourly blocks)."""
    interval_s = cfg["interval_seconds"]
    required = []
    for k in range(INTERVALS_PER_DAY):
        hour = k / 2
        lam = expected_calls(q, cfg["day_of_week_multiplier"][weekday], hour)
        a = erlang.traffic_erlangs(lam, q["aht_seconds"], interval_s)
        required.append(
            erlang.required_agents(
                a,
                q["aht_seconds"],
                cfg["interval_target"],
                cfg["threshold_seconds"],
                cfg["max_occupancy"],
            )
        )
    roster = []
    for k in range(INTERVALS_PER_DAY):
        block = max(required[k - k % 2], required[k - k % 2 + 1])
        base = block + q["roster_offset"]
        if is_night(k / 2):
            base = max(base, q["night_team"])
        roster.append(max(1, base))
    plant = cfg["billing_understaffing"]
    if name == "billing" and weekday in plant["weekdays"]:
        for k in range(INTERVALS_PER_DAY):
            if plant["start_hour"] <= k / 2 < plant["end_hour"]:
                roster[k] = max(1, required[k] - plant["agents_below_requirement"])
    return roster


def _draw_interval(
    rng: np.random.Generator,
    name: str,
    cfg: dict[str, Any],
    offered: int,
    aht: float,
    logged_in: int,
    hour: float,
) -> dict[str, int]:
    interval_s = cfg["interval_seconds"]
    t = cfg["threshold_seconds"]

    if name == "billing" and is_night(hour):
        wait = int(round(offered * rng.uniform(0.0, 4.0)))
        return {
            "answered": offered,
            "answered_within_t": offered,
            "abandoned": 0,
            "abandoned_within_t": 0,
            "handle_seconds": int(round(offered * aht)),
            "wait_seconds": wait,
        }

    hangups = 0
    if name == "technical":
        hangups = int(rng.binomial(offered, cfg["technical_misroute_share"]))
    flow = offered - hangups

    a = erlang.traffic_erlangs(offered, aht, interval_s)
    c = erlang.erlang_c(logged_in, a)
    sl = erlang.service_level(logged_in, a, aht, t)
    asa = min(erlang.average_speed_of_answer(logged_in, a, aht), ASA_CAP_SECONDS)
    abandon_share = min(cfg["abandon_cap"], asa / (asa + cfg["patience_seconds"]))
    p_abandon = c * abandon_share
    share_within_t = 1.0 if asa <= t else t / asa

    abandoned_flow = int(rng.binomial(flow, p_abandon))
    abandoned_within = int(rng.binomial(abandoned_flow, share_within_t))
    answered = flow - abandoned_flow
    p_within = min(1.0, sl / max(1e-9, 1.0 - p_abandon))
    within = int(rng.binomial(answered, p_within))

    # An agent cannot handle more than their paid time: the excess hangs up after the threshold.
    max_answered = int(math.floor(CAPACITY_CEILING * logged_in * interval_s / aht))
    excess = max(0, answered - max_answered)
    answered -= excess
    within = min(within, answered)

    wait = int(round(answered * asa * rng.uniform(0.9, 1.1)))
    return {
        "answered": answered,
        "answered_within_t": within,
        "abandoned": abandoned_flow + excess + hangups,
        "abandoned_within_t": abandoned_within + hangups,
        "handle_seconds": int(round(answered * aht)),
        "wait_seconds": wait,
    }


def generate(seed: int = DEFAULT_SEED) -> pd.DataFrame:
    """Return the raw interval table: 3 queues x 8 weeks x 48 intervals = 8,064 rows."""
    cfg = load_config()
    rng = np.random.default_rng(seed)
    start = date.fromisoformat(cfg["start_date"])
    absence_p = cfg["absence_probabilities"]
    rows: list[dict[str, Any]] = []
    for name, q in cfg["queues"].items():
        for day_offset in range(cfg["weeks"] * 7):
            day = start + timedelta(days=day_offset)
            weekday = day.weekday()
            roster = _roster_for_day(name, q, cfg, weekday)
            for k in range(INTERVALS_PER_DAY):
                hour = k / 2
                lam = expected_calls(q, cfg["day_of_week_multiplier"][weekday], hour)
                if name == "billing" and is_night(hour):
                    offered = int(rng.integers(1, 5))
                else:
                    offered = max(1, int(rng.poisson(lam)))
                aht = max(60.0, q["aht_seconds"] * float(rng.lognormal(0.0, q["aht_noise"])))
                absent = int(rng.choice(len(absence_p), p=absence_p))
                logged_in = max(1, roster[k] - absent)
                outcome = _draw_interval(rng, name, cfg, offered, aht, logged_in, hour)
                rows.append(
                    {
                        "queue": name,
                        "interval_start": datetime(day.year, day.month, day.day)
                        + timedelta(minutes=30 * k),
                        "offered": offered,
                        **outcome,
                        "rostered": roster[k],
                        "logged_in": logged_in,
                    }
                )
    return pd.DataFrame(rows, columns=RAW_COLUMNS)


def write_raw(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["interval_start"] = out["interval_start"].dt.strftime("%Y-%m-%dT%H:%M")
    out.to_csv(path, index=False)


def read_raw(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["interval_start"])
    return df[RAW_COLUMNS]
