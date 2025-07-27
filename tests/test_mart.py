import duckdb
import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from servicelevel import erlang, mart, registry
from servicelevel.synth import RAW_COLUMNS

TOL = 1e-12


def _ratio_of_sums(df: pd.DataFrame) -> dict[str, float]:
    """Independent pandas recomputation; a zero denominator gives NaN, as NULL does in SQL."""
    s = df[["offered", "answered", "answered_within_t", "abandoned_within_t", "abandoned"]].sum()

    def ratio(num: float, den: float) -> float:
        return num / den if den else float("nan")

    return {
        "sl1": ratio(s.answered_within_t, s.offered),
        "sl2": ratio(s.answered_within_t, s.answered),
        "sl3": ratio(s.answered_within_t, s.offered - s.abandoned_within_t),
        "sl4": ratio(s.answered_within_t + s.abandoned_within_t, s.offered),
        "abandon": ratio(s.abandoned, s.offered),
    }


def test_sql_matches_pandas_ratio_of_sums(con, raw):
    agg = con.execute("SELECT * FROM agg_queue_week").df()
    assert len(agg) == 3 * 8
    iso = raw["interval_start"].dt.isocalendar()
    week = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    for row in agg.itertuples():
        sub = raw[(raw["queue"] == row.queue) & (week == row.iso_week)]
        expect = _ratio_of_sums(sub)
        assert row.sl1_pct == pytest.approx(expect["sl1"], abs=TOL)
        assert row.sl2_pct == pytest.approx(expect["sl2"], abs=TOL)
        assert row.sl3_pct == pytest.approx(expect["sl3"], abs=TOL)
        assert row.sl4_pct == pytest.approx(expect["sl4"], abs=TOL)
        assert row.abandon_pct == pytest.approx(expect["abandon"], abs=TOL)
        assert row.asa_s == pytest.approx(sub.wait_seconds.sum() / sub.answered.sum(), abs=1e-9)
        assert row.occupancy_pct == pytest.approx(
            sub.handle_seconds.sum() / (sub.logged_in.sum() * 1800), abs=TOL
        )


def test_whole_period_aggregates_match_pandas(con, raw):
    agg = con.execute("SELECT * FROM agg_queue").df().set_index("queue")
    for name, sub in raw.groupby("queue"):
        expect = _ratio_of_sums(sub)
        for key, col in [
            ("sl1", "sl1_pct"),
            ("sl2", "sl2_pct"),
            ("sl3", "sl3_pct"),
            ("sl4", "sl4_pct"),
        ]:
            assert agg.loc[name, col] == pytest.approx(expect[key], abs=TOL)


def test_iso_week_labels(con):
    weeks = con.execute("SELECT DISTINCT iso_week FROM agg_queue_week ORDER BY 1").df()["iso_week"]
    assert list(weeks) == [f"2025-W{w}" for w in range(10, 18)]


def test_billing_call_weighted_sl1_sits_far_below_the_interval_mean(con):
    row = con.execute("SELECT * FROM agg_queue WHERE queue = 'billing'").df().iloc[0]
    assert row.sl1_interval_mean - row.sl1_pct >= 0.08


def test_billing_every_week_is_flattered_by_the_interval_mean(con):
    weekly = con.execute("SELECT * FROM agg_queue_week WHERE queue = 'billing'").df()
    assert (weekly.sl1_interval_mean > weekly.sl1_pct + 0.05).all()


def test_technical_misroute_separates_sl4_from_sl1(con):
    row = con.execute("SELECT * FROM agg_queue WHERE queue = 'technical'").df().iloc[0]
    assert row.sl4_pct - row.sl1_pct >= 0.08


def test_sales_definitions_agree_within_three_points(con):
    row = con.execute("SELECT * FROM agg_queue WHERE queue = 'sales'").df().iloc[0]
    values = [row.sl1_pct, row.sl2_pct, row.sl3_pct, row.sl4_pct]
    assert max(values) - min(values) <= 0.03
    assert row.abandon_pct <= 0.02


