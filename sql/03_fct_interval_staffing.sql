-- Required agents (Erlang C at the actual offered calls and AHT) against rostered and logged-in
-- agents. staffing_gap > 0 means fewer agents were logged in than the interval needed.
CREATE OR REPLACE VIEW fct_interval_staffing AS
SELECT
    queue,
    interval_start,
    interval_date,
    iso_week,
    iso_weekday,
    interval_idx,
    time_label,
    offered,
    answered,
    answered_within_t,
    abandoned,
    abandoned_within_t,
    handle_seconds,
    wait_seconds,
    rostered,
    logged_in,
    required_agents,
    required_agents - logged_in  AS staffing_gap,
    required_agents - rostered   AS rostered_gap,
    sl1_pct,
    sl2_pct,
    sl3_pct,
    sl4_pct,
    is_consistent
FROM fct_interval_service;
