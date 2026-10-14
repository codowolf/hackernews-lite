// Shows the defining phrases of a piece of text in bold maroon.
//
// Two sources of highlights, both optional:
//   - **markers** the model wrote into the text itself (gist, key points, verdicts), and
//   - `phrases`: exact quotes from a verbatim comment (the comment text itself is never altered).
//
// Safety: text from the network is only ever put in text nodes. Nothing here uses innerHTML.

/**
 * Split text into runs, each marked bold or not.
 * Unbalanced `**` is dropped, so a malformed marker never shows up on screen.
 * @param {string} text
 * @param {string[]} [phrases] exact substrings of the plain text to make bold (first match of each)
 * @returns {{text: string, bold: boolean}[]}
 */
export function splitEmphasis(text, phrases = []) {
  const { plain, ranges } = parseMarkers(text ?? "");
  for (const phrase of phrases) {
    const range = findFree(plain, phrase, ranges);
    if (range) ranges.push(range);
  }
  return toRuns(plain, mergeRanges(ranges));
}

/** Build the text as a DocumentFragment, with highlights in <strong class="hl">. */
export function emphasize(text, phrases = []) {
  const fragment = document.createDocumentFragment();
  for (const run of splitEmphasis(text, phrases)) {
    if (!run.bold) {
      fragment.append(document.createTextNode(run.text));
      continue;
    }
    const strong = document.createElement("strong");
    strong.className = "hl";
    strong.textContent = run.text;
    fragment.append(strong);
  }
  return fragment;
}

/** Remove the `**` markers, and note where each marked span sits in the plain text. */
function parseMarkers(text) {
  const ranges = [];
  let plain = "";
  // split() with a capture group puts each **span** at an odd index.
  text.split(/(\*\*[^*\n]+?\*\*)/).forEach((part, index) => {
    if (index % 2 === 1) {
      const inner = part.slice(2, -2);
      ranges.push([plain.length, plain.length + inner.length]);
      plain += inner;
    } else {
      plain += part.replaceAll("*", ""); // a stray, unbalanced marker never reaches the screen
    }
  });
  return { plain, ranges };
}

/** The first place `phrase` occurs in `plain` that does not overlap an existing highlight. */
function findFree(plain, phrase, ranges) {
  const needle = (phrase ?? "").trim();
  if (!needle) return null;
  let from = 0;
  for (;;) {
    const start = plain.indexOf(needle, from);
    if (start < 0) return null;
    const end = start + needle.length;
    if (ranges.every(([s, e]) => end <= s || e <= start)) return [start, end];
    from = start + 1;
  }
}

function mergeRanges(ranges) {
  const sorted = ranges.filter(([s, e]) => e > s).sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const [start, end] of sorted) {
    const previous = merged[merged.length - 1];
    if (previous && start <= previous[1]) previous[1] = Math.max(previous[1], end);
    else merged.push([start, end]);
  }
  return merged;
}

function toRuns(plain, ranges) {
  const runs = [];
  let cursor = 0;
  for (const [start, end] of ranges) {
    if (start > cursor) runs.push({ text: plain.slice(cursor, start), bold: false });
    runs.push({ text: plain.slice(start, end), bold: true });
    cursor = end;
  }
  if (cursor < plain.length) runs.push({ text: plain.slice(cursor), bold: false });
  return runs.length ? runs : [{ text: plain, bold: false }];
}