def test_ordering_holds_on_every_interval_and_aggregate(con):
    fct = con.execute("SELECT * FROM fct_interval_service").df()
    assert len(fct) == 8064
    assert fct["is_consistent"].all()
    assert (fct.offered == fct.answered + fct.abandoned).all()
    # SL3 is NULL only when every offered call hung up inside T (a one-call technical night).
    undefined = fct.sl3_pct.isna()
    assert undefined.any()
    assert (fct.offered[undefined] == fct.abandoned_within_t[undefined]).all()
    ok = ~undefined
    assert (fct.sl1_pct[ok] <= fct.sl3_pct[ok] + TOL).all()
    upper = fct[["sl2_pct", "sl4_pct"]].min(axis=1)  # SL2 is NULL when nothing was answered
    assert (fct.sl3_pct[ok] <= upper[ok] + TOL).all()
    for col in ("sl1_pct", "sl2_pct", "sl3_pct", "sl4_pct"):
        assert fct[col].dropna().between(0, 1).all()
    for view in ("agg_queue_week", "agg_queue", "agg_queue_day"):
        agg = con.execute(f"SELECT * FROM {view}").df()
        assert (agg.sl1_pct <= agg.sl3_pct + TOL).all()
        assert (agg.sl3_pct <= agg[["sl2_pct", "sl4_pct"]].min(axis=1) + TOL).all()


def test_zero_denominators_give_null_not_zero():
    rows = pd.DataFrame(
        [
            ("q", "2025-03-03 00:00", 0, 0, 0, 0, 0, 0, 0, 2, 2, 0),
            ("q", "2025-03-03 00:30", 3, 0, 0, 3, 3, 0, 0, 2, 0, 2),
        ],
        columns=[*RAW_COLUMNS, "required_agents"],
    )
    rows["interval_start"] = pd.to_datetime(rows["interval_start"])
    c = mart.open_mart(rows)
    first, second = (
        c.execute("SELECT * FROM fct_interval_service ORDER BY interval_start").df().itertuples()
    )
    assert pd.isna([first.sl1_pct, first.sl2_pct, first.sl3_pct, first.sl4_pct]).all()
    assert pd.isna(first.abandon_pct) and pd.isna(first.asa_s)
    assert second.sl1_pct == 0.0 and pd.isna(second.sl2_pct) and pd.isna(second.sl3_pct)
    assert second.sl4_pct == 1.0 and second.abandon_pct == 1.0  # all three hung up inside T
    assert pd.isna(second.occupancy_pct)  # nobody logged in


def test_registry_sql_expressions_agree_with_the_views(con):
    agg = con.execute("SELECT * FROM agg_queue_week ORDER BY queue, iso_week").df()
    for metric in registry.METRICS:
        direct = con.execute(
            f"SELECT {metric.sql_expr} AS v FROM stg_interval GROUP BY queue, iso_week "
            "ORDER BY queue, iso_week"
        ).df()["v"]
        assert metric.sql_column in agg.columns
        np.testing.assert_allclose(direct, agg[metric.sql_column], rtol=0, atol=TOL)


def test_required_agents_column_is_erlang_c_at_actual_load(interval):
    sample = interval.iloc[[100, 1500, 2690, 5000, 7000]]
    aht = mart.interval_aht(interval).loc[sample.index]
    for (_, row), h in zip(sample.iterrows(), aht, strict=True):
        a = erlang.traffic_erlangs(row.offered, h)
        assert row.required_agents == erlang.required_agents(a, h, 0.8, 20, 0.85)
        if a > 0:
            assert row.required_agents > a


def test_planted_billing_shortfall_is_visible_in_the_gap(fct):
    b = fct[fct.queue == "billing"]
    window = (
        b.interval_start.dt.day_of_week.isin([1, 2])
        & (b.interval_start.dt.hour >= 11)
        & (b.interval_start.dt.hour < 14)
    )
    planted = b[window]
    assert len(planted) == 2 * 6 * 8
    assert (planted.staffing_gap >= 2).mean() >= 0.90
    assert b[~window].staffing_gap.mean() < 0.5


def test_sales_is_never_short_staffed(fct):
    s = fct[fct.queue == "sales"]
    assert (s.staffing_gap <= 1).mean() >= 0.95
    assert s.staffing_gap.mean() < 0


