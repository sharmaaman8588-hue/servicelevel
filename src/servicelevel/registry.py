"""One list of metrics from which SQL column names and DAX measures are generated.

Each metric carries

* ``measure``     the name of the DAX measure and the label shown in Power BI;
* ``sql_column``  the column that holds it in the DuckDB mart (``agg_queue_week`` and friends);
* ``sql_expr``    the same figure written as a DuckDB aggregate over ``stg_interval``;
* ``dax``         the DAX measure expression, written over the ``Interval`` fact table.

``sql_expr`` exists so that a test can prove the hand-written SQL views and the registry agree,
and ``dax`` so that the static PBIP validator can prove the committed TMDL matches the registry.
Service-level metrics are only ever ratios of sums.
"""

from __future__ import annotations

from dataclasses import dataclass

FACT_TABLE = "Interval"
INTERVAL_SECONDS = 1800


@dataclass(frozen=True)
class Metric:
    measure: str
    sql_column: str
    sql_expr: str
    dax: str
    format_string: str
    description: str
    is_percentage: bool = False

    @property
    def dax_columns(self) -> tuple[str, ...]:
        """Fact-table columns the DAX expression reads."""
        import re

        return tuple(dict.fromkeys(re.findall(rf"{FACT_TABLE}\[([^\]]+)\]", self.dax)))


def _sum(col: str) -> str:
    return f"SUM({FACT_TABLE}[{col}])"


_PER_INTERVAL = f"DISTINCTCOUNT({FACT_TABLE}[interval_start])"

METRICS: tuple[Metric, ...] = (
    Metric(
        measure="SL1 %",
        sql_column="sl1_pct",
        sql_expr="CAST(sum(answered_within_t) AS DOUBLE) / NULLIF(sum(offered), 0)",
        dax=f"DIVIDE({_sum('answered_within_t')}, {_sum('offered')})",
        format_string="0.0%",
        description="Answered within threshold / offered.",
        is_percentage=True,
    ),
    Metric(
        measure="SL2 %",
        sql_column="sl2_pct",
        sql_expr="CAST(sum(answered_within_t) AS DOUBLE) / NULLIF(sum(answered), 0)",
        dax=f"DIVIDE({_sum('answered_within_t')}, {_sum('answered')})",
        format_string="0.0%",
        description="Answered within threshold / answered. Abandoned calls are ignored.",
        is_percentage=True,
    ),
    Metric(
        measure="SL3 %",
        sql_column="sl3_pct",
        sql_expr=(
            "CAST(sum(answered_within_t) AS DOUBLE) "
            "/ NULLIF(sum(offered) - sum(abandoned_within_t), 0)"
        ),
        dax=(
            f"DIVIDE({_sum('answered_within_t')}, {_sum('offered')} - {_sum('abandoned_within_t')})"
        ),
        format_string="0.0%",
        description="Answered within threshold / (offered - abandoned within threshold).",
        is_percentage=True,
    ),
    Metric(
        measure="SL4 %",
        sql_column="sl4_pct",
        sql_expr=(
            "CAST(sum(answered_within_t) + sum(abandoned_within_t) AS DOUBLE) "
            "/ NULLIF(sum(offered), 0)"
        ),
        dax=(
            f"DIVIDE({_sum('answered_within_t')} + {_sum('abandoned_within_t')}, {_sum('offered')})"
        ),
        format_string="0.0%",
        description="(Answered + abandoned within threshold) / offered.",
        is_percentage=True,
    ),
    Metric(
        measure="Abandon %",
        sql_column="abandon_pct",
        sql_expr="CAST(sum(abandoned) AS DOUBLE) / NULLIF(sum(offered), 0)",
        dax=f"DIVIDE({_sum('abandoned')}, {_sum('offered')})",
        format_string="0.0%",
        description="Abandoned / offered.",
        is_percentage=True,
    ),
    Metric(
        measure="ASA (s)",
        sql_column="asa_s",
        sql_expr="CAST(sum(wait_seconds) AS DOUBLE) / NULLIF(sum(answered), 0)",
        dax=f"DIVIDE({_sum('wait_seconds')}, {_sum('answered')})",
        format_string="0.0",
        description="Average speed of answer: queue seconds of answered calls / answered.",
    ),
    Metric(
        measure="Occupancy %",
        sql_column="occupancy_pct",
        sql_expr=(
            "CAST(sum(handle_seconds) AS DOUBLE) "
            f"/ NULLIF(sum(logged_in) * {INTERVAL_SECONDS}.0, 0)"
        ),
        dax=f"DIVIDE({_sum('handle_seconds')}, {_sum('logged_in')} * {INTERVAL_SECONDS})",
        format_string="0.0%",
        description="Handle seconds / logged-in agent seconds.",
        is_percentage=True,
    ),
    Metric(
        measure="Required Agents",
        sql_column="required_agents",
        sql_expr="CAST(sum(required_agents) AS DOUBLE) / count(DISTINCT interval_start)",
        dax=f"DIVIDE({_sum('required_agents')}, {_PER_INTERVAL})",
        format_string="0.0",
        description="Erlang C requirement at the actual offered calls, per interval.",
    ),
    Metric(
        measure="Logged-in Agents",
        sql_column="logged_in_agents",
        sql_expr="CAST(sum(logged_in) AS DOUBLE) / count(DISTINCT interval_start)",
        dax=f"DIVIDE({_sum('logged_in')}, {_PER_INTERVAL})",
        format_string="0.0",
        description="Agents logged in, per interval.",
    ),
    Metric(
        measure="Staffing Gap",
        sql_column="staffing_gap",
        sql_expr=(
            "CAST(sum(required_agents) - sum(logged_in) AS DOUBLE) / count(DISTINCT interval_start)"
        ),
        dax="[Required Agents] - [Logged-in Agents]",
        format_string="0.0;-0.0;0.0",
        description="Required minus logged-in agents; positive means short-staffed.",
    ),
)

BY_MEASURE = {m.measure: m for m in METRICS}
BY_SQL_COLUMN = {m.sql_column: m for m in METRICS}


def measure_names() -> list[str]:
    return [m.measure for m in METRICS]


def sql_columns() -> list[str]:
    return [m.sql_column for m in METRICS]


def dax_measures() -> dict[str, str]:
    """Measure name -> DAX expression."""
    return {m.measure: m.dax for m in METRICS}


def percentage_measures() -> set[str]:
    return {m.measure for m in METRICS if m.is_percentage}
