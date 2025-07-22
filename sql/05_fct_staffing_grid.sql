-- Weekday x half-hour grid per queue: the shape behind the staffing-gap heatmap.
-- share_short is the fraction of the grid cell's intervals with at least one agent missing.
CREATE OR REPLACE VIEW fct_staffing_grid AS
SELECT
    queue,
    iso_weekday,
    interval_idx,
    time_label,
    count(*)                                                                  AS intervals,
    CAST(sum(required_agents) AS DOUBLE) / count(*)                           AS required_agents,
    CAST(sum(logged_in) AS DOUBLE) / count(*)                                 AS logged_in_agents,
    CAST(sum(required_agents) - sum(logged_in) AS DOUBLE) / count(*)          AS staffing_gap,
    CAST(sum(CASE WHEN required_agents > logged_in THEN 1 ELSE 0 END) AS DOUBLE)
        / count(*)                                                            AS share_short,
    CAST(sum(answered_within_t) AS DOUBLE) / NULLIF(sum(offered), 0)          AS sl1_pct
FROM fct_interval_staffing
GROUP BY queue, iso_weekday, interval_idx, time_label;