def test_staffing_grid_covers_every_weekday_and_half_hour(con):
    grid = con.execute("SELECT * FROM fct_staffing_grid").df()
    assert len(grid) == 3 * 7 * 48
    assert (grid.intervals == 8).all()
    top = grid[grid.queue == "billing"].nlargest(6, "staffing_gap")
    assert set(top.iso_weekday) == {2, 3}
    assert top.interval_idx.between(22, 27).all()
    assert (top.share_short >= 0.85).all()


def test_exported_mart_is_small_and_complete(mart_dir):
    total = sum((mart_dir / name).stat().st_size for name in mart.MART_FILES)
    assert total < 1_000_000
    interval = pd.read_csv(mart_dir / "Interval.csv")
    assert list(interval.columns) == mart.INTERVAL_EXPORT_COLUMNS
    assert len(interval) == 8064
    assert len(pd.read_csv(mart_dir / "Calendar.csv")) == 56
    assert len(pd.read_csv(mart_dir / "TimeOfDay.csv")) == 48
    assert list(pd.read_csv(mart_dir / "Queue.csv")["queue"]) == ["billing", "technical", "sales"]


def test_reopening_the_exported_csv_reproduces_the_aggregates(con, mart_dir):
    again = mart.open_from_dir(mart_dir)
    a = con.execute("SELECT * FROM agg_queue_week ORDER BY 1, 2").df()
    b = again.execute("SELECT * FROM agg_queue_week ORDER BY 1, 2").df()
    pd.testing.assert_frame_equal(a, b)


def _rows(draw):
    n = draw(st.integers(1, 12))
    out = []
    for k in range(n):
        answered = draw(st.integers(0, 60))
        abandoned = draw(st.integers(0, 60))
        within = draw(st.integers(0, answered))
        abandoned_within = draw(st.integers(0, abandoned))
        out.append(
            (
                "q",
                pd.Timestamp("2025-03-03") + pd.Timedelta(minutes=30 * k),
                answered + abandoned,
                answered,
                within,
                abandoned,
                abandoned_within,
                answered * 200,
                answered * 5,
                draw(st.integers(1, 30)),
                draw(st.integers(1, 30)),
                draw(st.integers(0, 30)),
            )
        )
    return pd.DataFrame(out, columns=[*RAW_COLUMNS, "required_agents"])


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.composite(_rows)())
def test_property_ordering_and_bounds_for_random_counts(df):
    c = mart.open_mart(df)
    fct = c.execute("SELECT * FROM fct_interval_service").df()
    assert fct["is_consistent"].all()
    for _, r in fct.iterrows():
        vals = {k: r[k] for k in ("sl1_pct", "sl2_pct", "sl3_pct", "sl4_pct")}
        for v in vals.values():
            assert pd.isna(v) or 0.0 <= v <= 1.0
        if not pd.isna(vals["sl3_pct"]):
            assert vals["sl1_pct"] <= vals["sl3_pct"] + TOL
            assert pd.isna(vals["sl2_pct"]) or vals["sl3_pct"] <= vals["sl2_pct"] + TOL
            assert vals["sl3_pct"] <= vals["sl4_pct"] + TOL
    agg = c.execute("SELECT * FROM agg_queue").df().iloc[0]
    assert agg.is_consistent
    if not pd.isna(agg.sl3_pct):
        assert agg.sl1_pct <= agg.sl3_pct + TOL
        assert pd.isna(agg.sl2_pct) or agg.sl3_pct <= agg.sl2_pct + TOL
        assert agg.sl3_pct <= agg.sl4_pct + TOL
    expect = _ratio_of_sums(df)
    for key in ("sl1", "sl2", "sl3", "sl4"):
        got, want = agg[f"{key}_pct"], expect[key]
        assert (pd.isna(got) and np.isnan(want)) or got == pytest.approx(want, abs=TOL)


def test_inconsistent_counts_are_flagged():
    bad = pd.DataFrame(
        [("q", pd.Timestamp("2025-03-03"), 10, 5, 7, 5, 0, 0, 0, 1, 1, 0)],
        columns=[*RAW_COLUMNS, "required_agents"],
    )  # answered within threshold (7) exceeds answered (5)
    c = mart.open_mart(bad)
    assert not c.execute("SELECT is_consistent FROM fct_interval_service").fetchone()[0]


def test_duckdb_is_used_in_memory_only(con):
    assert isinstance(con, duckdb.DuckDBPyConnection)
