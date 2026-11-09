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
| `ghcr.io/fidelodok/metaforge-render-adapter` | Browser render adapter (model views of a GLB in headless Chromium with three.js, for the CAD design loop) |
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

There is no public key anywhere in this check, which is the point. Signing is keyless:
CI exchanges its GitHub OIDC token for a short-lived certificate from Sigstore's
CA, and the signature is recorded in a public transparency log. So there is no
MetaForge private key that can leak, and a signature proves a *build provenance*
claim rather than possession of a secret.

## Two kinds of signature

Almost every image carries the keyless signature above. Keyless signing needs
Sigstore's public services, and on 2026-11-07 the Rekor transparency log
returned `500 unexpected error communicating with inactive shard` for long
enough that three releases failed to sign, which blocked every image-only host
from deploying.

So CI has a fallback. Every publishing workflow signs through
`scripts/ci/sign_image.sh`, which:

1. tries keyless signing, three times over about a minute;
2. if that still fails and the repository has a `COSIGN_PRIVATE_KEY` secret,
   signs with that key instead, with no transparency log entry, and marks the
   run with a warning saying the image was key-signed because Rekor was
   unavailable;
3. otherwise fails the job, as it always did.

| | Keyless (normal) | Key (fallback) |
|---|---|---|
| Proves | this repository's CI workflow built this digest | the holder of the MetaForge fallback key signed this digest |
| Verified with | the identity and issuer flags | `cosign.pub` from the repository root |
| Public log entry | yes, in Rekor | no |
| Needs Sigstore at signing time | yes | no |

A given image has one or the other. Check keyless first; if it fails, check the
key.

### Verify a key-signed image

Take `cosign.pub` from the root of this repository, from a commit you trust,
and keep your own copy. Then:

```bash
cosign verify --key cosign.pub --insecure-ignore-tlog=true \
  "ghcr.io/fidelodok/metaforge-gateway@${digest}"
```

`--insecure-ignore-tlog=true` is required because the fallback signature was
made precisely when the log was unavailable, so there is no entry to find.
Until `cosign.pub` exists in the repository, no image is key-signed and every
image must pass the keyless check.

### What the fallback gives up

- **No public record.** A keyless signature is logged where anyone can see
  it, so a signature made with stolen credentials is discoverable. A key
  signature made without the log is not: if the key leaked, a forged signature
  would leave no public trace.
- **Key custody.** The private key lives only as the `COSIGN_PRIVATE_KEY`
  repository secret, protected by `COSIGN_PASSWORD`. Anyone who can run a
  workflow with access to repository secrets can use it.
- **Rotation is manual.** Rotating means generating a new pair, replacing both
  secrets, committing the new `cosign.pub`, and replacing the copy on every
  host. Images signed with the old key stop verifying against the new one, so
  rotate after the images signed with it have been superseded, or keep the old
  public key on hosts until then.

The keyless path is unchanged, and a host only accepts a key signature if its
operator placed the public key there.

### Create the fallback key (maintainers)

Run once, on a trusted machine with [cosign](https://docs.sigstore.dev/cosign/installation/)
and the GitHub CLI, from the repository root:

```bash
cosign generate-key-pair                    # prompts for a password; writes cosign.key and cosign.pub
gh secret set COSIGN_PRIVATE_KEY < cosign.key
gh secret set COSIGN_PASSWORD               # paste the same password at the prompt
git add cosign.pub && git commit -m "chore: add the cosign fallback public key"
rm cosign.key                               # or move it to offline storage
```

Never commit `cosign.key`; `.gitignore` excludes it. CI never needs the file
again, because the secret holds it. Then give each host the new `cosign.pub`
(see [Running a host from images](image-only-host.md#accepting-key-signed-images)).

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

### Tags move only after signing

CI pushes each image by digest, signs that digest, and only then points
`:latest` and `:<commit sha>` at it. A build whose signing fails leaves both
tags on the previous signed image, so a tag never names an unsigned digest.
See [Signing order](image-only-host.md#signing-order).

Each package also has a `:buildcache` tag. It holds CI's layer cache, not an
image: it is not signed and cannot be run, so `cosign verify` on it fails by
design. Do not pull it.

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
check fails. If the keyless check fails and the host holds a `cosign.pub`, it
accepts a key signature instead. See [Running a host from images](image-only-host.md).
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
