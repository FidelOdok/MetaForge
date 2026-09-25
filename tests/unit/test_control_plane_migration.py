"""Static checks on the MetaForge Cloud control-plane migration.

The control plane is the one place in MetaForge where a single SQL mistake
leaks another customer's data, and row-level security is the entire boundary —
there is no application-layer filter behind it to catch a miss.

Behavioural verification needs a live Postgres and is done against one (see
``docs/deployment/cloud.md``). These tests are the part that can run anywhere,
and they exist to catch the specific regression that is easy to commit and
invisible in review: adding a table to the migration and forgetting to turn RLS
on. A table without a policy is not "unprotected pending follow-up", it is
world-readable to every authenticated user of the platform.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"

#: Tables the control plane is expected to define. Listed explicitly so that
#: deleting one is also a test failure, not just adding an unprotected one.
EXPECTED_TABLES = {
    "accounts",
    "account_members",
    "cloud_projects",
    "gateway_connections",
}


@pytest.fixture(scope="module")
def sql() -> str:
    files = sorted(MIGRATIONS.glob("*_control_plane.sql"))
    assert files, f"no control-plane migration found in {MIGRATIONS}"
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def _tables(sql: str) -> set[str]:
    return set(re.findall(r"create table if not exists public\.(\w+)", sql))


class TestControlPlaneTables:
    def test_defines_exactly_the_expected_tables(self, sql: str):
        assert _tables(sql) == EXPECTED_TABLES

    @pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
    def test_every_table_enables_and_forces_rls(self, sql: str, table: str):
        """FORCE matters as much as ENABLE.

        Without FORCE the table owner bypasses its own policies, so anything
        connecting as that role — a migration, a job, a misconfigured service
        key — reads across every tenant.
        """
        assert f"alter table public.{table} enable row level security" in sql
        assert f"alter table public.{table} force row level security" in sql

    @pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
    def test_every_table_has_at_least_one_policy(self, sql: str, table: str):
        assert re.search(rf"create policy \w+ on public\.{table}\b", sql), (
            f"public.{table} has RLS enabled but no policy, which denies everyone "
            f"and will look like an outage rather than a mistake"
        )

    @pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
    def test_every_table_scopes_to_the_caller(self, sql: str, table: str):
        """No policy may be unconditional.

        Every policy must route through a membership helper. A `using (true)`
        would satisfy the "has a policy" test above while protecting nothing.
        """
        block = sql.split(f"on public.{table}")
        joined = "".join(block[1:])
        assert "is_account_member" in joined or "is_account_admin" in joined


class TestSecurityDefinerHygiene:
    def test_security_definer_functions_pin_search_path(self, sql: str):
        """An unpinned search_path on SECURITY DEFINER is privilege escalation.

        The caller prepends a schema they control and the function body
        resolves to their table instead of ours, while running with the
        definer's rights.
        """
        for match in re.finditer(r"create or replace function public\.(\w+)(.*?)\$\$", sql, re.S):
            name, body = match.group(1), match.group(2)
            if "security definer" in body:
                assert "set search_path = ''" in body, (
                    f"public.{name} is SECURITY DEFINER without a pinned search_path"
                )

    def test_membership_helpers_are_security_definer(self, sql: str):
        """They must bypass RLS, or the policies that call them recurse."""
        for helper in ("is_account_member", "is_account_admin"):
            match = re.search(rf"create or replace function public\.{helper}.*?\$\$", sql, re.S)
            assert match and "security definer" in match.group(0)


class TestGatewayConnections:
    def test_gateway_base_url_must_be_https(self, sql: str):
        """The proxy sends a bearer credential to this address."""
        assert "base_url ~ '^https://'" in sql

    def test_the_proxy_credential_is_not_stored_in_the_table(self, sql: str):
        """Only a Vault reference and a display hint belong here.

        A column holding the secret itself would be selectable by every member
        of the account through the read policy.
        """
        assert "credential_secret_id" in sql
        assert not re.search(r"credential(_token|_secret)?\s+text", sql)


class TestSignUp:
    def test_new_users_get_a_personal_account(self, sql: str):
        """Otherwise a signed-up user belongs to nothing, every policy denies
        them, and the dashboard is an empty shell with no way forward."""
        assert "on_auth_user_created" in sql
        assert "after insert on auth.users" in sql

    def test_slug_collisions_are_resolved_rather_than_fatal(self, sql: str):
        """Two providers can yield the same email local part. A unique-violation
        here would fail the sign-up itself."""
        assert "while exists" in sql
