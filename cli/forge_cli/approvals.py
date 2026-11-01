"""`forge approvals` command handlers (FORGE-509, epic FORGE-506).

Drives the unified approvals API (`/v1/approvals`): list, show, and decide
(approve / reject / retry / rework) every kind of pending human decision from
one place.  Non-interactive and scriptable: ``--json`` prints the raw gateway
payload and failures map to distinct exit codes (see ``EXIT_*``).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from cli.forge_cli.client import ForgeClient, ForgeClientError
from cli.forge_cli.formatters import format_output

EXIT_OK = 0
EXIT_ERROR = 1  # transport failure, 5xx, anything unmapped
EXIT_USAGE = 2  # bad arguments (argparse also exits 2)
EXIT_NOT_FOUND = 3  # 404: unknown approval id
EXIT_CONFLICT = 4  # 409: not decidable right now
EXIT_INVALID = 5  # 422, or the same checks failing locally before posting
EXIT_AUTH = 6  # 401 / 403

_LIST_COLUMNS = ["id", "kind", "status", "title", "project_id", "created_at"]


def _json_mode(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _fail(code: int, message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(code)


def _exit_for(exc: ForgeClientError, approval_id: str | None = None) -> None:
    status = exc.status_code
    if status == 404:
        _fail(EXIT_NOT_FOUND, f"no approval with id {approval_id!r}" if approval_id else str(exc))
    if status == 409:
        _fail(EXIT_CONFLICT, f"not decidable right now: {exc}")
    if status == 422:
        _fail(EXIT_INVALID, f"gateway rejected the decision: {exc}")
    if status in (401, 403):
        _fail(EXIT_AUTH, f"not authorized ({exc}); set METAFORGE_AUTH_TOKEN")
    _fail(EXIT_ERROR, str(exc))


def handle_approvals(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Dispatch `forge approvals <subcommand>`."""
    sub = getattr(args, "approvals_command", None)
    if sub == "list":
        return _list(args, client)
    if sub == "show":
        return _show(args, client)
    if sub in ("approve", "reject", "retry", "rework"):
        return decide(
            client,
            args.approval_id,
            sub,
            reason=getattr(args, "reason", None),
            to_phase=getattr(args, "to", None),
            as_json=_json_mode(args),
        )
    _fail(
        EXIT_USAGE, "specify an approvals subcommand (list, show, approve, reject, retry, rework)"
    )
    return None


def _list(args: argparse.Namespace, client: ForgeClient) -> Any:
    status = "all" if args.all else "decided" if args.decided else "pending"
    try:
        payload = client.list_approvals(status=status, project_id=args.project, kind=args.kind)
    except ForgeClientError as exc:
        _exit_for(exc)
        return None
    if _json_mode(args):
        print(format_output(payload, fmt="json"))
        return None
    items = payload.get("items", [])
    if not items:
        print("No approvals.")
    else:
        print(format_output(items, fmt="table", columns=_LIST_COLUMNS))
    unscoped = payload.get("unscoped_count") or 0
    if unscoped:
        print(f"({unscoped} approval(s) have no project and are not matched by --project)")
    return None


def _show(args: argparse.Namespace, client: ForgeClient) -> Any:
    try:
        item = client.get_approval(args.approval_id)
    except ForgeClientError as exc:
        _exit_for(exc, args.approval_id)
        return None
    if _json_mode(args):
        print(format_output(item, fmt="json"))
    else:
        print(format_item(item))
    return None


def format_item(item: dict[str, Any]) -> str:
    """Human-readable card with every ApprovalItem field."""
    lines = [
        f"{item.get('title', '')}",
        f"  id:           {item.get('id')}",
        f"  kind:         {item.get('kind')}",
        f"  status:       {item.get('status')}",
        f"  project:      {item.get('project_id') or '-'}",
        f"  created:      {item.get('created_at')}",
        f"  deadline:     {item.get('deadline') or '-'}",
        f"  route:        {item.get('route') or '-'}",
        f"  requested by: {item.get('requested_by') or '-'}",
    ]
    if item.get("summary"):
        lines.append(f"  summary:      {item['summary']}")
    if item.get("reason_held"):
        lines.append(f"  held because: {item['reason_held']}")
    findings = item.get("findings") or []
    lines.append(f"  findings:     {len(findings) if findings else 'none'}")
    for f in findings:
        lines.append(f"    [{f.get('severity')}] {f.get('kind')}: {f.get('message')}")
    allowed = item.get("allowed_decisions") or []
    lines.append(f"  allowed:      {', '.join(allowed) or 'none'}")
    lines.append(f"  reason needed for: {', '.join(item.get('reason_required_for') or []) or '-'}")
    lines.append(f"  rework targets:    {', '.join(item.get('rework_targets') or []) or '-'}")
    decidable = (
        "yes" if item.get("decidable") else f"no ({item.get('not_decidable_reason') or '?'})"
    )
    lines.append(f"  decidable:    {decidable}")
    detail = item.get("detail") or {}
    if detail:
        lines.append("  detail:")
        for k, v in detail.items():
            lines.append(f"    {k}: {v}")
    decision = item.get("decision")
    if decision:
        lines.append("  decision:")
        for k, v in decision.items():
            lines.append(f"    {k}: {v}")
    return "\n".join(lines)


