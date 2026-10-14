// Run with: node --test tests/js/*.test.mjs   (also run by tests/unit/test_emphasis_js.py when node is installed)
import assert from "node:assert/strict";
import { test } from "node:test";

import { splitLinks } from "../../src/hackernews_lite/web/static/linkify.js";

const joined = (runs) => runs.map((r) => r.text).join("");
const links = (runs) => runs.filter((r) => r.href).map((r) => [r.text, r.href]);

test("a cut-short address links to the full one but still reads as shown", () => {
  const shown = "https://github.com/o/r/blob/master/doc/custom-b...";
  const full = "https://github.com/o/r/blob/master/doc/custom-build.md";
  const text = `¹https://typeof.net/x\n\n²${shown}`;
  const runs = splitLinks(text, [
    { text: "https://typeof.net/x", href: "https://typeof.net/x" },
    { text: shown, href: full },
  ]);
  assert.equal(joined(runs), text);
  assert.deepEqual(links(runs), [["https://typeof.net/x", "https://typeof.net/x"], [shown, full]]);
});

test("addresses the server did not send are found in the text, without the full stop after them", () => {
  const runs = splitLinks("See https://example.com/a. Then (https://example.org/b), done.");
  assert.deepEqual(links(runs), [
    ["https://example.com/a", "https://example.com/a"],
    ["https://example.org/b", "https://example.org/b"],
  ]);
  assert.equal(joined(runs), "See https://example.com/a. Then (https://example.org/b), done.");
});

test("the same address twice is linked twice, each in its own place", () => {
  const url = "https://example.com/same";
  const runs = splitLinks(`${url} and again ${url}`, [
    { text: url, href: url },
    { text: url, href: url },
  ]);
  assert.equal(links(runs).length, 2);
});

test("nothing but web addresses becomes a link", () => {
  const runs = splitLinks("click javascript:alert(1) now", [{ text: "javascript:alert(1)", href: "javascript:alert(1)" }]);
  assert.deepEqual(links(runs), []);
  assert.equal(joined(runs), "click javascript:alert(1) now");
});

test("text without links comes back whole, and nothing breaks on empty input", () => {
  assert.deepEqual(splitLinks("Just words."), [{ text: "Just words.", href: null }]);
  assert.deepEqual(splitLinks(""), []);
  assert.deepEqual(splitLinks(undefined), []);
});
