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
| `ghcr.io/fidelodok/metaforge-cadquery-adapter` | CadQuery tool adapter |
| `ghcr.io/fidelodok/metaforge-kicad-adapter` | KiCad tool adapter |
| `ghcr.io/fidelodok/metaforge-spice-adapter` | SPICE tool adapter |
| `ghcr.io/fidelodok/metaforge-slicer-adapter` | PrusaSlicer tool adapter (G-code and print estimates) |
| `ghcr.io/fidelodok/metaforge-openfoam-adapter` | OpenFOAM flow solver adapter (opt-in, `cfd` compose profile; the base is the OpenFOAM Foundation release image, pinned by digest) |
| `ghcr.io/fidelodok/metaforge-firmware-adapter` | Firmware build and emulation adapter (opt-in, `firmware` compose profile; PlatformIO with the STM32 and ESP32 platforms, Renode, Espressif QEMU) |
| `ghcr.io/fidelodok/metaforge-occt` | STEP to GLB converter for the viewer |
| `ghcr.io/fidelodok/metaforge-dashboard` | The production dashboard (static build behind nginx) |
| `ghcr.io/fidelodok/metaforge-seaweedfs` | The object store: a byte-for-byte copy of the signed SeaweedFS release, mirrored by `mirror-images.yml` |

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

## Where it is enforced

The image-only host update, `scripts/host_update.sh`, verifies every MetaForge
image it pulls by digest, with the two flags above, and restarts nothing if any
check fails. See [Running a host from images](image-only-host.md).
Nothing else refuses an unverified image: `docker compose up` on a checkout
still runs whatever it has.

If you want to enforce it elsewhere in your own deployment, run `cosign verify` in whatever
pulls your images and fail the deploy on a non-zero exit. In Kubernetes, an
admission policy such as [policy-controller](https://docs.sigstore.dev/policy-controller/overview/)
does the same at the cluster boundary.

## Why not `docker trust`

Docker Content Trust is being retired: the `notary.docker.io` service it depends
on closes **2026-12-08**. A signature made with it would outlive its own
verifier, so these images use Sigstore instead.
