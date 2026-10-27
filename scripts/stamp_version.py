"""Write the resolved release version into ``pyproject.toml`` (FORGE-648).

The release workflow derives the version from Conventional-Commit subjects and
tags the repository with it, but nothing ever put that number into the package
being built. ``[project].version`` stayed at whatever literal was committed, so
the gateway image installed a distribution whose metadata disagreed with the
release it was cut from: the tag said ``v0.3.617`` and ``/health``, which reads
``importlib.metadata.version("metaforge-hw")``, said ``0.3.0``.

Deriving the version from git tags instead (hatch-vcs and friends) cannot work
here: ``.dockerignore`` excludes ``.git``, so the image build has no repository
to read. Stamping the value in before the build is what fits the pipeline that
exists.

Builds that are not releases get a local version segment, because they are not
that release -- a ``:latest`` image built three commits after ``v0.3.617`` says
``0.3.617+g1a2b3c4``, which sorts after the release and cannot be mistaken for
it. PEP 440 is explicit that local segments are for exactly this.

Only ``[project].version`` is touched. Other tables carry their own ``version``
keys and must not be rewritten, so the table is tracked rather than the file
scanned for the first match.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

#: ``X.Y.Z``, optional pre/post/dev suffix, optional ``+local``. Narrower than
#: the whole of PEP 440 on purpose: this is the shape the release script emits,
#: and anything else is a mistake worth failing on rather than stamping.
VERSION_RE = re.compile(
    r"^\d+(\.\d+)*((a|b|rc)\d+|\.post\d+|\.dev\d+)*(\+[a-zA-Z0-9]+([.-][a-zA-Z0-9]+)*)?$"
)

TABLE_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
VERSION_KEY_RE = re.compile(r'^(?P<prefix>\s*version\s*=\s*)"(?P<value>[^"]*)"(?P<suffix>.*)$')


def normalise(version: str, *, sha: str | None = None) -> str:
    """Turn a tag into a PEP 440 version, optionally marking it as a snapshot.

    The release workflow speaks in tags (``v0.3.617``); package metadata does
    not carry the ``v``. A ``sha`` means this build is after the named release
    rather than being it.
    """
    value = version.strip()
    if value.startswith("v"):
        value = value[1:]
    if sha:
        # Local segments may not contain underscores, and a short sha is
        # enough to identify the commit.
        clean = re.sub(r"[^a-zA-Z0-9]", "", sha)[:7]
        if not clean:
            raise ValueError(f"unusable sha: {sha!r}")
        value = f"{value}+g{clean}"
    if not VERSION_RE.match(value):
        raise ValueError(f"not a PEP 440 version: {value!r}")
    return value


def stamp(text: str, version: str) -> tuple[str, str]:
    """Return the file with ``[project].version`` set, and the old value."""
    lines = text.splitlines(keepends=True)
    table: str | None = None
    for index, line in enumerate(lines):
        header = TABLE_RE.match(line)
        if header:
            table = header.group(1).strip()
            continue
        if table != "project":
            continue
        key = VERSION_KEY_RE.match(line)
        if key:
            lines[index] = f'{key.group("prefix")}"{version}"{key.group("suffix")}\n'
            return "".join(lines), key.group("value")
    raise LookupError("no version key under [project]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="the resolved version, with or without a leading v")
    parser.add_argument(
        "--sha",
        default=None,
        help="mark this as a build after that release, not the release itself",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("pyproject.toml"),
        help="the file to stamp (default: pyproject.toml)",
    )
    args = parser.parse_args(argv)

    try:
        version = normalise(args.version, sha=args.sha)
    except ValueError as exc:
        print(f"stamp_version: {exc}", file=sys.stderr)
        return 2

    text = args.path.read_text(encoding="utf-8")
    try:
        stamped, previous = stamp(text, version)
    except LookupError as exc:
        print(f"stamp_version: {args.path}: {exc}", file=sys.stderr)
        return 2

    args.path.write_text(stamped, encoding="utf-8")
    print(f"{args.path}: {previous} -> {version}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
