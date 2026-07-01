"""Central tool registry — native + MCP (MET-547, Phase 2).

The harness agents call tools through one registry rather than reaching into
MCP clients directly. Native tools (in-process functions) and MCP tools (from
an external server, discovered over the protocol) are registered uniformly as
:class:`ToolSpec` entries and invoked by name.

MCP tools are namespaced ``mcp_<server>_<tool>`` so they can never collide with
native tools or with tools from a different server -- the naming scheme called
for in MET-547.

Layering: this registry holds an opaque async ``handler`` per tool and never
imports ``mcp_core`` (which ``orchestrator`` may not depend on). Whoever bridges
an MCP server registers its tools by passing a handler that performs the actual
protocol call, so the registry stays a pure orchestration-layer component.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# A tool invocation: arguments in, result out.
Handler = Callable[[dict[str, Any]], Awaitable[Any]]
# Gate precondition check: gate name -> is it currently satisfied?
GateCheck = Callable[[str], bool]

NATIVE = "native"
_SANITIZE = re.compile(r"[^0-9a-zA-Z]+")


def _slug(value: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to single underscores."""
    return _SANITIZE.sub("_", value).strip("_").lower()


class ToolNotFoundError(KeyError):
    """No registered tool with the given name."""


class DuplicateToolError(ValueError):
    """A tool with the given name is already registered."""


class GateBlockedError(PermissionError):
    """A tool was invoked while a required gate precondition was unmet.

    Enforced server-side so a consequential tool can never run for an external
    client that skipped the gate.
    """

    def __init__(self, tool: str, gate: str) -> None:
        self.tool = tool
        self.gate = gate
        super().__init__(f"tool '{tool}' blocked: gate '{gate}' not satisfied")


@dataclass(frozen=True)
class ToolSpec:
    """One registered tool the harness can call."""

    name: str
    description: str
    input_schema: dict[str, Any]
    origin: str  # NATIVE or the MCP server name
    handler: Handler
    # Gate names that must be satisfied before this tool may be invoked.
    required_gates: tuple[str, ...] = ()


class ToolRegistry:
    """Register and invoke native + MCP tools by a unified name."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    @staticmethod
    def mcp_name(server: str, tool: str) -> str:
        """The namespaced registry name for an MCP server's tool."""
        return f"mcp_{_slug(server)}_{_slug(tool)}"

    def _add(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise DuplicateToolError(f"tool '{spec.name}' is already registered")
        self._tools[spec.name] = spec
        logger.info("tool_registered", tool=spec.name, origin=spec.origin)
        return spec

    def register_native(
        self,
        name: str,
        *,
        description: str,
        input_schema: dict[str, Any],
        handler: Handler,
        required_gates: Sequence[str] = (),
    ) -> ToolSpec:
        return self._add(
            ToolSpec(
                name=name,
                description=description,
                input_schema=input_schema,
                origin=NATIVE,
                handler=handler,
                required_gates=tuple(required_gates),
            )
        )

    def register_mcp(
        self,
        server: str,
        tool: str,
        *,
        description: str,
        input_schema: dict[str, Any],
        handler: Handler,
        required_gates: Sequence[str] = (),
    ) -> ToolSpec:
        return self._add(
            ToolSpec(
                name=self.mcp_name(server, tool),
                description=description,
                input_schema=input_schema,
                origin=server,
                handler=handler,
                required_gates=tuple(required_gates),
            )
        )

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all_tools(self, *, origin: str | None = None) -> list[ToolSpec]:
        specs = list(self._tools.values())
        if origin is not None:
            specs = [s for s in specs if s.origin == origin]
        return sorted(specs, key=lambda s: s.name)

    def visible(self, gate_check: GateCheck) -> list[ToolSpec]:
        """Tools whose every required gate is currently satisfied.

        Used to filter the tool list exposed to a client so gated tools don't
        even appear until their preconditions hold.
        """
        return [s for s in self.all_tools() if all(gate_check(g) for g in s.required_gates)]

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        gate_check: GateCheck | None = None,
    ) -> Any:
        spec = self.get(name)
        if spec.required_gates:
            # Fail safe: a gated tool never runs without an evaluator.
            if gate_check is None:
                raise GateBlockedError(name, spec.required_gates[0])
            for gate in spec.required_gates:
                if not gate_check(gate):
                    logger.warning("tool_gate_blocked", tool=name, gate=gate)
                    raise GateBlockedError(name, gate)
        logger.info("tool_invoke", tool=name, origin=spec.origin)
        return await spec.handler(arguments)
