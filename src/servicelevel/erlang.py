"""Erlang B and C staffing maths that stay finite at hundreds of agents.

The textbook Erlang C formula divides ``A**N / N!`` by a sum of the same terms. At a few hundred
agents both overflow a double. Here Erlang C is derived from Erlang B, and Erlang B is computed
with the recurrence

    B(0) = 1,    B(n) = A * B(n-1) / (n + A * B(n-1))

in which every value stays inside [0, 1].

Conventions
-----------
* ``traffic`` (A) is the offered load in Erlangs: calls * AHT / interval length.
* ``agents`` (N) is a whole number of agents on the phones.
* When ``N <= A`` the queue is unstable: every call waits, so the probability of waiting is 1,
  service level is 0 and the average speed of answer is infinite. Nothing returns NaN or a
  negative value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_TARGET = 0.80
DEFAULT_THRESHOLD_S = 20.0
DEFAULT_MAX_OCCUPANCY = 0.85
_EPS = 1e-9


def traffic_erlangs(calls: float, aht_seconds: float, interval_seconds: float = 1800.0) -> float:
    """Offered load A in Erlangs."""
    if calls < 0 or aht_seconds < 0:
        raise ValueError("calls and aht_seconds must be non-negative")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    return calls * aht_seconds / interval_seconds


def erlang_b(agents: int, traffic: float) -> float:
    """Blocking probability B(N, A) by the stable recurrence."""
    if agents < 0:
        raise ValueError("agents must be non-negative")
    if traffic < 0:
        raise ValueError("traffic must be non-negative")
    b = 1.0
    for n in range(1, agents + 1):
        ab = traffic * b
        b = ab / (n + ab)
    return b


def erlang_c(agents: int, traffic: float) -> float:
    """Probability that an arriving call has to wait, C(N, A), derived from Erlang B."""
    if traffic == 0:
        return 0.0
    if agents <= traffic:
        return 1.0
    b = erlang_b(agents, traffic)
    c = agents * b / (agents - traffic * (1.0 - b))
    return min(1.0, max(0.0, c))


def service_level(
    agents: int,
    traffic: float,
    aht_seconds: float,
    threshold_seconds: float = DEFAULT_THRESHOLD_S,
) -> float:
    """Share of calls answered within the threshold: 1 - C * exp(-(N - A) * T / AHT)."""
    if traffic == 0:
        return 1.0
    if agents <= traffic:
        return 0.0
    c = erlang_c(agents, traffic)
    sl = 1.0 - c * math.exp(-(agents - traffic) * threshold_seconds / aht_seconds)
    return min(1.0, max(0.0, sl))


def average_speed_of_answer(agents: int, traffic: float, aht_seconds: float) -> float:
    """Mean wait of all offered calls, C * AHT / (N - A); infinity when ``N <= A``."""
    if traffic == 0:
        return 0.0
    if agents <= traffic:
        return math.inf
    return erlang_c(agents, traffic) * aht_seconds / (agents - traffic)


def occupancy(agents: int, traffic: float) -> float:
    """Share of paid time spent handling calls, A / N (above 1 means the queue cannot clear)."""
    if agents <= 0:
        return math.inf if traffic > 0 else 0.0
    return traffic / agents


def min_agents_for_occupancy(traffic: float, max_occupancy: float) -> int:
    """Smallest N with A / N <= max_occupancy."""
    if not 0 < max_occupancy <= 1:
        raise ValueError("max_occupancy must be in (0, 1]")
    if traffic == 0:
        return 0
    return math.ceil(traffic / max_occupancy - _EPS)


def required_agents(
    traffic: float,
    aht_seconds: float,
    target: float = DEFAULT_TARGET,
    threshold_seconds: float = DEFAULT_THRESHOLD_S,
    max_occupancy: float | None = DEFAULT_MAX_OCCUPANCY,
) -> int:
    """Fewest agents that meet the service-level target and, if given, the occupancy cap.

    The Erlang B recurrence is advanced one agent at a time, so the search costs O(N) in total.
    """
    if not 0 < target < 1:
        raise ValueError("target must be in (0, 1)")
    if traffic == 0:
        return 0
    floor_n = min_agents_for_occupancy(traffic, max_occupancy) if max_occupancy else 0
    b = 1.0
    n = 0
    while True:
        n += 1
        ab = traffic * b
        b = ab / (n + ab)
        if n <= traffic or n < floor_n:
            continue
        c = n * b / (n - traffic * (1.0 - b))
        sl = 1.0 - c * math.exp(-(n - traffic) * threshold_seconds / aht_seconds)
        if sl >= target - _EPS:
            return n


def scheduled_agents(required: int, shrinkage: float) -> int:
    """Agents to roster so that ``required`` remain after shrinkage: ceil(N / (1 - shrinkage))."""
    if not 0 <= shrinkage < 1:
        raise ValueError("shrinkage must be in [0, 1)")
    return math.ceil(round(required / (1.0 - shrinkage), 9))


@dataclass(frozen=True)
class StaffingResult:
    """Everything the ``erlang`` command reports for one interval."""

    calls: float
    aht_seconds: float
    interval_seconds: float
    traffic: float
    required: int
    service_level: float
    prob_wait: float
    asa_seconds: float
    occupancy: float
    scheduled: int
    service_level_one_fewer: float | None


def solve(
    calls: float,
    aht_seconds: float,
    interval_seconds: float = 1800.0,
    target: float = DEFAULT_TARGET,
    threshold_seconds: float = DEFAULT_THRESHOLD_S,
    max_occupancy: float | None = DEFAULT_MAX_OCCUPANCY,
    shrinkage: float = 0.0,
) -> StaffingResult:
    """Required agents and the service level they deliver for one interval."""
    a = traffic_erlangs(calls, aht_seconds, interval_seconds)
    n = required_agents(a, aht_seconds, target, threshold_seconds, max_occupancy)
    return StaffingResult(
        calls=calls,
        aht_seconds=aht_seconds,
        interval_seconds=interval_seconds,
        traffic=a,
        required=n,
        service_level=service_level(n, a, aht_seconds, threshold_seconds),
        prob_wait=erlang_c(n, a),
        asa_seconds=average_speed_of_answer(n, a, aht_seconds),
        occupancy=occupancy(n, a),
        scheduled=scheduled_agents(n, shrinkage),
        service_level_one_fewer=(
            service_level(n - 1, a, aht_seconds, threshold_seconds) if n > 0 else None
        ),
    )
