"""Published images are signed, and signed in the way that means something
(FORGE-585).

No image was signed at all before this. `docker trust` was not the answer:
Docker Content Trust is being retired and notary.docker.io closes
2026-12-08, so a signature made with it would outlive its own verifier.

Sigstore keyless signing replaces the private key with an identity. Cosign
exchanges the job's GitHub OIDC token for a short-lived Fulcio certificate,
so what a verifier checks is not "somebody held the key" but "this workflow,
in this repository, produced this digest".

Three things make that claim true rather than decorative, and each is one
line that is easy to lose in an edit:

* ``id-token: write`` on the publishing job. Without it the OIDC exchange
  fails -- loudly, but only in CI.
* signing a **digest**, never a tag. `:latest` is a moving pointer; a
  signature bound to it says nothing about what it points at tomorrow.
* the build step keeping ``id: build``, because the digest comes from its
  output. Rename it and ``${{ steps.build.outputs.digest }}`` silently
  becomes the empty string.

Structural assertions on the workflow files; no Docker, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every job that pushes an image to a registry, as (workflow, job).
PUBLISHING_JOBS = [
    (".github/workflows/release.yml", "docker-publish"),
    (".github/workflows/adapter-images.yml", "publish"),
    # FORGE-593: the OCCT converter and the production dashboard.
    (".github/workflows/adapter-images.yml", "publish-services"),
]

#: The cosign installer every publishing job uses (FORGE-838).
INSTALL_COSIGN = "./.github/actions/install-cosign"
INSTALL_COSIGN_ACTION = REPO_ROOT / ".github" / "actions" / "install-cosign" / "action.yml"


def _job(workflow: str, name: str) -> dict:
    data = yaml.safe_load((REPO_ROOT / workflow).read_text(encoding="utf-8"))
    assert name in data["jobs"], f"{workflow} has no job {name!r}"
    return data["jobs"][name]


def _steps(job: dict) -> list[dict]:
    return job.get("steps", [])


def _step_with(job: dict, *, uses_prefix: str | None = None, name: str | None = None) -> dict:
    for step in _steps(job):
        if uses_prefix and str(step.get("uses", "")).startswith(uses_prefix):
            return step
        if name and step.get("name") == name:
            return step
    raise AssertionError(f"no step matching uses={uses_prefix!r} name={name!r}")


@pytest.mark.parametrize(("workflow", "job_name"), PUBLISHING_JOBS)
class TestEveryPublishedImageIsSigned:
    def test_the_job_can_mint_a_signing_identity(self, workflow: str, job_name: str) -> None:
        """``id-token: write``. Keyless signing has no key to fall back on."""
        job = _job(workflow, job_name)
        assert job.get("permissions", {}).get("id-token") == "write", (
            f"{workflow}:{job_name} cannot request an OIDC token, so cosign cannot sign"
        )

    def test_cosign_is_installed_before_anything_is_pushed(
        self, workflow: str, job_name: str
    ) -> None:
        """FORGE-838: the pinned, retrying installer, ahead of the build. A
        cosign install that 504'd after the push left an unsigned image."""
        job = _job(workflow, job_name)
        uses = [str(s.get("uses", "")) for s in _steps(job)]
        assert INSTALL_COSIGN in uses, f"{workflow}:{job_name} does not use {INSTALL_COSIGN}"
        assert not any(u.startswith("sigstore/cosign-installer") for u in uses), (
            "sigstore/cosign-installer downloads cosign with one unretried curl"
        )
        build = next(i for i, u in enumerate(uses) if u.startswith("docker/build-push-action"))
        assert uses.index(INSTALL_COSIGN) < build

    def test_nothing_is_tagged_until_it_is_signed(self, workflow: str, job_name: str) -> None:
        """FORGE-838: push by digest, sign, then tag. Tagging in the build step
        moved :latest to an image a host would refuse when signing failed."""
        job = _job(workflow, job_name)
        build = _step_with(job, uses_prefix="docker/build-push-action")
        with_ = build.get("with", {})
        assert "tags" not in with_, f"{workflow}:{job_name} tags the image as it pushes"
        assert with_.get("push") is not True, with_
        outputs = str(with_.get("outputs", ""))
        assert "push-by-digest=true" in outputs and "push=true" in outputs, outputs
        names = [s.get("name") for s in _steps(job)]
        assert names.index("Sign the pushed image") < names.index("Tag the signed image")
        tag = _step_with(job, name="Tag the signed image")
        assert "@${DIGEST}" in tag["run"] and "imagetools create" in tag["run"]
        assert "steps.build.outputs.digest" in tag["env"]["DIGEST"]
        # The tags must name the very digest that was signed.
        assert "exit 1" in tag["run"] and "Manifest.Digest" in tag["run"]

    def test_layers_are_cached_in_the_registry(self, workflow: str, job_name: str) -> None:
        """FORGE-838: the Actions cache's 10 GB could not hold every image, so
        layers that never change were rebuilt and re-downloaded."""
        with_ = _step_with(_job(workflow, job_name), uses_prefix="docker/build-push-action")["with"]
        assert "type=registry" in str(with_.get("cache-from", ""))
        cache_to = str(with_.get("cache-to", ""))
        assert cache_to.startswith("type=registry") and ":buildcache" in cache_to
        assert "mode=max" in cache_to

    def test_the_signature_covers_a_digest_not_a_tag(self, workflow: str, job_name: str) -> None:
        """The assertion this whole file exists for.

        Signing `image:latest` is worse than not signing: it looks like a
        guarantee and is a promise about a pointer.
        """
        step = _step_with(_job(workflow, job_name), name="Sign the pushed image")
        run = step.get("run", "")
        assert "@${DIGEST}" in run, f"{workflow}:{job_name} does not sign by digest: {run!r}"
        assert ":latest" not in run and ":${{" not in run, (
            f"{workflow}:{job_name} appears to sign a tag: {run!r}"
        )

    def test_the_digest_comes_from_a_step_that_still_has_that_id(
        self, workflow: str, job_name: str
    ) -> None:
        """``steps.build.outputs.digest`` is an empty string if the build step
        is renamed -- cosign would then be handed ``image@``."""
        job = _job(workflow, job_name)
        step = _step_with(job, name="Sign the pushed image")
        referenced = step.get("env", {}).get("DIGEST", "")
        assert "steps.build.outputs.digest" in referenced, referenced
        build = _step_with(job, uses_prefix="docker/build-push-action")
        assert build.get("id") == "build", (
            f"{workflow}:{job_name} build step has id {build.get('id')!r}, "
            "so the digest reference resolves to nothing"
        )

    def test_the_image_carries_provenance_and_an_sbom(self, workflow: str, job_name: str) -> None:
        """A signature says who built it; the attestations say what it is."""
        build = _step_with(_job(workflow, job_name), uses_prefix="docker/build-push-action")
        with_ = build.get("with", {})
        assert with_.get("provenance") == "mode=max", with_.get("provenance")
        assert with_.get("sbom") is True, with_.get("sbom")


