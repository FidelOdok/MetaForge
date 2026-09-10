import { test } from "node:test";
import assert from "node:assert/strict";
import { decideInvocation } from "./invocation.js";

test("bare forge launches the workspace", () => {
  assert.equal(decideInvocation([]).mode, "tui");
  assert.equal(decideInvocation(["ui"]).mode, "tui");
});

test("--project launches the workspace scoped to that project", () => {
  const a = decideInvocation(["--project", "Monitor Build Demo"]);
  assert.equal(a.mode, "tui");
  assert.equal(a.initialProject, "Monitor Build Demo");
  // The flag's value must not be read as a subcommand.
  assert.equal(decideInvocation(["--project=gimbal"]).initialProject, "gimbal");
  assert.equal(decideInvocation(["ui", "-p", "gimbal"]).initialProject, "gimbal");
});

test("--debug is a global flag, not a subcommand", () => {
  const d = decideInvocation(["--debug"]);
  assert.equal(d.mode, "tui");
  assert.equal(d.debug, true);
  const both = decideInvocation(["--debug", "--project", "gimbal"]);
  assert.equal(both.mode, "tui");
  assert.equal(both.initialProject, "gimbal");
});

test("subcommands and unknown flags go to the command layer", () => {
  assert.equal(decideInvocation(["runs", "list"]).mode, "command");
  assert.equal(decideInvocation(["chat", "-m", "hi", "--project", "x"]).mode, "command");
  // A flag the workspace can't act on must print/execute, not silently launch a
  // UI that ignores it.
  assert.equal(decideInvocation(["--help"]).mode, "command");
  assert.equal(decideInvocation(["-h"]).mode, "command");
  assert.equal(decideInvocation(["--gateway", "http://gw:8000"]).mode, "command");
});

test("--version/-v short-circuits to the version printer", () => {
  assert.equal(decideInvocation(["--version"]).mode, "version");
  assert.equal(decideInvocation(["-v"]).mode, "version");
});

test("a bare --project with no value doesn't become a project name", () => {
  const r = decideInvocation(["--project"]);
  assert.equal(r.mode, "tui");
  assert.equal(r.initialProject, undefined);
});
