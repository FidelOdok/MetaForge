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
]


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

    def test_cosign_is_installed(self, workflow: str, job_name: str) -> None:
        _step_with(_job(workflow, job_name), uses_prefix="sigstore/cosign-installer")

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
