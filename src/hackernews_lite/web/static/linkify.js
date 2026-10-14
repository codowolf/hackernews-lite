// Turns the web addresses in a comment into real links.
//
// Hacker News shows long addresses cut short ("https://github.com/o/r/blob/master/doc/custom-b..."),
// so the server sends each link's full address next to the text. Addresses it did not send are
// still found in the text. Only http and https links are ever made; nothing here uses innerHTML.

const WEB = /^https?:\/\//i;
const BARE = /https?:\/\/[^\s<>"]+/g;
const TRAILING = /[.,;:!?)\]}'"]+$/; // punctuation that ends a sentence, not an address

/**
 * Split text into runs, each plain or a link.
 * @param {string} text
 * @param {{text: string, href: string}[]} [links] in the order they appear in the text
 * @returns {{text: string, href: string | null}[]}
 */
export function splitLinks(text, links = []) {
  const source = text ?? "";
  const found = []; // [start, end, href], in text order
  let cursor = 0;
  for (const link of links) {
    if (!link?.text || !WEB.test(link.href ?? "")) continue;
    const start = source.indexOf(link.text, cursor);
    if (start < 0) continue;
    found.push([start, start + link.text.length, link.href]);
    cursor = start + link.text.length;
  }
  const pieces = [];
  let from = 0;
  for (const [start, end, href] of [...found, [source.length, source.length, null]]) {
    pieces.push(...bareAddresses(source, from, start));
    if (href) pieces.push([start, end, href]);
    from = end;
  }
  return toRuns(source, pieces);
}

/** Addresses in source[from, to) that the server did not describe. */
function bareAddresses(source, from, to) {
  const out = [];
  for (const match of source.slice(from, to).matchAll(BARE)) {
    const address = match[0].replace(TRAILING, "");
    if (address.length > "https://".length) {
      out.push([from + match.index, from + match.index + address.length, address]);
    }
  }
  return out;
}

function toRuns(source, pieces) {
  const runs = [];
  let at = 0;
  for (const [start, end, href] of pieces) {
    if (start > at) runs.push({ text: source.slice(at, start), href: null });
    runs.push({ text: source.slice(start, end), href });
    at = end;
  }
  if (at < source.length) runs.push({ text: source.slice(at), href: null });
  return runs;
}

/** Build the text as a DocumentFragment, with each address as an <a> that opens in a new tab. */
export function linkify(text, links = []) {
  const fragment = document.createDocumentFragment();
  for (const run of splitLinks(text, links)) {
    if (!run.href) {
      fragment.append(document.createTextNode(run.text));
      continue;
    }
    const anchor = document.createElement("a");
    anchor.href = run.href;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    anchor.className = "ext";
    anchor.textContent = run.text;
    fragment.append(anchor);
  }
  return fragment;
}
