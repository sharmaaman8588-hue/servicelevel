"""Command line: generate, build, sl, staffing, erlang and validate-pbip."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer
from rich import box
from rich.console import Console
from rich.table import Table

from servicelevel import erlang, mart, pbip, pbip_validate, synth

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Contact-centre service level as a ratio of sums, and an Erlang C staffing gap.",
)
console = Console()
if not console.is_terminal:  # piped output: keep wide tables on one line
    console.width = max(console.width, 110)

DataDir = Annotated[Path, typer.Option("--data", help="Folder holding the exported mart CSVs.")]
DEFAULT_MART = Path("data/mart")
DEFAULT_RAW = Path("data/raw/interval.csv")


def _pct(value: float | None) -> str:
    return "-" if value is None or pd.isna(value) else f"{100 * value:.1f}%"


def _num(value: float | None, digits: int = 1) -> str:
    return "-" if value is None or pd.isna(value) else f"{value:.{digits}f}"


def _open(data: Path):
    if not (data / "Interval.csv").is_file():
        console.print(f"[red]No {data / 'Interval.csv'}; run `servicelevel build` first.[/red]")
        raise typer.Exit(1)
    return mart.open_from_dir(data)


@app.command()
def generate(
    seed: Annotated[
        int, typer.Option(help="Seed of the synthetic generator.")
    ] = synth.DEFAULT_SEED,
    out: Annotated[Path, typer.Option(help="Raw interval CSV to write.")] = DEFAULT_RAW,
) -> None:
    """Write the seeded synthetic half-hour interval data."""
    df = synth.generate(seed)
    synth.write_raw(df, out)
    console.print(f"wrote {len(df):,} intervals to {out}")


@app.command()
def build(
    raw: Annotated[Path, typer.Option(help="Raw interval CSV from `generate`.")] = DEFAULT_RAW,
    out: Annotated[Path, typer.Option(help="Folder for the exported mart CSVs.")] = DEFAULT_MART,
    pbip_dir: Annotated[
        Path, typer.Option("--pbip-dir", help="Folder to write the PBIP project into.")
    ] = Path("powerbi"),
) -> None:
    """Build the DuckDB mart, export the CSVs and rewrite the Power BI project."""
    if not raw.is_file():
        console.print(f"[red]{raw} not found; run `servicelevel generate` first.[/red]")
        raise typer.Exit(1)
    con = mart.build(synth.read_raw(raw))
    sizes = mart.export_mart(con, out)
    for name, size in sizes.items():
        console.print(f"wrote {out / name} ({size:,} bytes)")
    bad = con.execute(
        "SELECT count(*) FROM fct_interval_service WHERE NOT is_consistent"
    ).fetchone()
    if bad and bad[0]:
        console.print(f"[red]{bad[0]} intervals fail the integrity check[/red]")
        raise typer.Exit(1)
    written = pbip.write_project(pbip_dir)
    console.print(f"wrote {len(written)} Power BI project files under {pbip_dir}")


def _summary_table(title: str, rows: pd.DataFrame, label: str = "Queue") -> Table:
    table = Table(title=title, box=box.SIMPLE_HEAD)
    table.add_column(label)
    for col in (
        "Offered",
        "SL1",
        "SL2",
        "SL3",
        "SL4",
        "SL1 interval mean",
        "Abandon",
        "ASA (s)",
        "Occupancy",
    ):
        table.add_column(col, justify="right")
    for r in rows.itertuples():
        table.add_row(
            str(r[1]),
            f"{int(r.offered):,}",
            _pct(r.sl1_pct),
            _pct(r.sl2_pct),
            _pct(r.sl3_pct),
            _pct(r.sl4_pct),
            _pct(r.sl1_interval_mean),
            _pct(r.abandon_pct),
            _num(r.asa_s),
            _pct(r.occupancy_pct),
        )
    return table


@app.command()
def sl(
    queue: Annotated[str | None, typer.Option(help="Queue name, e.g. billing.")] = None,
    week: Annotated[str | None, typer.Option(help="ISO week, e.g. 2025-W10.")] = None,
    data: DataDir = DEFAULT_MART,
) -> None:
    """Service level under the four abandon rules: one queue and week, or every queue overall."""
    con = _open(data)
    if week is None:
        sql = "SELECT * FROM agg_queue" + (" WHERE queue = ?" if queue else "") + " ORDER BY queue"
        rows = con.execute(sql, [queue] if queue else []).df()
        order = {name: k for k, name in enumerate(synth.load_config()["queues"])}
        rows = rows.sort_values("queue", key=lambda col: col.map(order).fillna(len(order)))
        if rows.empty:
            console.print(f"[red]No data for queue {queue!r}.[/red]")
            raise typer.Exit(1)
        console.print(_summary_table("All weeks: ratio of sums", rows))
        return
    if queue is None:
        console.print("[red]--week needs --queue.[/red]")
        raise typer.Exit(2)
    row = con.execute(
        "SELECT * FROM agg_queue_week WHERE queue = ? AND iso_week = ?", [queue, week]
    ).df()
    if row.empty:
        known = con.execute("SELECT DISTINCT queue FROM agg_queue_week ORDER BY 1").df()["queue"]
        console.print(
            f"[red]No data for queue {queue!r} in {week}. Queues: {', '.join(known)}[/red]"
        )
        raise typer.Exit(1)
    console.print(_summary_table(f"{queue} {week}: ratio of sums", row))
    days = con.execute(
        "SELECT * FROM agg_queue_day WHERE queue = ? AND iso_week = ? ORDER BY interval_date",
        [queue, week],
    ).df()
    daily = Table(title="By day", box=box.SIMPLE_HEAD)
    for col in ("Date", "Offered", "SL1", "SL2", "SL3", "SL4", "Abandon"):
        daily.add_column(col, justify="right" if col != "Date" else "left")
    for d in days.itertuples():
        daily.add_row(
            pd.Timestamp(d.interval_date).strftime("%a %Y-%m-%d"),
            f"{int(d.offered):,}",
            _pct(d.sl1_pct),
            _pct(d.sl2_pct),
            _pct(d.sl3_pct),
            _pct(d.sl4_pct),
            _pct(d.abandon_pct),
        )
    console.print(daily)


@app.command()
def staffing(
    queue: Annotated[str, typer.Option(help="Queue name, e.g. billing.")],
    date: Annotated[str | None, typer.Option(help="Day to list, e.g. 2025-03-04.")] = None,
    top: Annotated[
        int, typer.Option(help="Without --date: list the N intervals with the largest gap.")
    ] = 10,
    data: DataDir = DEFAULT_MART,
) -> None:
    """Required against logged-in agents per half hour for one queue."""
    con = _open(data)
    base = (
        "SELECT interval_start, time_label, offered, handle_seconds / NULLIF(answered, 0) AS aht, "
        "required_agents, rostered, logged_in, staffing_gap, sl1_pct "
        "FROM fct_interval_staffing WHERE queue = ?"
    )
    if date:
        df = con.execute(
            base + " AND interval_date = CAST(? AS DATE) ORDER BY interval_start", [queue, date]
        ).df()
        title = f"{queue} {date}"
    else:
        df = con.execute(
            base + " ORDER BY staffing_gap DESC, offered DESC, interval_start LIMIT ?", [queue, top]
        ).df()
        title = f"{queue}: {top} intervals with the largest staffing gap"
    if df.empty:
        console.print(
            f"[red]No intervals for queue {queue!r}" + (f" on {date}" if date else "") + "[/red]"
        )
        raise typer.Exit(1)
    table = Table(title=title, box=box.SIMPLE_HEAD)
    table.add_column("Interval" if date is None else "Time")
    for col in ("Offered", "AHT (s)", "Required", "Rostered", "Logged in", "Gap", "SL1"):
        table.add_column(col, justify="right")
    for r in df.itertuples():
        stamp = (
            r.time_label if date else pd.Timestamp(r.interval_start).strftime("%a %Y-%m-%d %H:%M")
        )
        table.add_row(
            stamp,
            f"{int(r.offered)}",
            _num(r.aht, 0),
            f"{int(r.required_agents)}",
            f"{int(r.rostered)}",
            f"{int(r.logged_in)}",
            f"{int(r.staffing_gap):+d}",
            _pct(r.sl1_pct),
        )
    console.print(table)


@app.command(name="erlang")
def erlang_cmd(
    calls: Annotated[float, typer.Option(help="Calls offered in the interval.")],
    aht: Annotated[float, typer.Option(help="Average handle time in seconds.")],
    interval: Annotated[float, typer.Option(help="Interval length in seconds.")] = 1800.0,
    target: Annotated[
        float, typer.Option(help="Service-level target, 0-1.")
    ] = erlang.DEFAULT_TARGET,
    threshold: Annotated[
        float, typer.Option(help="Threshold T in seconds.")
    ] = erlang.DEFAULT_THRESHOLD_S,
    max_occupancy: Annotated[
        float, typer.Option(help="Occupancy cap; 1 turns it off.")
    ] = erlang.DEFAULT_MAX_OCCUPANCY,
    shrinkage: Annotated[float, typer.Option(help="Shrinkage, 0-1.")] = 0.0,
) -> None:
    """Required agents for one interval by Erlang C (via the stable Erlang B recurrence)."""
    try:
        res = erlang.solve(
            calls,
            aht,
            interval,
            target,
            threshold,
            None if max_occupancy >= 1 else max_occupancy,
            shrinkage,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    rows = [
        ("Offered load A (Erlangs)", f"{res.traffic:.2f}"),
        (f"Required agents for {target:.0%} in {threshold:g} s", f"{res.required}"),
        ("Service level at that headcount", f"{res.service_level:.4f}"),
        (
            "Service level with one fewer agent",
            "-" if res.service_level_one_fewer is None else f"{res.service_level_one_fewer:.4f}",
        ),
        ("Probability of waiting, C(N, A)", f"{res.prob_wait:.4f}"),
        (
            "ASA (s)",
            "inf" if math.isinf(res.asa_seconds) else f"{res.asa_seconds:.1f}",
        ),
        ("Occupancy", f"{res.occupancy:.1%}"),
        (f"Scheduled agents at {shrinkage:.0%} shrinkage", f"{res.scheduled}"),
    ]
    table = Table(box=box.SIMPLE_HEAD, show_header=False)
    table.add_column(justify="left")
    table.add_column(justify="right")
    for label, value in rows:
        table.add_row(label, value)
    console.print(table)


@app.command("validate-pbip")
def validate_pbip(
    pbip_file: Annotated[Path, typer.Option("--pbip", help="Path to the .pbip file.")] = Path(
        "powerbi/ServiceLevel.pbip"
    ),
    data: DataDir = DEFAULT_MART,
) -> None:
    """Statically check the Power BI project against the exported CSVs and the metric registry."""
    issues = pbip_validate.validate(pbip_file, data)
    if issues:
        for issue in issues:
            console.print(f"[red]{issue}[/red]", highlight=False)
        console.print(f"[red]{len(issues)} problem(s) found[/red]")
        raise typer.Exit(1)
    console.print(
        f"{pbip_file}: OK ({len(pbip.TABLE_COLUMNS)} tables, "
        f"{len(pbip_validate.registry.METRICS)} measures match the registry)"
    )


if __name__ == "__main__":
    app()
