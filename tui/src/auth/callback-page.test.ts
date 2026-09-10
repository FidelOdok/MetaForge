import { test } from "node:test";
import assert from "node:assert/strict";
import { renderCallbackPage } from "./callback-page.js";

test("success page carries the MetaForge wordmark, heading, and message", () => {
  const html = renderCallbackPage(true, "You can close this window.");
  assert.match(html, /class="wordmark">Forge</); // rendered uppercase via CSS text-transform
  assert.match(html, /Authorization successful/);
  assert.match(html, /You can close this window\./);
  assert.match(html, /color-scheme:\s*dark/); // Kinetic Console is always-dark
});

test("failure page uses the error accent and heading", () => {
  const html = renderCallbackPage(false, "state mismatch");
  assert.match(html, /Login failed/);
  assert.match(html, /state mismatch/);
  assert.match(html, /#ffb4ab/); // Kinetic Console --color-error
});

test("escapes HTML in the message so injected markup can't render", () => {
  const html = renderCallbackPage(true, "<script>alert(1)</script>");
  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /&lt;script&gt;/);
});
