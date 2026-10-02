"""Every CI job must bound its own runtime (FORGE-467).

No job in any workflow set ``timeout-minutes``, so GitHub's 360-minute
default applied. The Tests job hung twice in one day on
``pytest tests/unit/ -x --tb=short -q`` -- 14.7 minutes on one PR and 47.6
on an unrelated one, both passing locally in about three minutes with the
identical command. Each time the PR simply sat there, because a stalled job
and a slow job look exactly the same when nothing fails.

That is the same shape as most of the bugs this surface has produced: a
failure with no alarm becomes a wait nobody questions. A timeout is the
alarm.
"""

from __future__ import annotations

import glob
from pathlib import Path
from typing import Any

import pytest
import yaml

_WORKFLOWS = sorted(glob.glob(".github/workflows/*.yml"))

#: Nothing here should take this long. A budget above it is not a budget --
#: it would let a hung job outlast the attention of whoever is waiting.
_CEILING_MINUTES = 60


def _jobs() -> list[tuple[str, str, dict[str, Any]]]:
    out: list[tuple[str, str, dict[str, Any]]] = []
    for path in _WORKFLOWS:
        data = yaml.safe_load(Path(path).read_text()) or {}
        for name, job in (data.get("jobs") or {}).items():
            out.append((path, name, job))
    return out


def test_there_are_workflows_to_check() -> None:
    """Guards the parametrised tests below from silently covering nothing."""
    assert _WORKFLOWS, "no workflow files found; this test has stopped testing anything"
    assert _jobs()


@pytest.mark.parametrize(
    ("path", "name", "job"), _jobs(), ids=[f"{p.split('/')[-1]}:{n}" for p, n, _ in _jobs()]
)
class TestEveryJobIsBounded:
    def test_it_sets_a_timeout(self, path: str, name: str, job: dict[str, Any]) -> None:
        assert "timeout-minutes" in job, (
            f"{path}:{name} has no timeout-minutes, so it can hang for GitHub's "
            "360-minute default with nothing reporting a failure"
        )

    def test_the_timeout_is_a_sane_budget(self, path: str, name: str, job: dict[str, Any]) -> None:
        minutes = job.get("timeout-minutes")
        assert isinstance(minutes, int), f"{path}:{name} timeout-minutes is not an integer"
        assert 1 <= minutes <= _CEILING_MINUTES, (
            f"{path}:{name} budgets {minutes} minutes; above {_CEILING_MINUTES} is not "
            "a bound, it is a longer silence"
        )
