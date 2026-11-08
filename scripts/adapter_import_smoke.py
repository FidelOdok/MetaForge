"""Import every module an adapter image ships, inside that image.

Run by ``.github/workflows/adapter-images.yml`` against each image it builds,
before the image is signed or tagged. The adapter Dockerfiles copy only the
shared modules an adapter imports (``mcp_core/*.py``, the Python parts of
``observability/``, the top of ``tool_registry/``, ``tool_registry/mcp_server/``
and the adapter's own folder), so a new import of a module the image does not
carry would only show up when the container starts on a host. This makes it
show up in CI instead.

Piped into the image's own interpreter, so it needs nothing but the stdlib::

    docker run --rm -i --entrypoint python3 IMAGE - freecad < scripts/adapter_import_smoke.py

It imports ``tool_registry.tools.<adapter>`` and every module under it (tests
excluded), then ``entrypoint`` when the image has ``/app/entrypoint.py``. A
module that cannot find a first-party package (``mcp_core``, ``observability``,
``tool_registry``) fails the run: that is the image missing code. Any failure
to import ``entrypoint`` or the adapter package itself fails too. Other import
errors in leaf modules (an optional library a helper loads) are printed and
left to the adapter's own tests.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import sys
import traceback

FIRST_PARTY = ("mcp_core", "observability", "tool_registry")


def _missing_first_party(exc: BaseException) -> str | None:
    name = getattr(exc, "name", None) or ""
    if name.split(".")[0] in FIRST_PARTY:
        return name
    return None


def main(adapter: str) -> int:
    package_name = f"tool_registry.tools.{adapter}"
    fatal: list[str] = []
    warnings: list[str] = []

    try:
        package = importlib.import_module(package_name)
    except Exception:  # noqa: BLE001 - report everything, this is a smoke test
        print(f"FAIL {package_name}\n{traceback.format_exc()}")
        return 1

    imported = 1
    for info in pkgutil.walk_packages(package.__path__, f"{package_name}."):
        leaf = info.name.rsplit(".", 1)[-1]
        if leaf == "tests" or leaf.startswith("test_"):
            continue
        try:
            importlib.import_module(info.name)
            imported += 1
        except ImportError as exc:
            missing = _missing_first_party(exc)
            if missing:
                fatal.append(f"{info.name}: the image has no {missing}")
            else:
                warnings.append(f"{info.name}: {exc!r}")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{info.name}: {exc!r}")

    if os.path.exists("/app/entrypoint.py"):
        sys.path.insert(0, "/app")
        try:
            importlib.import_module("entrypoint")
            imported += 1
        except Exception:  # noqa: BLE001
            fatal.append(f"entrypoint: {traceback.format_exc()}")

    for line in warnings:
        print(f"warn {line}")
    for line in fatal:
        print(f"FAIL {line}")
    print(f"{adapter}: imported {imported} modules, {len(fatal)} failed, {len(warnings)} warned")
    return 1 if fatal else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: adapter_import_smoke.py <adapter>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
