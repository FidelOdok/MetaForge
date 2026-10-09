---
title: Verifying the images
---

# Verifying the images you run

MetaForge publishes its container images to GitHub Container Registry, and
signs them. You self-host, so nobody verifies on your behalf — this page is how
you check for yourself that the image you are about to run is the one this
repository's CI built.

| Image | What it is |
|---|---|
| `ghcr.io/fidelodok/metaforge-gateway` | The gateway |
| `ghcr.io/fidelodok/metaforge-freecad-adapter` | FreeCAD tool adapter |
| `ghcr.io/fidelodok/metaforge-calculix-adapter` | CalculiX tool adapter |

## Verify a signature

Install [cosign](https://docs.sigstore.dev/cosign/installation/), then:

```bash
cosign verify \
  --certificate-identity-regexp '^https://github\.com/FidelOdok/MetaForge/\.github/workflows/' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  ghcr.io/fidelodok/metaforge-gateway:latest
```

It prints the signature's claims and exits `0`. An unsigned image, or one
signed by anything else, exits non-zero.

### Read what those two flags actually assert

They are the verification. Without them `cosign verify` will accept a signature
from *anyone* — the check becomes "this was signed", which is a sentence with no
subject.

- **`--certificate-oidc-issuer`** pins *who vouched for the signer's identity*:
  GitHub's OIDC provider, and nothing else.
- **`--certificate-identity-regexp`** pins *which identity*: a workflow in
  `FidelOdok/MetaForge`. Narrow it further if you want to require a specific
  workflow or branch, for example `.../\.github/workflows/release\.yml@refs/heads/main$`.

There is no public key anywhere in this, which is the point. Signing is keyless:
CI exchanges its GitHub OIDC token for a short-lived certificate from Sigstore's
CA, and the signature is recorded in a public transparency log. So there is no
MetaForge private key that can leak, and a signature proves a *build provenance*
claim rather than possession of a secret.

## Verify a digest, not a tag

A tag is a moving pointer. `:latest` today and `:latest` tomorrow are different
images, and verifying the tag only tells you the registry handed you something
signed *now*.

```bash
# Resolve the tag once, then pin it
digest=$(crane digest ghcr.io/fidelodok/metaforge-gateway:latest)
cosign verify \
  --certificate-identity-regexp '^https://github\.com/FidelOdok/MetaForge/\.github/workflows/' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  "ghcr.io/fidelodok/metaforge-gateway@${digest}"
```

Pin that digest in your compose file, and you are running a specific set of
bytes you verified, not whatever `:latest` means next week.

## Read the bill of materials

Each image carries an SBOM and build provenance as attestations. The SBOM is the
practical way to answer "does this ship the package in today's advisory",
which matters most for the adapter images — they carry an entire CAD or FEA
stack.

```bash
# What is inside it
cosign download sbom ghcr.io/fidelodok/metaforge-gateway:latest

# How it was built: source repository, commit, build arguments, base images
cosign verify-attestation --type slsaprovenance \
  --certificate-identity-regexp '^https://github\.com/FidelOdok/MetaForge/\.github/workflows/' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  ghcr.io/fidelodok/metaforge-gateway:latest
```

## What this does and does not tell you

**It tells you** the image was built by CI in this repository, from a known
commit, and has not been altered since — someone who compromises the registry
cannot substitute a different image without the signature failing.

**It does not tell you** the code was correct, reviewed, or free of
vulnerabilities. A signature is a statement about *origin*, not *quality*. The
SBOM is what you feed to a scanner to ask the second question.

## Nothing enforces this yet

Verification is available and documented; no part of MetaForge refuses to run an
unverified image today. That is deliberate — signatures need to exist and
accumulate before anything is gated on them, or the first enforcement locks
everyone out over an image predating the signing.

If you want to enforce it in your own deployment, run `cosign verify` in whatever
pulls your images and fail the deploy on a non-zero exit. In Kubernetes, an
admission policy such as [policy-controller](https://docs.sigstore.dev/policy-controller/overview/)
does the same at the cluster boundary.

## Why not `docker trust`

Docker Content Trust is being retired: the `notary.docker.io` service it depends
on closes **2026-12-08**. A signature made with it would outlive its own
verifier, so these images use Sigstore instead.
