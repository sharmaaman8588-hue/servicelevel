import re

from servicelevel import pbip, registry


def test_ten_measures_with_the_documented_names():
    assert registry.measure_names() == [
        "SL1 %",
        "SL2 %",
        "SL3 %",
        "SL4 %",
        "Abandon %",
        "ASA (s)",
        "Occupancy %",
        "Required Agents",
        "Logged-in Agents",
        "Staffing Gap",
    ]
    assert len(set(registry.sql_columns())) == 10


def test_sql_columns_are_snake_case_and_unique():
    for col in registry.sql_columns():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", col)


def test_dax_reads_only_existing_fact_columns():
    fact_columns = {c.name for c in pbip.TABLE_COLUMNS[registry.FACT_TABLE]}
    for metric in registry.METRICS:
        assert set(metric.dax_columns) <= fact_columns, metric.measure


def test_no_registry_measure_averages_anything():
    for metric in registry.METRICS:
        assert "AVERAGE" not in metric.dax.upper(), metric.measure


def test_service_level_measures_are_ratios_of_sums():
    for name in ("SL1 %", "SL2 %", "SL3 %", "SL4 %"):
        dax = registry.BY_MEASURE[name].dax
        assert dax.startswith("DIVIDE(SUM(")
        assert "SUM(Interval[sl1_pct])" not in dax


def test_percentage_flags():
    assert registry.percentage_measures() == {
        "SL1 %",
        "SL2 %",
        "SL3 %",
        "SL4 %",
        "Abandon %",
        "Occupancy %",
    }
    assert registry.BY_MEASURE["Staffing Gap"].dax == "[Required Agents] - [Logged-in Agents]"
