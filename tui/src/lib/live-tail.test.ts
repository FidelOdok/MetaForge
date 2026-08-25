import { test } from "node:test";
import assert from "node:assert/strict";
import { tailLines, stepRows } from "./live-tail.js";

test("short text passes through untouched", () => {
  assert.equal(tailLines("hello\nworld", 80, 10), "hello\nworld");
  assert.equal(tailLines("", 80, 10), "");
});

test("keeps only the trailing lines within the row budget", () => {
  const text = ["l1", "l2", "l3", "l4", "l5"].join("\n");
  assert.equal(tailLines(text, 80, 3), "…l3\nl4\nl5");
});

test("clipped output is marked with a leading ellipsis", () => {
  const text = Array.from({ length: 20 }, (_, i) => `line ${i}`).join("\n");
  const out = tailLines(text, 80, 5);
  assert.ok(out.startsWith("…"));
});

test("counts wrapped rows, not logical lines", () => {
  // One 200-char logical line = 3 rows at width 80.
  const long = "x".repeat(200);
  const out = tailLines(`${long}\ntail`, 80, 2);
  // 'tail' costs 1 row; only 1 row of the long line fits.
  const rows = out.split("\n");
  assert.equal(rows.length, 2);
  assert.ok(rows[0].startsWith("…"));
  assert.ok(rows[0].length <= 80);
  assert.equal(rows[1], "tail");
});

test("a single huge line yields its trailing chars within budget", () => {
  const out = tailLines("y".repeat(1000), 50, 3);
  const rows = out.split("\n");
  assert.equal(rows.length, 1);
  assert.ok(out.startsWith("…"));
  assert.ok(out.length <= 3 * 50);
});

test("never returns more rows than the budget", () => {
  const text = Array.from({ length: 50 }, (_, i) => `row ${i} `.repeat(10)).join("\n");
  for (const budget of [1, 2, 5, 12]) {
    const out = tailLines(text, 60, budget);
    const rendered = out
      .split("\n")
      .reduce((n, l) => n + Math.max(1, Math.ceil(l.length / 60)), 0);
    assert.ok(rendered <= budget, `budget ${budget} → ${rendered} rows`);
  }
});

test("stepRows scales with terminal width", () => {
  assert.equal(stepRows(120), 1);
  assert.equal(stepRows(60), 2);
  assert.ok(stepRows(30) >= 4);
});