class TestNoDeadSigningScheme:
    """Docker Content Trust's service closes 2026-12-08. Anything that grows a
    dependency on it is building on a dated foundation."""

    @pytest.mark.parametrize(("workflow", "job_name"), PUBLISHING_JOBS)
    def test_docker_content_trust_is_not_used(self, workflow: str, job_name: str) -> None:
        text = (REPO_ROOT / workflow).read_text(encoding="utf-8")
        for dead in ("docker trust sign", "DOCKER_CONTENT_TRUST=1"):
            assert dead not in text, f"{workflow} uses {dead!r}, which stops working 2026-12-08"


class TestTheCosignInstallerSurvivesA504:
    """FORGE-838: GitHub release downloads returned 504 three runs in a row and
    failed a different set of publishing jobs each time."""

    def _env_and_run(self) -> tuple[dict, str]:
        data = yaml.safe_load(INSTALL_COSIGN_ACTION.read_text(encoding="utf-8"))
        (step,) = data["runs"]["steps"]
        return step["env"], step["run"]

    def test_the_version_and_its_checksum_are_pinned(self) -> None:
        env, run = self._env_and_run()
        assert str(env["COSIGN_VERSION"]).startswith("v")
        assert len(str(env["COSIGN_SHA256"])) == 64
        assert "sha256sum -c" in run

    def test_the_download_is_retried_on_any_error(self) -> None:
        _, run = self._env_and_run()
        assert "--retry-all-errors" in run and "--retry " in run

    def test_the_fallback_image_is_pinned_by_digest(self) -> None:
        env, _ = self._env_and_run()
        assert "@sha256:" in str(env["COSIGN_IMAGE"])

    def test_the_mirror_uses_it_too(self) -> None:
        job = _job(".github/workflows/mirror-images.yml", "mirror")
        uses = [str(s.get("uses", "")) for s in _steps(job)]
        assert INSTALL_COSIGN in uses
        assert not any(u.startswith("sigstore/cosign-installer") for u in uses)
