import { strict as assert } from "node:assert";
import { test } from "node:test";
import { pageJump, visibleWindow, windowLabel } from "./paging.js";

test("short lists are shown whole", () => {
  assert.deepEqual(visibleWindow(5, 3, 8), { start: 0, end: 5 });
});

test("window follows the selection down and up without jumping", () => {
  let w = visibleWindow(50, 0, 8);
  assert.deepEqual(w, { start: 0, end: 8 });
  // arrow down inside the window: stable
  w = visibleWindow(50, 5, 8, w.start);
  assert.equal(w.start, 0);
  // selection leaves the bottom: scrolls just enough
  w = visibleWindow(50, 8, 8, w.start);
  assert.deepEqual(w, { start: 1, end: 9 });
  // jump far: selection lands at the bottom edge
  w = visibleWindow(50, 30, 8, w.start);
  assert.deepEqual(w, { start: 23, end: 31 });
  // arrow back above the top: scrolls up
  w = visibleWindow(50, 22, 8, w.start);
  assert.deepEqual(w, { start: 22, end: 30 });
});

test("window clamps when the list shrinks under it", () => {
  const w = visibleWindow(10, 9, 8, 40);
  assert.deepEqual(w, { start: 2, end: 10 });
});

test("labels: plain count when whole, range when windowed", () => {
  assert.equal(windowLabel("Runs", { start: 0, end: 5 }, 5), "Runs (5)");
  assert.equal(windowLabel("Runs", { start: 2, end: 10 }, 87), "Runs 3–10 of 87");
});

test("page jumps clamp at both ends", () => {
  assert.equal(pageJump(0, 50, 8, -1), 0);
  assert.equal(pageJump(45, 50, 8, 1), 49);
  assert.equal(pageJump(10, 50, 8, 1), 18);
});
