import pandas as pd
import pytest

from servicelevel import synth


def test_shape_and_calendar(raw):
    assert len(raw) == 8064
    assert list(raw.columns) == synth.RAW_COLUMNS
    assert set(raw["queue"]) == {"billing", "technical", "sales"}
    assert raw["interval_start"].min() == pd.Timestamp("2025-03-03 00:00")
    assert raw["interval_start"].min().day_name() == "Monday"
    assert raw["interval_start"].max() == pd.Timestamp("2025-04-27 23:30")
    assert (raw.groupby("queue").size() == 56 * 48).all()
    assert not raw.duplicated(["queue", "interval_start"]).any()


def test_deterministic_for_a_seed(raw):
    pd.testing.assert_frame_equal(raw, synth.generate(synth.DEFAULT_SEED))
    other = synth.generate(synth.DEFAULT_SEED + 1)
    assert not other["offered"].equals(raw["offered"])


def test_counts_are_internally_consistent(raw):
    assert (raw["offered"] == raw["answered"] + raw["abandoned"]).all()
    assert (raw["answered_within_t"] <= raw["answered"]).all()
    assert (raw["abandoned_within_t"] <= raw["abandoned"]).all()
    assert (raw[["offered", "answered", "abandoned", "rostered", "logged_in"]] >= 0).all().all()
    assert (raw["offered"] >= 1).all()
    shortfall = raw["rostered"] - raw["logged_in"]
    assert shortfall.between(0, 2).all() or (raw.loc[shortfall < 0, "logged_in"] == 1).all()
    assert (raw["handle_seconds"] <= raw["logged_in"] * 1800).all()


def test_daily_shape_has_two_peaks_and_monday_is_heaviest(raw):
    t = raw[raw["queue"] == "technical"].copy()
    t["hour"] = t["interval_start"].dt.hour
    by_hour = t.groupby("hour")["offered"].mean()
    assert by_hour.loc[11] > by_hour.loc[13] > by_hour.loc[3]
    assert by_hour.loc[15] > by_hour.loc[13]
    by_day = t.groupby(t["interval_start"].dt.day_of_week)["offered"].sum()
    assert by_day.idxmax() == 0


def test_aht_is_in_the_documented_range(raw):
    aht = raw.groupby("queue")["handle_seconds"].sum() / raw.groupby("queue")["answered"].sum()
    assert aht.between(235, 485).all()  # 240-480 s base, plus per-interval noise
    assert aht["technical"] > aht["billing"] > aht["sales"]


def test_billing_overnight_is_quiet_and_always_within_threshold(raw):
    b = raw[raw["queue"] == "billing"]
    hour = b["interval_start"].dt.hour
    night = b[(hour < 7) | (hour >= 22)]
    assert night["offered"].between(1, 4).all()
    assert (night["answered_within_t"] == night["offered"]).all()
    assert (night["abandoned"] == 0).all()


def test_billing_is_understaffed_on_tuesday_and_wednesday_lunch(raw):
    b = raw[raw["queue"] == "billing"].copy()
    h = b["interval_start"].dt.hour + b["interval_start"].dt.minute / 60
    window = b["interval_start"].dt.day_of_week.isin([1, 2]) & (h >= 11) & (h < 14)
    same_slot_thursday = (b["interval_start"].dt.day_of_week == 3) & (h >= 11) & (h < 14)
    assert b.loc[window, "rostered"].mean() < 0.7 * b.loc[same_slot_thursday, "rostered"].mean()


def test_technical_misroute_hangs_up_inside_the_threshold(raw):
    t = raw[raw["queue"] == "technical"]
    share = t["abandoned_within_t"].sum() / t["offered"].sum()
    assert 0.09 <= share <= 0.13
    other = raw[raw["queue"] != "technical"]
    assert other["abandoned_within_t"].sum() / other["offered"].sum() < 0.03


def test_sales_is_overstaffed_with_very_low_abandonment(raw):
    s = raw[raw["queue"] == "sales"]
    assert s["abandoned"].sum() / s["offered"].sum() <= 0.02
    assert (s["rostered"] >= 2).all()


def test_queue_config_is_complete():
    cfg = synth.load_config()
    assert set(cfg["queues"]) == {"billing", "technical", "sales"}
    assert sum(cfg["absence_probabilities"]) == pytest.approx(1.0)
    assert len(cfg["day_of_week_multiplier"]) == 7


def test_raw_csv_round_trip(raw, tmp_path):
    path = tmp_path / "raw.csv"
    synth.write_raw(raw, path)
    pd.testing.assert_frame_equal(synth.read_raw(path), raw, check_dtype=False)
