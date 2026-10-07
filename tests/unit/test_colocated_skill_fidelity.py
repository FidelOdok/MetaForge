"""Run the FORGE-541 co-located skill tests under CI's ``tests/`` path.

``pytest`` in CI collects ``tests/`` only, so a skill's own ``tests.py``
never runs there; and the co-located files share the module name
``tests``, so they cannot all be collected in one session either. These
re-exports put the skill-fidelity cases (FORGE-544, FORGE-552, FORGE-554) where CI
runs them.
"""

from __future__ import annotations

from domain_agents.electronics.skills.check_power_budget.tests import (  # noqa: F401
    TestCheckPowerBudgetSkill,
    power_context,
)
from domain_agents.mechanical.skills.validate_stress.tests import (  # noqa: F401
    TestLimitVersusAllowableForge554,
)
from domain_agents.shared.skills.ingest_knowledge.tests import (  # noqa: F401
    TestIngestKnowledgeSkill,
    context,
)
from domain_agents.shared.skills.retrieve_knowledge.tests import (  # noqa: F401
    TestRetrieveKnowledgeSkill,
)
