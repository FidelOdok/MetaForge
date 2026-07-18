"""Scheduled background chat runs — "routines" (MET-563).

A daemonless, self-contained way to run assistant prompts on a schedule, in
keeping with the thin-client posture. Routines live in ``.forge/routines.json``;
``forge routine run-due`` fires every routine whose interval has elapsed (create
an assistant thread + send the prompt) and records ``last_run``. Wire ``run-due``
to OS cron or a loop for actual scheduling.

Interval-based (``30s`` / ``10m`` / ``2h`` / ``1d``) rather than full cron, so it
stays dependency-free and easy to test.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cli.forge_cli.client import ForgeClient, ForgeClientError

_DEFAULT_PATH = ".forge/routines.json"
_SCOPE_KIND = "assistant"
_INTERVAL_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class RoutineError(ValueError):
    """Invalid routine input (e.g. a malformed interval)."""


def parse_interval(text: str) -> int:
    """Parse ``30s`` / ``10m`` / ``2h`` / ``1d`` into seconds."""
    m = _INTERVAL_RE.match(text.strip().lower())
    if not m:
        raise RoutineError(f"invalid interval {text!r} (use e.g. 30s, 10m, 2h, 1d)")
    return int(m.group(1)) * _UNIT_SECONDS[m.group(2)]


@dataclass
class Routine:
    """One scheduled prompt."""

    id: str
    prompt: str
    interval_seconds: int
    last_run: float | None = None
    provider: str | None = None
    model: str | None = None
    mode: str = "ask"
    enabled: bool = True

    def is_due(self, now: float) -> bool:
        if not self.enabled:
            return False
        return self.last_run is None or (now - self.last_run) >= self.interval_seconds


@dataclass
class RoutineStore:
    """The routines file (`.forge/routines.json`)."""

    path: Path
    routines: list[Routine] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path = _DEFAULT_PATH) -> RoutineStore:
        p = Path(path)
        if not p.exists():
            return cls(path=p, routines=[])
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(path=p, routines=[])
        raw = data.get("routines", []) if isinstance(data, dict) else []
        routines = [Routine(**r) for r in raw if isinstance(r, dict)]
        return cls(path=p, routines=routines)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"routines": [asdict(r) for r in self.routines]}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def add(self, routine: Routine) -> None:
        self.routines.append(routine)

    def remove(self, routine_id: str) -> bool:
        before = len(self.routines)
        self.routines = [r for r in self.routines if r.id != routine_id]
        return len(self.routines) < before

    def due(self, now: float) -> list[Routine]:
        return [r for r in self.routines if r.is_due(now)]


def _execute(client: ForgeClient, routine: Routine, *, timeout: float = 120.0) -> None:
    """Fire one routine: create an assistant thread and send its prompt."""
    thread = client.create_thread(
        _SCOPE_KIND, f"routine-{routine.id}", title=f"routine {routine.id}"
    )
    client.send_message(
        str(thread["id"]),
        routine.prompt,
        provider=routine.provider,
        model=routine.model,
        timeout=timeout,
    )


def run_due(
    client: ForgeClient,
    store: RoutineStore,
    *,
    now: float | None = None,
    clock: Callable[[], float] = time.time,
) -> int:
    """Execute all due routines (best-effort), stamp last_run, save. Returns count."""
    current = now if now is not None else clock()
    fired = 0
    for routine in store.due(current):
        try:
            _execute(client, routine)
            routine.last_run = current
            fired += 1
            print(f"  ✓ routine {routine.id} fired")
        except ForgeClientError as exc:
            print(f"  ✗ routine {routine.id} failed: {exc}", file=sys.stderr)
    if fired:
        store.save()
    return fired


# ---------------------------------------------------------------------------
# CLI handler
# ---------------------------------------------------------------------------


def handle_routine(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Dispatch `forge routine <subcommand>`."""
    sub = args.routine_command
    path = getattr(args, "file", _DEFAULT_PATH)
    store = RoutineStore.load(path)

    if sub == "add":
        try:
            interval = parse_interval(args.every)
        except RoutineError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return None
        routine = Routine(
            id=uuid.uuid4().hex[:8],
            prompt=args.prompt,
            interval_seconds=interval,
            provider=args.provider,
            model=args.model,
            mode=args.mode,
        )
        store.add(routine)
        store.save()
        print(f"Added routine {routine.id} (every {args.every})")
        return None

    if sub == "list":
        if not store.routines:
            print("No routines.")
            return None
        for r in store.routines:
            state = "on" if r.enabled else "off"
            last = "never" if r.last_run is None else f"{r.last_run:.0f}"
            print(f"  {r.id}  every {r.interval_seconds}s  [{state}]  last={last}  {r.prompt!r}")
        return None

    if sub == "remove":
        if store.remove(args.routine_id):
            store.save()
            print(f"Removed routine {args.routine_id}")
        else:
            print(f"No routine with id {args.routine_id!r}", file=sys.stderr)
        return None

    if sub == "run-due":
        count = run_due(client, store)
        print(f"{count} routine(s) fired.")
        return None

    print("Error: unknown routine subcommand", file=sys.stderr)
    return None
