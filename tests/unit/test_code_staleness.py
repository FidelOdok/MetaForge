"""Can this process say what code it is running (FORGE-411)?

The `mcp-http` sidecar on fidel-dev served two-day-old code while the checkout
beside it sat on current `main`. The symptoms -- an older protocol version, a
shorter tool list -- read as a smaller deployment, so four bugs were filed
against that run, and two of the findings in them were not real.

The expensive part of that was not the stale image. It was that staleness was
*invisible*: establishing it took container uptime, a protocol version compared
against the source, and inference. These tests pin the three properties that
make it answerable in one call instead.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from metaforge.mcp.build_info import BUILD_SHA_ENV, CodeVersion, code_version

_A = "a" * 40
_B = "b" * 40


class TestStalenessIsOnlyClaimedWhenProven:
    def test_a_mismatch_on_a_non_reloading_process_is_stale(self) -> None:
        assert CodeVersion(build_sha=_A, source_sha=_B, reloads=False).stale is True

    def test_a_match_is_not_stale(self) -> None:
        assert CodeVersion(build_sha=_A, source_sha=_A, reloads=False).stale is False

    def test_unknown_is_not_stale(self) -> None:
        """An alert that is always firing is an alert nobody reads, which is
        the failure being fixed rather than one to repeat. A deployment that
        bakes no build SHA must not report a problem on every health check."""
        assert CodeVersion(build_sha=None, source_sha=_B, reloads=False).stale is False
        assert CodeVersion(build_sha=_A, source_sha=None, reloads=False).stale is False

    def test_but_unknown_is_not_current_either(self) -> None:
        """The distinction the metric needs. Reporting "current" when the
        question cannot be answered is exactly the silent pass this module
        exists to end, so `result` is three-valued where `stale` is a bool."""
        assert CodeVersion(build_sha=None, source_sha=_B, reloads=False).result == "unknown"
        assert CodeVersion(build_sha=_A, source_sha=_A, reloads=False).result == "current"
        assert CodeVersion(build_sha=_A, source_sha=_B, reloads=False).result == "stale"

    def test_a_reloading_process_is_never_stale(self) -> None:
        """The gateway runs under uvicorn's reloader, so a difference between
        the baked SHA and the mount is expected and transient. Only the
        sidecar -- which calls uvicorn programmatically and has no reloader --
        keeps running its image no matter what the mount says."""
        assert CodeVersion(build_sha=_A, source_sha=_B, reloads=True).stale is False
        assert CodeVersion(build_sha=_A, source_sha=_B, reloads=True).result == "current"


class TestTheReportSaysWhatToDo:
    def test_a_stale_report_names_the_fix_and_the_symptoms(self) -> None:
        report = CodeVersion(build_sha=_A, source_sha=_B, reloads=False).report()
        detail = str(report["detail"])
        assert "restart" in detail.lower()
        # The sentence that would have saved the four bug reports.
        assert "smaller deployment" in detail

    def test_an_undetectable_report_says_so_rather_than_passing_quietly(self) -> None:
        report = CodeVersion(build_sha=None, source_sha=_B, reloads=False).report()
        assert report["stale"] is False
        assert BUILD_SHA_ENV in str(report["detail"])

    def test_shas_are_shortened_but_not_invented(self) -> None:
        report = CodeVersion(build_sha=_A, source_sha=None, reloads=False).report()
        assert report["build_sha"] == _A[:12]
        assert report["source_sha"] == "unknown"

    def test_reloads_is_reported_not_left_to_be_inferred(self) -> None:
        """Without it, a reader seeing two different SHAs and `stale: false`
        has no way to tell a reloading process from a broken check."""
        assert CodeVersion(_A, _B, reloads=True).report()["reloads"] is True


class TestItReadsTheRealCheckout:
    def test_the_source_sha_is_this_worktree_s_head(self, monkeypatch: Any) -> None:
        monkeypatch.delenv(BUILD_SHA_ENV, raising=False)
        version = code_version()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()
        assert version.source_sha == head

    def test_it_does_not_need_the_git_binary(self, tmp_path: Path, monkeypatch: Any) -> None:
        """The container may not ship git, and a diagnostic that depends on a
        tool the image might omit is a diagnostic that silently stops
        working. `.git` is read directly."""
        from metaforge.mcp.build_info import _git_head

        git_dir = tmp_path / ".git"
        (git_dir / "refs" / "heads").mkdir(parents=True)
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        (git_dir / "refs" / "heads" / "main").write_text(f"{_A}\n")

        monkeypatch.setattr(
            "metaforge.mcp.build_info._git_binary",
            lambda root: pytest.fail("fell back to the git binary"),
        )
        assert _git_head(tmp_path) == _A

    def test_a_detached_head_reads_as_the_commit(self, tmp_path: Path) -> None:
        from metaforge.mcp.build_info import _git_head

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text(f"{_B}\n")
        assert _git_head(tmp_path) == _B

    def test_an_unreadable_checkout_reports_unknown_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        from metaforge.mcp.build_info import _git_head

        assert _git_head(tmp_path / "nope") is None

    def test_the_env_var_is_what_ci_bakes_in(self, monkeypatch: Any) -> None:
        monkeypatch.setenv(BUILD_SHA_ENV, _A)
        assert code_version().build_sha == _A

    def test_a_blank_env_var_counts_as_absent(self, monkeypatch: Any) -> None:
        """An unset `ARG` expands to the empty string, not to nothing. Treating
        that as a SHA would make every image look mismatched."""
        monkeypatch.setenv(BUILD_SHA_ENV, "   ")
        assert code_version().build_sha is None


@pytest.mark.asyncio
class TestHealthReportsIt:
    async def _report(self, **kwargs: Any) -> dict[str, Any]:
        from metaforge.mcp.server import UnifiedMcpServer

        server = UnifiedMcpServer(adapters=[], **kwargs)
        raw = await server.handle_request(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "health/check", "params": {}})
        )
        return dict(json.loads(raw)["result"])

    async def test_the_health_call_carries_the_code_version(self) -> None:
        report = await self._report()
        assert "code" in report
        assert report["code"]["source_sha"] != "unknown"

    async def test_a_stale_process_is_not_reported_as_healthy(self, monkeypatch: Any) -> None:
        """It answers every request correctly -- for code nobody is looking
        at. Calling that "healthy" is what made the original bug invisible."""
        monkeypatch.setenv(BUILD_SHA_ENV, _A)
        report = await self._report()
        assert report["code"]["stale"] is True
        assert report["status"] == "stale"

    async def test_a_current_process_is_healthy(self, monkeypatch: Any) -> None:
        monkeypatch.delenv(BUILD_SHA_ENV, raising=False)
        report = await self._report()
        assert report["status"] == "healthy"

    async def test_the_skew_is_also_counted_so_an_alert_can_fire(self, monkeypatch: Any) -> None:
        """A health field only helps somebody already looking at it. The point
        of FORGE-411 is to be told without looking."""
        monkeypatch.setenv(BUILD_SHA_ENV, _A)
        recorded: list[tuple[str, bool]] = []

        class _Metrics:
            def record_mcp_adapter_probe(self, adapter_id: str, reachable: bool) -> None:
                pass

            def record_mcp_code_version(self, result: str, reloads: bool = False) -> None:
                recorded.append((result, reloads))

        await self._report(metrics=_Metrics())
        assert recorded == [("stale", False)]


class TestTheAlertAndTheMetricAgree:
    def test_the_alert_selects_a_label_the_metric_declares(self) -> None:
        """The failure mode this guards: an alert whose selector matches no
        series returns zero results with no error, which reads exactly like
        "nothing is wrong". Already hit once on Loki's `severity_text`."""
        import yaml

        from observability.metrics import MetricsRegistry

        definition = MetricsRegistry.MCP_CODE_VERSION_CHECK_TOTAL
        rules = yaml.safe_load(Path("observability/alerting/rules.yaml").read_text())
        exprs = [
            r["expr"]
            for group in rules["groups"]
            for r in group["rules"]
            if r["alert"] in {"McpRunningStaleCode", "McpCodeVersionUndetectable"}
        ]
        assert len(exprs) == 2
        for expr in exprs:
            assert definition.name in expr
            assert 'result="' in expr
        assert "result" in definition.labels

    def test_both_results_the_alerts_select_are_ones_the_code_can_produce(self) -> None:
        produced = {
            CodeVersion(_A, _B, reloads=False).result,
            CodeVersion(None, _B, reloads=False).result,
        }
        assert produced == {"stale", "unknown"}


