-- Aggregates. Every service-level figure is a ratio of sums over the group; sl1_interval_mean
-- (the unweighted mean of interval SL1) is reported only as the contrast that shows why.
-- Agent figures are per-interval averages: SUM(agents) / number of distinct intervals.
CREATE OR REPLACE VIEW agg_queue_week AS
SELECT
    queue,
    iso_week,
    count(*)                                                                  AS intervals,
    sum(offered)                                                              AS offered,
    sum(answered)                                                             AS answered,
    sum(abandoned)                                                            AS abandoned,
    CAST(sum(answered_within_t) AS DOUBLE) / NULLIF(sum(offered), 0)          AS sl1_pct,
    CAST(sum(answered_within_t) AS DOUBLE) / NULLIF(sum(answered), 0)         AS sl2_pct,
    CAST(sum(answered_within_t) AS DOUBLE)
        / NULLIF(sum(offered) - sum(abandoned_within_t), 0)                   AS sl3_pct,
    CAST(sum(answered_within_t) + sum(abandoned_within_t) AS DOUBLE)
        / NULLIF(sum(offered), 0)                                             AS sl4_pct,
    avg(sl1_pct)                                                              AS sl1_interval_mean,
    CAST(sum(abandoned) AS DOUBLE) / NULLIF(sum(offered), 0)                  AS abandon_pct,
    CAST(sum(wait_seconds) AS DOUBLE) / NULLIF(sum(answered), 0)              AS asa_s,
    CAST(sum(handle_seconds) AS DOUBLE) / NULLIF(sum(logged_in) * 1800.0, 0)  AS occupancy_pct,
    CAST(sum(required_agents) AS DOUBLE) / count(DISTINCT interval_start)     AS required_agents,
    CAST(sum(logged_in) AS DOUBLE) / count(DISTINCT interval_start)           AS logged_in_agents,
    CAST(sum(required_agents) - sum(logged_in) AS DOUBLE)
        / count(DISTINCT interval_start)                                      AS staffing_gap,
    bool_and(is_consistent)                                                   AS is_consistent
FROM fct_interval_staffing
GROUP BY queue, iso_week;

CREATE OR REPLACE VIEW agg_queue AS
SELECT
    queue,
    count(*)                                                                  AS intervals,
    sum(offered)                                                              AS offered,
    sum(answered)                                                             AS answered,
    sum(abandoned)                                                            AS abandoned,
    CAST(sum(answered_within_t) AS DOUBLE) / NULLIF(sum(offered), 0)          AS sl1_pct,
    CAST(sum(answered_within_t) AS DOUBLE) / NULLIF(sum(answered), 0)         AS sl2_pct,
    CAST(sum(answered_within_t) AS DOUBLE)
        / NULLIF(sum(offered) - sum(abandoned_within_t), 0)                   AS sl3_pct,
    CAST(sum(answered_within_t) + sum(abandoned_within_t) AS DOUBLE)
        / NULLIF(sum(offered), 0)                                             AS sl4_pct,
    avg(sl1_pct)                                                              AS sl1_interval_mean,
    CAST(sum(abandoned) AS DOUBLE) / NULLIF(sum(offered), 0)                  AS abandon_pct,
    CAST(sum(wait_seconds) AS DOUBLE) / NULLIF(sum(answered), 0)              AS asa_s,
    CAST(sum(handle_seconds) AS DOUBLE) / NULLIF(sum(logged_in) * 1800.0, 0)  AS occupancy_pct,
    CAST(sum(required_agents) AS DOUBLE) / count(DISTINCT interval_start)     AS required_agents,
    CAST(sum(logged_in) AS DOUBLE) / count(DISTINCT interval_start)           AS logged_in_agents,
    CAST(sum(required_agents) - sum(logged_in) AS DOUBLE)
        / count(DISTINCT interval_start)                                      AS staffing_gap,
    bool_and(is_consistent)                                                   AS is_consistent
FROM fct_interval_staffing
GROUP BY queue;

CREATE OR REPLACE VIEW agg_queue_day AS
SELECT
    queue,
    iso_week,
    interval_date,
    iso_weekday,
    sum(offered)                                                              AS offered,
    CAST(sum(answered_within_t) AS DOUBLE) / NULLIF(sum(offered), 0)          AS sl1_pct,
    CAST(sum(answered_within_t) AS DOUBLE) / NULLIF(sum(answered), 0)         AS sl2_pct,
    CAST(sum(answered_within_t) AS DOUBLE)
        / NULLIF(sum(offered) - sum(abandoned_within_t), 0)                   AS sl3_pct,
    CAST(sum(answered_within_t) + sum(abandoned_within_t) AS DOUBLE)
        / NULLIF(sum(offered), 0)                                             AS sl4_pct,
    avg(sl1_pct)                                                              AS sl1_interval_mean,
    CAST(sum(abandoned) AS DOUBLE) / NULLIF(sum(offered), 0)                  AS abandon_pct
FROM fct_interval_staffing
GROUP BY queue, iso_week, interval_date, iso_weekday;
