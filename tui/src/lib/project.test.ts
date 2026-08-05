import { test } from "node:test";
import assert from "node:assert/strict";
import type { Project } from "../api/client.js";
import {
  assistantScope,
  isDetachQuery,
  resolveProject,
  scopeKey,
  scopeLabel,
} from "./project.js";

const p = (id: string, name: string): Project => ({ id, name, status: "active" });

const PROJECTS: Project[] = [
  p("cfcd6ae8-5b16-4ba1-84d7-b9384ea1cbf3", "Monitor Build Demo"),
  p("250aec91-6d31-4a26-bb71-5e0d1e6fedb9", "Pan-Tilt Gimbal"),
  p("aaa11111-0000-0000-0000-000000000000", "eval-chat_brief_project-native-1"),
];

test("resolves an exact project id", () => {
  const r = resolveProject(PROJECTS, "250aec91-6d31-4a26-bb71-5e0d1e6fedb9");
  assert.ok(r.ok);
  assert.deepEqual(r.scope, {
    kind: "project",
    id: "250aec91-6d31-4a26-bb71-5e0d1e6fedb9",
    name: "Pan-Tilt Gimbal",
  });
});

test("resolves an exact name case-insensitively", () => {
  const r = resolveProject(PROJECTS, "monitor build demo");
  assert.ok(r.ok);
  assert.equal(r.scope.name, "Monitor Build Demo");
});

test("resolves a unique substring of a name", () => {
  const r = resolveProject(PROJECTS, "gimbal");
  assert.ok(r.ok);
  assert.equal(r.scope.id, "250aec91-6d31-4a26-bb71-5e0d1e6fedb9");
});

test("an ambiguous query is an error, not a coin flip", () => {
  const projects = [...PROJECTS, p("bbb22222-0000-0000-0000-000000000000", "Monitor Build v2")];
  const r = resolveProject(projects, "monitor");
  assert.equal(r.ok, false);
  if (!r.ok) {
    assert.match(r.error, /matches 2 projects/);
    assert.match(r.error, /Monitor Build Demo/);
    assert.match(r.error, /Monitor Build v2/);
  }
});

test("an exact name wins over other substring matches", () => {
  const projects = [...PROJECTS, p("bbb22222-0000-0000-0000-000000000000", "Monitor Build Demo v2")];
  const r = resolveProject(projects, "Monitor Build Demo");
  assert.ok(r.ok);
  assert.equal(r.scope.id, "cfcd6ae8-5b16-4ba1-84d7-b9384ea1cbf3");
});

test("an unknown name reports no match", () => {
  const r = resolveProject(PROJECTS, "nope");
  assert.equal(r.ok, false);
  if (!r.ok) assert.match(r.error, /no project matches "nope"/);
});

test("an empty query and an empty gateway both explain themselves", () => {
  const empty = resolveProject(PROJECTS, "  ");
  assert.equal(empty.ok, false);
  if (!empty.ok) assert.match(empty.error, /usage/);
  const none = resolveProject([], "anything");
  assert.equal(none.ok, false);
  if (!none.ok) assert.match(none.error, /no projects/);
});

test("scopeLabel never invents a project", () => {
  assert.equal(scopeLabel(null), "no thread");
  assert.equal(scopeLabel(assistantScope()), "no project");
  assert.equal(scopeLabel({ kind: "project", id: "p1", name: "Quadruped" }), "Quadruped");
});

test("scopeKey ignores an assistant scope's random entity id", () => {
  // Two distinct assistant scopes must compare equal, else every render would
  // look like a scope change and recreate the chat thread.
  assert.equal(scopeKey(assistantScope()), scopeKey(assistantScope()));
  assert.notEqual(assistantScope().entityId, assistantScope().entityId);
  assert.equal(scopeKey({ kind: "project", id: "p1", name: "x" }), "project:p1");
  assert.equal(scopeKey(null), null);
});

test("detach words leave the project", () => {
  for (const q of ["none", "None", " off ", "clear", "-"]) assert.ok(isDetachQuery(q));
  for (const q of ["gimbal", "nonetheless"]) assert.equal(isDetachQuery(q), false);
});
