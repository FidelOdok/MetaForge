import { test } from "node:test";
import assert from "node:assert/strict";
import { composeGoal, isComplete, missingFields, INTENT_FIELDS } from "./intent.js";

test("missingFields flags every empty required field (the completeness gate)", () => {
  assert.deepEqual(
    missingFields({}),
    INTENT_FIELDS.map((f) => f.key),
  );
  assert.equal(isComplete({}), false);
});

test("whitespace-only values do not satisfy a required field", () => {
  const vals = Object.fromEntries(INTENT_FIELDS.map((f) => [f.key, "   "]));
  assert.equal(isComplete(vals), false);
});

test("all required fields filled -> complete", () => {
  const vals = Object.fromEntries(INTENT_FIELDS.map((f) => [f.key, "x"]));
  assert.deepEqual(missingFields(vals), []);
  assert.equal(isComplete(vals), true);
});

test("composeGoal renders every field as Label: value", () => {
  const g = composeGoal({
    product: "quadruped leg",
    user: "researchers",
    mission: "walk",
    constraints: "5kg",
    acceptance: "SF>=2",
  });
  assert.match(g, /Product: quadruped leg/);
  assert.match(g, /Acceptance: SF>=2/);
});
