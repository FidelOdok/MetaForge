"""Fetch CAD-Coder's validation split and re-derive the committed sample.

Mirrors ``scripts/datasheets/fetch_and_extract.py``'s role: the full source
file lives in a gitignored ``.cache/cad_bench/`` (large, easy to re-fetch),
only the small deterministic sample this suite actually runs against is
committed (``samples/cad_coder_validation_sample.json``, see manifest.yaml).

    python evals/cad_bench/fetch_dataset.py            # fetch + verify sha256
    python evals/cad_bench/fetch_dataset.py --resample # also regenerate the sample
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.yaml"
CACHE_DIR = REPO_ROOT / ".cache" / "cad_bench"
SOURCE_URL = (
    "https://huggingface.co/datasets/gudo7208/CAD-Coder/resolve/main/cad_data_validation.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(manifest: dict) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / manifest["file"]
    if dest.exists() and _sha256(dest) == manifest["file_sha256"]:
        print(f"cache hit, sha256 verified: {dest}")
        return dest

    print(f"fetching {SOURCE_URL} -> {dest}")
    with urllib.request.urlopen(SOURCE_URL, timeout=120) as resp:  # noqa: S310
        dest.write_bytes(resp.read())

    actual = _sha256(dest)
    if actual != manifest["file_sha256"]:
        print(
            f"WARNING: sha256 mismatch -- manifest pins {manifest['file_sha256']}, "
            f"got {actual}. The upstream file may have changed; if this is expected, "
            "update manifest.yaml's file_sha256 after reviewing what changed.",
            file=sys.stderr,
        )
    else:
        print(f"sha256 verified: {actual}")
    return dest


def resample(manifest: dict, full_path: Path) -> Path:
    data = json.loads(full_path.read_text())
    rng = random.Random(manifest["sample_seed"])
    idx = sorted(rng.sample(range(len(data)), manifest["sample_count"]))

    sample = []
    for i in idx:
        entry = data[i]
        sample.append(
            {
                "id": f"cad_coder_val_{i:05d}",
                "source_index": i,
                "model_path": entry.get("model_path"),
                "prompt": entry["messages"][0]["content"],
                "reference_cadquery": entry["messages"][1]["content"],
            }
        )

    out_path = Path(__file__).resolve().parent / manifest["sample"]
    out_path.write_text(json.dumps(sample, indent=2))
    print(f"wrote {len(sample)} samples -> {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resample",
        action="store_true",
        help="Also regenerate samples/cad_coder_validation_sample.json (deterministic, "
        "same seed -- only useful after intentionally changing sample_seed/sample_count "
        "in manifest.yaml)",
    )
    args = parser.parse_args()

    manifest = yaml.safe_load(MANIFEST_PATH.read_text())
    full_path = fetch(manifest)
    if args.resample:
        resample(manifest, full_path)


if __name__ == "__main__":
    main()
