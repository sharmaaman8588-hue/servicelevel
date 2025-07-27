from pathlib import Path

import pytest

from servicelevel import mart, synth

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def raw():
    return synth.generate()


@pytest.fixture(scope="session")
def interval(raw):
    return mart.with_required_agents(raw)


@pytest.fixture(scope="session")
def con(interval):
    return mart.open_mart(interval)


@pytest.fixture(scope="session")
def fct(con):
    return con.execute("SELECT * FROM fct_interval_staffing ORDER BY queue, interval_start").df()


@pytest.fixture(scope="session")
def mart_dir(con, tmp_path_factory):
    out = tmp_path_factory.mktemp("mart")
    mart.export_mart(con, out)
    return out
