"""A deploy that half-starts must not read as a success (FORGE-622).

The sandbox update failed this way for real: ``docker compose up`` returned
non-zero because the gateway had not reported healthy inside compose's own
wait, ``set -e`` ended the script on the spot, and the dashboard was left in
``Created`` -- never started, and invisible to a health check that only looks
at containers which have a healthcheck.

These drive ``compose_settle`` with a stub ``docker`` on PATH, so the
behaviour is exercised rather than asserted from the script's text.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "lib" / "compose_settle.sh"


def _run(
    tmp_path: Path,
    states: list[str],
    *,
    up_rc: int = 0,
    advance: str = "up",
) -> subprocess.CompletedProcess[str]:
    """Run compose_settle against a stub docker.

    ``states`` is one ``docker compose ps`` table per step, and the last entry
    repeats. ``advance`` says what moves the stack to the next one: ``"up"``
    for a stack that only changes when compose is asked to start it, ``"ps"``
    for one that is coming good on its own while being polled.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for i, table in enumerate(states):
        (tmp_path / f"state{i}").write_text(table, encoding="utf-8")
    (tmp_path / "step").write_text("0", encoding="utf-8")

    (bin_dir / "docker").write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            step=$(cat {tmp_path}/step)
            if [ "$2" = "ps" ]; then
              # Only the requested columns matter; emit the whole table and let
              # the caller's awk pick. Name State Health, space separated.
              table={tmp_path}/state$step
              [ -f "$table" ] || table={tmp_path}/state{len(states) - 1}
              while read -r name state health; do
                case "$*" in
                  *'{{{{.Name}}}} {{{{.State}}}}'*) echo "$name $state" ;;
                  *'{{{{.Name}}}} {{{{.Health}}}}'*) echo "$name $health" ;;
                esac
              done < "$table"
              if [ "{advance}" = "ps" ]; then echo "$((step + 1))" > {tmp_path}/step; fi
              exit 0
            fi
            if [ "$2" = "up" ]; then
              echo up >> {tmp_path}/ups
              if [ "{advance}" = "up" ]; then echo "$((step + 1))" > {tmp_path}/step; fi
              exit {up_rc}
            fi
            exit 0
            """
        ),
        encoding="utf-8",
    )
    (bin_dir / "docker").chmod(0o755)

    script = f'. "{LIB}"; compose_settle 3 0'
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
        timeout=60,
    )


HEALTHY = "gateway running healthy\ndashboard running \n"
# What the box actually looked like: the gateway came good, the dashboard was
# created and never started, and it has no healthcheck to betray it.
STRANDED = "gateway running healthy\ndashboard created \n"


def test_a_settled_stack_passes_without_touching_compose(tmp_path: Path) -> None:
    result = _run(tmp_path, [HEALTHY])

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "ups").exists(), "a settled stack should not be restarted"


def test_a_container_that_never_started_is_started(tmp_path: Path) -> None:
    # Stranded on the first look; the retry starts it.
    result = _run(tmp_path, [STRANDED, HEALTHY])

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "ups").read_text(encoding="utf-8").count("up") == 1
    assert "not started, retrying" in result.stderr


def test_a_stack_that_never_settles_fails_and_names_the_service(tmp_path: Path) -> None:
    result = _run(tmp_path, [STRANDED])

    assert result.returncode == 1
    assert "stack did not settle" in result.stderr
    # The whole point: a container with no healthcheck must still be reported.
    assert "not running: dashboard (created)" in result.stderr


def test_an_unhealthy_container_is_reported_even_while_running(tmp_path: Path) -> None:
    result = _run(tmp_path, ["gateway running unhealthy\n"])

    assert result.returncode == 1
    assert "not healthy: gateway (unhealthy)" in result.stderr


def test_a_slow_dependency_is_waited_out_rather_than_failed(tmp_path: Path) -> None:
    # Starting, starting, then healthy: no retry needed, no failure.
    result = _run(
        tmp_path,
        [
            "gateway running starting\ndashboard running \n",
            "gateway running starting\ndashboard running \n",
            HEALTHY,
        ],
        advance="ps",
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "ups").exists()


@pytest.mark.parametrize("rc", [0, 1])
def test_the_retry_does_not_abort_when_compose_itself_fails(tmp_path: Path, rc: int) -> None:
    # compose returning non-zero is exactly the case that used to end the
    # deploy; it must not stop the settle from reaching its verdict.
    result = _run(tmp_path, [STRANDED, HEALTHY], up_rc=rc)

    assert result.returncode == 0, result.stderr
