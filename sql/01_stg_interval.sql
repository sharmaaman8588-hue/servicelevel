-- Staging: one row per queue and half hour, with calendar keys.
-- Input table raw_interval is registered by servicelevel.mart (counts, plus the Erlang
-- requirement computed in Python because DuckDB has no stable Erlang B recurrence).
CREATE OR REPLACE VIEW stg_interval AS
SELECT
    queue,
    interval_start,
    CAST(interval_start AS DATE)                                         AS interval_date,
    strftime(interval_start, '%G-W%V')                                   AS iso_week,
    isodow(interval_start)                                               AS iso_weekday,
    hour(interval_start) * 2 + CAST(minute(interval_start) / 30 AS INTEGER) AS interval_idx,
    strftime(interval_start, '%H:%M')                                    AS time_label,
    offered,
    answered,
    answered_within_t,
    abandoned,
    abandoned_within_t,
    handle_seconds,
    wait_seconds,
    rostered,
    logged_in,
    required_agents
FROM raw_interval;
