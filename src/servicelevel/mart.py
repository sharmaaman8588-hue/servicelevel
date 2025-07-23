"""Build the DuckDB reference mart and export the CSVs that Power BI loads.

Pipeline: raw half-hour counts -> required agents (Erlang C, in Python) -> ``raw_interval`` table
-> the views in ``sql/`` -> ``Interval.csv`` plus the three small dimension tables.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd

from servicelevel import erlang
from servicelevel.synth import RAW_COLUMNS, load_config

MART_FILES = ("Interval.csv", "Queue.csv", "Calendar.csv", "TimeOfDay.csv")
INTERVAL_EXPORT_COLUMNS = [
    "queue",
    "interval_start",
    "date",
    "interval_idx",
    "offered",
    "answered",
    "answered_within_t",
    "abandoned",
    "abandoned_within_t",
    "handle_seconds",
    "wait_seconds",
    "rostered",
    "logged_in",
    "required_agents",
    "staffing_gap",
    "sl1_pct",
]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def sql_dir() -> Path:
    """Directory holding the numbered SQL files (repo root, or packaged next to the module)."""
    packaged = Path(__file__).resolve().parent / "sql"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "sql"


def interval_aht(raw: pd.DataFrame) -> pd.Series:
    """Realised AHT per interval; intervals with no answered call fall back to the queue mean."""
    answered = raw["answered"].where(raw["answered"] > 0)
    own = raw["handle_seconds"] / answered
    totals = raw.groupby("queue")[["handle_seconds", "answered"]].transform("sum")
    queue_mean = totals["handle_seconds"] / totals["answered"]
    return own.fillna(queue_mean)


def with_required_agents(
    raw: pd.DataFrame,
    target: float | None = None,
    threshold_seconds: float | None = None,
    max_occupancy: float | None = None,
) -> pd.DataFrame:
    """Add ``required_agents``: Erlang C at the interval's actual offered calls and AHT."""
    cfg = load_config()
    target = cfg["interval_target"] if target is None else target
    threshold_seconds = cfg["threshold_seconds"] if threshold_seconds is None else threshold_seconds
    max_occupancy = cfg["max_occupancy"] if max_occupancy is None else max_occupancy
    interval_s = cfg["interval_seconds"]
    aht = interval_aht(raw)
    required = [
        erlang.required_agents(
            erlang.traffic_erlangs(calls, h, interval_s),
            h,
            target,
            threshold_seconds,
            max_occupancy,
        )
        for calls, h in zip(raw["offered"], aht, strict=True)
    ]
    out = raw.copy()
    out["required_agents"] = required
    return out


def open_mart(interval: pd.DataFrame) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with every mart view created from a table with ``required_agents``."""
    columns = [*RAW_COLUMNS, "required_agents"]
    con = duckdb.connect(":memory:")
    con.register("raw_interval", interval[columns])
    for path in sorted(sql_dir().glob("*.sql")):
        con.execute(path.read_text(encoding="utf-8"))
    return con


def build(raw: pd.DataFrame) -> duckdb.DuckDBPyConnection:
    """Raw counts in, populated mart out."""
    return open_mart(with_required_agents(raw))


def read_mart_csv(data_dir: Path) -> pd.DataFrame:
    """Re-open a mart from the exported ``Interval.csv`` (counts only; ratios are recomputed)."""
    df = pd.read_csv(data_dir / "Interval.csv", parse_dates=["interval_start"])
    return df[[*RAW_COLUMNS, "required_agents"]]


def open_from_dir(data_dir: Path) -> duckdb.DuckDBPyConnection:
    return open_mart(read_mart_csv(data_dir))


def calendar_frame(dates: pd.Series) -> pd.DataFrame:
    days = pd.Series(sorted(pd.to_datetime(dates).dt.normalize().unique()))
    iso = days.dt.isocalendar()
    return pd.DataFrame(
        {
            "date": days.dt.strftime("%Y-%m-%d"),
            "week": iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2),
            "week_start": (
                days - days.dt.day_of_week.map(lambda d: timedelta(days=int(d)))
            ).dt.strftime("%Y-%m-%d"),
            "weekday_num": days.dt.day_of_week + 1,
            "weekday_name": days.dt.day_of_week.map(lambda d: WEEKDAYS[int(d)]),
        }
    )


def time_of_day_frame() -> pd.DataFrame:
    rows = []
    for idx in range(48):
        hour, half = divmod(idx, 2)
        if hour < 7 or hour >= 22:
            part = "Overnight"
        elif hour < 12:
            part = "Morning"
        elif hour < 17:
            part = "Afternoon"
        else:
            part = "Evening"
        rows.append(
            {
                "interval_idx": idx,
                "time_label": f"{hour:02d}:{30 * half:02d}",
                "hour": hour,
                "daypart": part,
            }
        )
    return pd.DataFrame(rows)


def queue_frame(queues: list[str]) -> pd.DataFrame:
    cfg = load_config()
    return pd.DataFrame(
        {
            "queue": queues,
            "queue_label": [q.capitalize() for q in queues],
            "sl_target": cfg["interval_target"],
            "threshold_seconds": cfg["threshold_seconds"],
        }
    )


def export_mart(con: duckdb.DuckDBPyConnection, out_dir: Path) -> dict[str, int]:
    """Write the four CSVs the semantic model loads; returns file name -> bytes written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    interval = con.execute(
        """
        SELECT queue,
               strftime(interval_start, '%Y-%m-%dT%H:%M') AS interval_start,
               strftime(interval_date, '%Y-%m-%d')        AS date,
               interval_idx, offered, answered, answered_within_t, abandoned,
               abandoned_within_t, handle_seconds, wait_seconds, rostered, logged_in,
               required_agents, staffing_gap,
               round(sl1_pct, 4)                          AS sl1_pct
        FROM fct_interval_staffing
        ORDER BY queue, interval_start
        """
    ).df()
    interval = interval[INTERVAL_EXPORT_COLUMNS]
    dates = pd.to_datetime(interval["date"])
    present = set(interval["queue"])
    queues = [q for q in load_config()["queues"] if q in present]
    frames = {
        "Interval.csv": interval,
        "Queue.csv": queue_frame(queues),
        "Calendar.csv": calendar_frame(dates),
        "TimeOfDay.csv": time_of_day_frame(),
    }
    sizes = {}
    for name, frame in frames.items():
        path = out_dir / name
        frame.to_csv(path, index=False, lineterminator="\n")
        sizes[name] = path.stat().st_size
    return sizes
