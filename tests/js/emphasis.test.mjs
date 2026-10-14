// Run with: node --test tests/js/*.test.mjs   (also run by tests/unit/test_emphasis_js.py when node is installed)
import assert from "node:assert/strict";
import { test } from "node:test";

import { splitEmphasis } from "../../src/hackernews_lite/web/static/emphasis.js";

const joined = (runs) => runs.map((r) => r.text).join("");
const bold = (runs) => runs.filter((r) => r.bold).map((r) => r.text);

test("markers become bold runs and disappear from the text", () => {
  const runs = splitEmphasis("Saved **$672 million** in **tax**.");
  assert.equal(joined(runs), "Saved $672 million in tax.");
  assert.deepEqual(bold(runs), ["$672 million", "tax"]);
});

test("phrases are bolded without changing the text", () => {
  const runs = splitEmphasis("I ran this in production for two years.", ["in production", "two years"]);
  assert.equal(joined(runs), "I ran this in production for two years.");
  assert.deepEqual(bold(runs), ["in production", "two years"]);
});

test("a phrase that is not in the text is ignored", () => {
  const runs = splitEmphasis("Plain words only.", ["not here"]);
  assert.deepEqual(runs, [{ text: "Plain words only.", bold: false }]);
});

test("a stray marker never reaches the screen", () => {
  // Pairing is positional, so the exact bold span is not the point: no asterisk may be shown
  // and no word may be lost. (The server already strips unbalanced markers before they get here.)
  const runs = splitEmphasis("Half **open and a **fine one** here");
  assert.ok(!joined(runs).includes("*"));
  assert.equal(joined(runs), "Half open and a fine one here");
  assert.equal(joined(splitEmphasis("a lone ** marker")), "a lone  marker");
});

test("a phrase that overlaps a marker is left out rather than doubled", () => {
  const runs = splitEmphasis("We **saw the same thing** at work.", ["same thing at work"]);
  assert.equal(joined(runs), "We saw the same thing at work.");
  assert.deepEqual(bold(runs), ["saw the same thing"]);
});

test("only the first match of a repeated phrase is bold", () => {
  const runs = splitEmphasis("cost matters, and cost again", ["cost"]);
  assert.deepEqual(bold(runs), ["cost"]);
  assert.equal(joined(runs), "cost matters, and cost again");
});

test("empty and missing input are safe", () => {
  assert.equal(joined(splitEmphasis("")), "");
  assert.equal(joined(splitEmphasis(undefined)), "");
  assert.equal(joined(splitEmphasis("text", [null, "", "  "])), "text");
});