class TestTheDeployMakesTheCheckPossible:
    """The check above can only work if the build stamps a SHA and the sidecar
    stops being built locally. Both are one-line changes that nothing else
    would notice if they were reverted, which is why they are asserted here."""

    def test_ci_passes_the_commit_into_the_image(self) -> None:
        import yaml

        workflow = yaml.safe_load(Path(".github/workflows/release.yml").read_text())
        steps = [
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if "build-push-action" in str(step.get("uses", ""))
        ]
        assert steps, "no image build step found"
        for step in steps:
            build_args = str(step["with"].get("build-args", ""))
            assert BUILD_SHA_ENV in build_args, (
                "the image is published without a build SHA, so staleness "
                "reports 'cannot be detected' on every deployment"
            )
            assert "github.sha" in build_args

    def test_the_dockerfile_accepts_it(self) -> None:
        dockerfile = Path("Dockerfile").read_text()
        assert f"ARG {BUILD_SHA_ENV}" in dockerfile
        # The ARG alone is build-time only; the process reads the env var.
        assert f"ENV {BUILD_SHA_ENV}=${{{BUILD_SHA_ENV}}}" in dockerfile

    def test_the_sidecar_runs_the_published_image_not_a_local_build(self) -> None:
        """The two were built from the same Dockerfile by different means, so
        they could diverge without anything saying so -- and a stray
        `docker compose build mcp-http` would clobber a hand-applied fix."""
        import yaml

        compose = yaml.safe_load(Path("docker-compose.override.yml").read_text())
        sidecar = compose["services"]["mcp-http"]
        assert "build" not in sidecar
        assert sidecar["image"].startswith("ghcr.io/")
