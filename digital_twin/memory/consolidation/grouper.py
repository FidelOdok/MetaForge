"""Stage 2 of the consolidation pipeline — group experiences by theme.

Takes a flat batch of ``ExperienceMemory`` rows (output of the fetcher)
and emits ``ExperienceGroup`` clusters keyed on ``ConsolidationTheme``.
The synthesizer consumes one group at a time so each LLM call has a
focused scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from digital_twin.memory.consolidation.themes import (
    ConsolidationTheme,
    classify_theme,
)
from digital_twin.memory.models import ExperienceMemory


@dataclass(frozen=True)
class ExperienceGroup:
    """A cluster of experiences sharing a consolidation theme."""

    theme: ConsolidationTheme
    experiences: tuple[ExperienceMemory, ...] = field(default_factory=tuple)

    @property
    def size(self) -> int:
        return len(self.experiences)

    @property
    def failure_count(self) -> int:
        return sum(1 for exp in self.experiences if not exp.success)

    @property
    def success_count(self) -> int:
        return sum(1 for exp in self.experiences if exp.success)


class EventGrouper:
    """Cluster experiences into ``ExperienceGroup``s for synthesis.

    The grouper is stateless — every call partitions its input afresh.
    A minimum group size keeps low-signal singletons out of the LLM
    pipeline (one stress-validation event isn't enough to "synthesize a
    pattern"); experiences below the threshold roll into
    ``ConsolidationTheme.MISC`` so the synthesizer can still see them
    if the operator wants a catch-all pass.
    """

    DEFAULT_MIN_GROUP_SIZE = 2

    def __init__(self, *, min_group_size: int = DEFAULT_MIN_GROUP_SIZE) -> None:
        if min_group_size < 1:
            raise ValueError(f"min_group_size must be >= 1, got {min_group_size}")
        self._min_group_size = min_group_size

    def group(self, experiences: list[ExperienceMemory]) -> list[ExperienceGroup]:
        """Partition ``experiences`` by theme; emit one group per theme.

        Groups smaller than ``min_group_size`` get folded into a single
        ``MISC`` group so the synthesizer can still consider them
        downstream. Empty input → empty output (never a stray empty
        group).
        """
        if not experiences:
            return []

        buckets: dict[ConsolidationTheme, list[ExperienceMemory]] = {}
        for exp in experiences:
            theme = classify_theme(exp)
            buckets.setdefault(theme, []).append(exp)

        groups: list[ExperienceGroup] = []
        rolled_into_misc: list[ExperienceMemory] = []
        for theme, items in buckets.items():
            if len(items) < self._min_group_size and theme != ConsolidationTheme.MISC:
                rolled_into_misc.extend(items)
                continue
            groups.append(ExperienceGroup(theme=theme, experiences=tuple(items)))

        if rolled_into_misc:
            existing_misc = next(
                (g for g in groups if g.theme == ConsolidationTheme.MISC),
                None,
            )
            if existing_misc is None:
                groups.append(
                    ExperienceGroup(
                        theme=ConsolidationTheme.MISC,
                        experiences=tuple(rolled_into_misc),
                    )
                )
            else:
                groups.remove(existing_misc)
                merged = (*existing_misc.experiences, *rolled_into_misc)
                groups.append(ExperienceGroup(theme=ConsolidationTheme.MISC, experiences=merged))

        # Stable ordering: bigger groups first so the synthesizer
        # invests its first LLM budget on the highest-signal theme.
        groups.sort(key=lambda g: (-g.size, g.theme.value))
        return groups
