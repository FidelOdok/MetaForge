"""Fetch real public datasheets and extract their text into committed
fixtures used by the tier-1 UAT scenarios in
``tests/uat/scenarios/tier1/datasheets-real.md``.

Reads ``tests/fixtures/datasheets/manifest.yaml``. For each entry:

1. Downloads the PDF to ``.cache/datasheets/<mpn-lower>.pdf`` if not
   already cached. The cache directory is gitignored.
2. Computes the SHA-256 of the PDF. If ``manifest.yaml`` has a pinned
   ``pdf_sha256``, mismatches abort the run with exit 2 (manufacturer
   has revised the document upstream — fixture review needed).
3. Extracts text with ``pdfplumber`` and writes
   ``tests/fixtures/datasheets/<mpn-lower>.txt``.
4. Computes the SHA-256 of the produced text. Same mismatch policy as
   above — if the local extract drifts, the script aborts so we
   never silently update committed fixtures.
5. When both fields were empty, fills them in and rewrites
   ``manifest.yaml`` so a follow-up run is idempotent.

The script is fixture-prep only. It is **not** part of the production
ingest path (see ``KnowledgeService`` / ``raganything`` for that). It
is decoupled on purpose — see ``docs/uat/kb-test-plan.md`` KB-CLI-003
and the plan's "Out of scope" notes.

Usage:
    pip install -e ".[dev]"
    python scripts/datasheets/fetch_and_extract.py
    # optional: --only RP2040  (case-insensitive substring match on mpn)
    # optional: --refresh-pdf  (delete cached PDFs and refetch)
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "datasheets" / "manifest.yaml"
CACHE_DIR = REPO_ROOT / ".cache" / "datasheets"

USER_AGENT = (
    "MetaForge-UAT-Fixture-Fetcher/1.0 "
    "(https://github.com/MetaForge-HA/MetaForge; technical-reference fixture prep)"
)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_pdf(url: str, dest: Path, *, refresh: bool) -> None:
    if dest.exists() and not refresh:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    print(f"  fetching {url}")
    with urlopen(req, timeout=60) as resp, dest.open("wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def extract_text(pdf_path: Path) -> str:
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append(f"\n\n## Page {i}\n\n{text}\n")
    return "".join(pages)


def process_entry(entry: dict[str, Any], *, only: str | None, refresh: bool) -> bool:
    """Returns True if the entry was modified (sha256 fields filled in)."""
    mpn = entry["mpn"]
    if only and only.lower() not in mpn.lower():
        return False

    print(f"\n[{mpn}] {entry['vendor']} — {entry['family']}")

    cache_pdf = CACHE_DIR / f"{mpn.lower()}.pdf"
    fetch_pdf(entry["source_url"], cache_pdf, refresh=refresh)

    actual_pdf_sha = sha256_of(cache_pdf)
    pinned_pdf_sha = entry.get("pdf_sha256") or ""
    if pinned_pdf_sha and pinned_pdf_sha != actual_pdf_sha:
        sys.stderr.write(
            f"  ERROR: PDF sha256 mismatch for {mpn}\n"
            f"    pinned: {pinned_pdf_sha}\n"
            f"    actual: {actual_pdf_sha}\n"
            f"  The manufacturer has revised the upstream document. Review the\n"
            f"  new PDF, update gt.yaml expected_substring values for any spec\n"
            f"  that changed, then clear the pinned sha256 in manifest.yaml and\n"
            f"  re-run this script.\n"
        )
        sys.exit(2)

    text_path = REPO_ROOT / entry["text_path"]
    text_path.parent.mkdir(parents=True, exist_ok=True)
    extracted = extract_text(cache_pdf)
    if not extracted.strip():
        sys.stderr.write(f"  ERROR: no text extracted from {cache_pdf}\n")
        sys.exit(3)
    text_path.write_text(extracted, encoding="utf-8")

    actual_text_sha = sha256_of(text_path)
    pinned_text_sha = entry.get("text_sha256") or ""
    if pinned_text_sha and pinned_text_sha != actual_text_sha:
        sys.stderr.write(
            f"  ERROR: extracted-text sha256 mismatch for {mpn}\n"
            f"    pinned: {pinned_text_sha}\n"
            f"    actual: {actual_text_sha}\n"
            f"  pdfplumber's output drifted (library upgrade, OS-locale, etc).\n"
            f"  Diff the new {text_path.relative_to(REPO_ROOT)} against the old\n"
            f"  one, accept the changes you trust, then clear text_sha256 in\n"
            f"  manifest.yaml and re-run.\n"
        )
        sys.exit(4)

    print(f"  pdf  -> {cache_pdf.relative_to(REPO_ROOT)} ({cache_pdf.stat().st_size:,} bytes)")
    print(f"  text -> {text_path.relative_to(REPO_ROOT)} ({text_path.stat().st_size:,} bytes)")

    modified = False
    if not pinned_pdf_sha:
        entry["pdf_sha256"] = actual_pdf_sha
        modified = True
    if not pinned_text_sha:
        entry["text_sha256"] = actual_text_sha
        modified = True
    return modified


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        help="case-insensitive substring match on mpn (e.g. RP2040)",
        default=None,
    )
    parser.add_argument(
        "--refresh-pdf",
        action="store_true",
        help="delete cached PDFs and refetch from manufacturer URLs",
    )
    args = parser.parse_args()

    if not MANIFEST_PATH.exists():
        sys.stderr.write(f"manifest not found: {MANIFEST_PATH}\n")
        sys.exit(1)

    with MANIFEST_PATH.open("r", encoding="utf-8") as f:
        manifest = yaml.safe_load(f)

    any_modified = False
    for entry in manifest["datasheets"]:
        if process_entry(entry, only=args.only, refresh=args.refresh_pdf):
            any_modified = True

    if any_modified:
        with MANIFEST_PATH.open("w", encoding="utf-8") as f:
            yaml.safe_dump(manifest, f, sort_keys=False, allow_unicode=True)
        print(f"\nManifest updated with newly-pinned sha256 values: {MANIFEST_PATH}")
    else:
        print("\nManifest unchanged (all sha256 values already pinned and matched).")


if __name__ == "__main__":
    main()