def validate_decision(
    item: dict[str, Any], decision: str, reason: str | None, to_phase: str | None
) -> str | None:
    """Local pre-flight against the item's own policy; returns an error or None."""
    allowed = item.get("allowed_decisions") or []
    if decision not in allowed:
        return (
            f"{decision!r} is not allowed for {item.get('id')} "
            f"(allowed: {', '.join(allowed) or 'none'})"
        )
    if decision in (item.get("reason_required_for") or []) and not (reason or "").strip():
        return f"{decision} requires --reason"
    if decision == "rework":
        targets = item.get("rework_targets") or []
        if not to_phase:
            return f"rework requires --to <phase> (one of: {', '.join(targets) or 'none'})"
        if to_phase not in targets:
            return (
                f"--to {to_phase!r} is not a rework target (one of: {', '.join(targets) or 'none'})"
            )
    return None


def decide(
    client: ForgeClient,
    approval_id: str,
    decision: str,
    *,
    reason: str | None = None,
    to_phase: str | None = None,
    as_json: bool = False,
) -> None:
    """Fetch the item, validate locally, post the decision, print the result."""
    try:
        item = client.get_approval(approval_id)
    except ForgeClientError as exc:
        _exit_for(exc, approval_id)
        return
    if item.get("decidable") is False:
        _fail(
            EXIT_CONFLICT,
            f"not decidable right now: {item.get('not_decidable_reason') or 'unknown'}",
        )
    problem = validate_decision(item, decision, reason, to_phase)
    if problem:
        _fail(EXIT_INVALID, problem)
    try:
        updated = client.decide_approval(
            approval_id,
            decision,
            reason=reason,
            to_phase=to_phase if decision == "rework" else None,
        )
    except ForgeClientError as exc:
        _exit_for(exc, approval_id)
        return
    if as_json:
        print(format_output(updated, fmt="json"))
    else:
        print(f"{updated.get('id', approval_id)} -> {updated.get('status')}")


def register_subparser(subparsers: Any) -> None:
    """Register the ``approvals`` command group."""
    p = subparsers.add_parser("approvals", help="Review and decide pending approvals")
    sub = p.add_subparsers(dest="approvals_command", help="Approvals subcommands")

    lst = sub.add_parser("list", help="List approvals (pending by default)")
    lst.add_argument("--project", default=None, help="Only this project id")
    lst.add_argument("--kind", default=None, help="Only this kind (gate, tool_call, ...)")
    which = lst.add_mutually_exclusive_group()
    which.add_argument("--all", action="store_true", help="Pending and decided")
    which.add_argument("--decided", action="store_true", help="Only decided")
    lst.add_argument("--json", action="store_true", help="JSON output")

    show = sub.add_parser("show", help="Show one approval with findings and allowed decisions")
    show.add_argument("approval_id", help="Approval id, e.g. gate:run_abc")
    show.add_argument("--json", action="store_true", help="Raw item JSON")

    for name, help_text in (
        ("approve", "Approve"),
        ("reject", "Reject (requires --reason)"),
        ("retry", "Retry the held phase (requires --reason)"),
        ("rework", "Send back to an earlier phase (requires --to and --reason)"),
    ):
        d = sub.add_parser(name, help=help_text)
        d.add_argument("approval_id", help="Approval id, e.g. gate:run_abc")
        d.add_argument("--reason", default=None, help="Reason (audit trail)")
        d.add_argument("--json", action="store_true", help="Print the updated item as JSON")
        if name == "rework":
            d.add_argument("--to", required=True, help="Phase id to rework")
