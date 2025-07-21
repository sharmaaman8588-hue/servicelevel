-- Service level for one interval under four abandon rules, at the threshold T the counts were
-- recorded with. Zero denominators give NULL, never 0 and never an error.
--   SL1 = answered_within_t / offered
--   SL2 = answered_within_t / answered
--   SL3 = answered_within_t / (offered - abandoned_within_t)
--   SL4 = (answered_within_t + abandoned_within_t) / offered
-- is_consistent is the data-integrity check: offered = answered + abandoned and
-- SL1 <= SL3 <= min(SL2, SL4), every defined value in [0, 1].
CREATE OR REPLACE VIEW fct_interval_service AS
WITH r AS (
    SELECT
        *,
        CAST(answered_within_t AS DOUBLE) / NULLIF(offered, 0)                       AS sl1_pct,
        CAST(answered_within_t AS DOUBLE) / NULLIF(answered, 0)                      AS sl2_pct,
        CAST(answered_within_t AS DOUBLE) / NULLIF(offered - abandoned_within_t, 0)  AS sl3_pct,
        CAST(answered_within_t + abandoned_within_t AS DOUBLE) / NULLIF(offered, 0)  AS sl4_pct,
        CAST(abandoned AS DOUBLE) / NULLIF(offered, 0)                               AS abandon_pct,
        CAST(wait_seconds AS DOUBLE) / NULLIF(answered, 0)                           AS asa_s,
        CAST(handle_seconds AS DOUBLE) / NULLIF(logged_in * 1800.0, 0)               AS occupancy_pct
    FROM stg_interval
)
SELECT
    *,
    (offered = answered + abandoned
        AND answered_within_t <= answered
        AND abandoned_within_t <= abandoned
        AND coalesce(sl1_pct <= sl3_pct + 1e-12, TRUE)
        AND coalesce(sl3_pct <= least(sl2_pct, sl4_pct) + 1e-12, TRUE)
        AND coalesce(sl4_pct <= 1.0 + 1e-12, TRUE)
    ) AS is_consistent
FROM r;
