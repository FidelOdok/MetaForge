"""Image signing survives a Sigstore Rekor outage, without weakening it (FORGE-823).

On 2026-11-07 the public Rekor returned 500s ("unexpected error
communicating with inactive shard") and three releases failed at the keyless
``cosign sign`` step, which blocked every image-only host from deploying.

``scripts/ci/sign_image.sh`` is now the one place every workflow signs
through. These drive it with a fake ``cosign`` so the behaviour is exercised
rather than read off the script's text:

* keyless is tried first, and retried;
* only if it keeps failing, and only if a key was provided, is the image
  signed with that key, without a transparency log entry, and the job says so;
* with no key it fails exactly as it did before;
* the key and its password never reach argv or the log.
"""

from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "sign_image.sh"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

REF = "ghcr.io/fidelodok/metaforge-gateway@sha256:" + "a" * 64
SECRET_KEY = "-----BEGIN ENCRYPTED SIGSTORE PRIVATE KEY-----TOPSECRET"
SECRET_PASSWORD = "hunter2-password"


def _fake_cosign(tmp_path: Path, *, keyless_fails: int, key_rc: int = 0) -> Path:
    """A cosign whose keyless sign fails ``keyless_fails`` times, then succeeds.

    Every call's argv is appended to ``calls``, one line per call.
    """
    (tmp_path / "count").write_text("0", encoding="utf-8")
    fake = tmp_path / "cosign"
    fake.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "$*" >> "{tmp_path.as_posix()}/calls"
            case "$*" in
              *--key*) exit {key_rc} ;;
            esac
            n=$(cat "{tmp_path.as_posix()}/count")
            echo $((n + 1)) > "{tmp_path.as_posix()}/count"
            if [ "$n" -lt {keyless_fails} ]; then
              echo 'giving up after 4 attempt(s): inactive shard' >&2
              exit 1
            fi
            exit 0
            """
        ),
        encoding="utf-8",
        newline="\n",
    )
    fake.chmod(0o755)
    return fake


def _run(
    tmp_path: Path,
    *,
    keyless_fails: int,
    key: str = "",
    key_rc: int = 0,
    ref: str = REF,
) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    fake = _fake_cosign(tmp_path, keyless_fails=keyless_fails, key_rc=key_rc)
    output = tmp_path / "github_output"
    env = {
        **os.environ,
        "COSIGN": fake.as_posix(),
        "SIGN_RETRY_DELAY": "0",
        "GITHUB_OUTPUT": output.as_posix(),
        "COSIGN_PRIVATE_KEY": key,
        "COSIGN_PASSWORD": SECRET_PASSWORD if key else "",
    }
    result = subprocess.run(
        ["bash", SCRIPT.as_posix(), ref],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    calls_file = tmp_path / "calls"
    calls = calls_file.read_text(encoding="utf-8").splitlines() if calls_file.exists() else []
    out = output.read_text(encoding="utf-8") if output.exists() else ""
    return result, calls, out


def test_keyless_success_signs_once_and_never_touches_the_key(tmp_path: Path) -> None:
    result, calls, out = _run(tmp_path, keyless_fails=0, key=SECRET_KEY)

    assert result.returncode == 0, result.stderr
    assert calls == [f"sign --yes {REF}"]
    assert "method=keyless" in out
    assert "::warning" not in result.stdout


def test_a_brief_rekor_blip_is_retried_rather_than_failed(tmp_path: Path) -> None:
    result, calls, out = _run(tmp_path, keyless_fails=2)

    assert result.returncode == 0, result.stderr
    assert len(calls) == 3
    assert all("--key" not in c for c in calls)
    assert "method=keyless" in out


def test_rekor_down_with_a_key_falls_back_to_the_key_and_warns(tmp_path: Path) -> None:
    result, calls, out = _run(tmp_path, keyless_fails=99, key=SECRET_KEY)

    assert result.returncode == 0, result.stderr
    assert len(calls) == 4, calls
    assert calls[-1] == f"sign --yes --key env://COSIGN_PRIVATE_KEY --tlog-upload=false {REF}"
    assert "::warning" in result.stdout
    assert "Rekor was unavailable" in result.stdout
    assert "method=key" in out


def test_rekor_down_without_a_key_fails_as_before(tmp_path: Path) -> None:
    result, calls, out = _run(tmp_path, keyless_fails=99, key="")

    assert result.returncode != 0
    assert len(calls) == 3
    assert all("--key" not in c for c in calls)
    assert "no COSIGN_PRIVATE_KEY" in result.stderr
    assert "method=" not in out


def test_a_failing_key_signature_fails_the_job(tmp_path: Path) -> None:
    result, _, out = _run(tmp_path, keyless_fails=99, key=SECRET_KEY, key_rc=1)

    assert result.returncode != 0
    assert "method=" not in out


def test_the_key_and_password_never_reach_argv_or_the_log(tmp_path: Path) -> None:
    result, calls, _ = _run(tmp_path, keyless_fails=99, key=SECRET_KEY)

    everything = result.stdout + result.stderr + "\n".join(calls)
    assert SECRET_KEY not in everything
    assert "TOPSECRET" not in everything
    assert SECRET_PASSWORD not in everything


@pytest.mark.parametrize(
    "ref",
    [
        "ghcr.io/fidelodok/metaforge-gateway:latest",
        # What a renamed build step produces: an empty digest.
        "ghcr.io/fidelodok/metaforge-gateway@",
    ],
)
def test_a_tag_or_an_empty_digest_is_refused(tmp_path: Path, ref: str) -> None:
    result, calls, _ = _run(tmp_path, keyless_fails=0, ref=ref)

    assert result.returncode == 2
    assert calls == []


def test_the_script_parses() -> None:
    subprocess.run(["bash", "-n", SCRIPT.as_posix()], check=True)


def _sign_steps() -> list[tuple[str, str, dict]]:
    """Every workflow step that runs cosign sign or the signing script."""
    found = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        data = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for job_name, job in (data.get("jobs") or {}).items():
            for step in job.get("steps", []):
                run = str(step.get("run", ""))
                if "cosign sign" in run or "sign_image.sh" in run:
                    found.append((wf.name, job_name, step))
    return found


def test_there_are_signing_sites_to_check() -> None:
    assert len(_sign_steps()) >= 4


@pytest.mark.parametrize(
    ("workflow", "job_name", "step"),
    _sign_steps(),
    ids=lambda v: v if isinstance(v, str) else "",
)
class TestEverySigningSiteUsesTheSharedScript:
    def test_it_calls_the_script_not_cosign_directly(
        self, workflow: str, job_name: str, step: dict
    ) -> None:
        run = str(step.get("run", ""))
        assert "cosign sign" not in run, f"{workflow}:{job_name} signs without the fallback"
        assert re.search(r"scripts/ci/sign_image\.sh \"\$\{IMAGE\}@\$\{DIGEST\}\"", run), run

    def test_the_fallback_secrets_are_passed_to_the_sign_step(
        self, workflow: str, job_name: str, step: dict
    ) -> None:
        env = step.get("env", {})
        assert env.get("COSIGN_PRIVATE_KEY") == "${{ secrets.COSIGN_PRIVATE_KEY }}"
        assert env.get("COSIGN_PASSWORD") == "${{ secrets.COSIGN_PASSWORD }}"

    def test_the_job_checks_out_the_script(self, workflow: str, job_name: str, step: dict) -> None:
        data = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
        steps = data["jobs"][job_name]["steps"]
        assert any(str(s.get("uses", "")).startswith("actions/checkout") for s in steps), (
            f"{workflow}:{job_name} runs sign_image.sh without checking it out"
        )

    def test_keyless_stays_possible(self, workflow: str, job_name: str, step: dict) -> None:
        data = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
        assert data["jobs"][job_name]["permissions"]["id-token"] == "write"


def test_no_private_key_is_committed() -> None:
    for name in ("cosign.key", "cosign.priv"):
        assert not (REPO_ROOT / name).exists(), f"{name} must never be committed"
