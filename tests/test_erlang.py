import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from servicelevel import erlang


def test_textbook_ten_erlangs():
    a = erlang.traffic_erlangs(100, 180, 1800)
    assert a == pytest.approx(10.0)
    assert erlang.required_agents(a, 180, 0.8, 20) == 14
    assert erlang.service_level(14, a, 180, 20) == pytest.approx(0.8884, abs=1e-4)
    assert erlang.service_level(13, a, 180, 20) == pytest.approx(0.7956, abs=1e-4)
    assert erlang.erlang_c(13, a) == pytest.approx(0.2853, abs=1e-4)
    assert erlang.average_speed_of_answer(14, a, 180) == pytest.approx(7.8, abs=0.1)


def test_hourly_textbook_case():
    a = erlang.traffic_erlangs(360, 240, 3600)
    assert a == pytest.approx(24.0)
    assert erlang.required_agents(a, 240, 0.8, 20) == 29
    assert erlang.service_level(29, a, 240, 20) == pytest.approx(0.8403, abs=1e-4)
    assert erlang.service_level(28, a, 240, 20) == pytest.approx(0.7613, abs=1e-4)


def test_large_queue_does_not_overflow():
    a = 180.0
    # The factorial formula needs 180**N and N! as doubles; both overflow long before N = 190.
    with pytest.raises(OverflowError):
        _ = a**190 / math.factorial(190)
    assert erlang.required_agents(a, 300, 0.8, 20, max_occupancy=None) == 190
    values = [erlang.erlang_c(n, a) for n in range(181, 241)]
    assert all(0.0 <= v <= 1.0 for v in values)
    assert all(later < earlier for earlier, later in zip(values, values[1:], strict=False))
    assert 0.0 <= erlang.erlang_c(2000, 1900.0) <= 1.0


@pytest.mark.parametrize("agents", [0, 5, 10])
def test_unstable_queue_is_safe(agents):
    a = 10.0
    assert erlang.service_level(agents, a, 180, 20) == 0.0
    assert math.isinf(erlang.average_speed_of_answer(agents, a, 180))
    assert erlang.erlang_c(agents, a) == 1.0
    assert not math.isnan(erlang.service_level(agents, a, 180, 20))


def test_shrinkage_and_occupancy_cap():
    assert erlang.scheduled_agents(14, 0.30) == 20
    assert erlang.scheduled_agents(10, 0.0) == 10
    assert erlang.scheduled_agents(1, 0.5) == 2
    assert erlang.min_agents_for_occupancy(10.0, 0.85) == 12
    # A loose 5-minute threshold is met by 11 agents, so only the occupancy cap forces 12.
    assert erlang.required_agents(10.0, 180, 0.8, 300, max_occupancy=None) == 11
    assert erlang.required_agents(10.0, 180, 0.8, 300, max_occupancy=0.85) == 12


def test_erlang_b_matches_closed_form_for_small_systems():
    for n, a in [(1, 1.0), (2, 1.0), (5, 3.0), (10, 7.5)]:
        direct = (a**n / math.factorial(n)) / sum(a**k / math.factorial(k) for k in range(n + 1))
        assert erlang.erlang_b(n, a) == pytest.approx(direct, rel=1e-12)
    assert erlang.erlang_b(0, 4.0) == 1.0
    assert erlang.erlang_b(2, 1.0) == pytest.approx(0.2)


def test_zero_traffic_needs_no_agents():
    assert erlang.required_agents(0.0, 300) == 0
    assert erlang.service_level(3, 0.0, 300, 20) == 1.0
    assert erlang.average_speed_of_answer(3, 0.0, 300) == 0.0


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        erlang.erlang_b(-1, 1.0)
    with pytest.raises(ValueError):
        erlang.required_agents(5.0, 180, target=1.0)
    with pytest.raises(ValueError):
        erlang.scheduled_agents(10, 1.0)
    with pytest.raises(ValueError):
        erlang.traffic_erlangs(10, 180, 0)


def test_solve_reports_one_interval():
    res = erlang.solve(100, 180, 1800, 0.8, 20, shrinkage=0.3)
    assert (res.required, res.scheduled) == (14, 20)
    assert res.occupancy == pytest.approx(10 / 14)
    assert res.service_level_one_fewer == pytest.approx(0.7956, abs=1e-4)


@given(st.floats(0.5, 300.0), st.floats(60.0, 900.0))
def test_required_agents_meets_target_and_is_minimal(a, aht):
    n = erlang.required_agents(a, aht, 0.8, 20, max_occupancy=None)
    assert n > a
    assert erlang.service_level(n, a, aht, 20) >= 0.8 - 1e-9
    assert erlang.service_level(n - 1, a, aht, 20) < 0.8


@given(st.floats(0.1, 200.0), st.integers(1, 400))
def test_erlang_c_is_a_probability(a, n):
    assert 0.0 <= erlang.erlang_c(n, a) <= 1.0
    assert 0.0 <= erlang.service_level(n, a, 240, 20) <= 1.0
