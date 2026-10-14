// Hackernews Lite player. No build step: plain ES modules.
// All text from the API is inserted with textContent, never innerHTML (comments are untrusted).

import { emphasize } from "./emphasis.js";
import { linkify } from "./linkify.js";

const $ = (id) => document.getElementById(id);

/** Build a DOM element. String children become text nodes; false/null children are skipped. */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (value !== false && value != null) el.setAttribute(key, value === true ? "" : value);
  }
  el.append(...children.filter((child) => child !== false && child != null));
  return el;
}

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode: fine */ }
}
function recall(key, fallback) {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}

const KIND_LABEL = {
  gist: "Gist",
  key_points: "Key points",
  reaction: "How the thread reacted",
  agree: "Top agreeing",
  disagree: "Top disagreeing",
  debate: "Debate",
  comment: "Comment",
};
const STANCE_ORDER = ["agree", "disagree", "mixed", "neutral"];
const PICK_LABEL = { llm: "Claude's picks", jev: "Jev's picks" };
const SPEEDS = [1, 1.25, 1.5, 1.75, 2];

const state = {
  config: null,
  feed: recall("feed", ""), // which list is showing: top, 24h, 48h, week or best
  stories: [],
  pollTimer: null, // set while stories are being prepared in the background
  counts: { archived: 0, ignored: 0 }, // for the badges on the Archived and Ignored tabs
  updatedAt: null, // when the showing list was last refreshed from Hacker News
  storyIndex: -1, // index into state.stories
  detail: null,
  segment: -1,
  picks: recall("picks", "llm"),
  voice: null,
  speed: Number(recall("speed", "1.5")),
  nav: 0, // bumped on every navigation, so a slow response never overwrites a newer one
  pushed: false, // whether we added a history entry when opening the player
  listening: false, // true once the reader has asked for the voice; until then the page is text only
  mode: recall("mode", ""), // the Daily list's choice: top5, top10 or top20
  days: 0, // how many days the Daily list is showing (grows with "Show earlier days")
  hasMoreDays: false,
  dayPools: {}, // per day: how many stories reached the minimum points
  miniCollapsed: recall("mini", "") === "collapsed", // the mini player shrunk to a play button
};

const audio = $("audio");

// ---------- the URL is the source of truth for which article is open ----------

const STORY_HASH = /^#\/story\/(\d+)$/;

/** The story id in a URL hash such as "#/story/123", or null for anything else (including ""). */
function storyIdFromHash(hash) {
  const match = STORY_HASH.exec(hash);
  return match ? Number(match[1]) : null;
}

function storyHash(id) {
  return `#/story/${id}`;
}

/** The address of the list: this page without an article hash, keeping ?feed= and ?theme=. */
function listUrl() {
  return location.pathname + location.search;
}

// ---------- the top bar gets out of the way while you read the list ----------

let lastScroll = 0;
window.addEventListener("scroll", () => {
  const y = window.scrollY;
  const bar = $("topbar");
  const menuOpen = !$("settings").hidden;
  if (y < 80 || y < lastScroll - 6 || menuOpen) bar.classList.remove("away");
  else if (y > lastScroll + 6) bar.classList.add("away");
  lastScroll = y;
}, { passive: true });

// The article bar gets out of the way while you read down the page and returns when you scroll up.
// It stays put while the voice is on, because that is where the controls are.
let lastPlayerScroll = 0;
$("player").addEventListener("scroll", () => {
  const y = $("player").scrollTop;
  const bar = document.querySelector(".reader-bar");
  if (state.listening || y < 60 || y < lastPlayerScroll - 6) bar.classList.remove("away");
  else if (y > lastPlayerScroll + 6) bar.classList.add("away");
  lastPlayerScroll = y;
}, { passive: true });

// ---------- story list ----------

async function loadStories({ quiet = false } = {}) {
  const feed = state.feed;
  if (!quiet) {
    $("status").textContent = "Loading…";
    $("stories").replaceChildren();
  }
  let list;
  try {
    list = await api(listUrl_for(feed));
  } catch (error) {
    if (feed === state.feed) $("status").textContent = `Could not load this list. ${error.message}`;
    return;
  }
  if (feed !== state.feed) return; // the user switched lists while this loaded

  const playingId = state.detail?.id;
  state.stories = list.stories;
  if (!$("player").hidden) state.storyIndex = state.stories.findIndex((story) => story.id === playingId);
  $("feed-title").textContent = list.feed.title;
  state.updatedAt = list.updated_at;
  state.counts = { archived: 0, ignored: 0, ...list.counts };
  state.hasMoreDays = list.has_more_days;
  state.dayPools = list.day_pools ?? {};
  renderCount();
  renderList();
  renderFeeds();
  renderModes();

  if (list.pending_thumbs && !quiet) setTimeout(() => state.feed === feed && loadStories({ quiet: true }), 7000);
  if (state.stories.some((s) => s.job === "queued" || s.job === "running")) pollJobs();
}

/** The API address for a list. The Daily list also says how much of each day, and how many days. */
function listUrl_for(feed) {
  if (!isDaily(feed)) return `/api/stories?feed=${feed}`;
  const { page_days: pageDays } = state.config.daily;
  if (!state.days) state.days = pageDays;
  return `/api/stories?feed=${feed}&mode=${state.mode}&days=${state.days}`;
}

function isDaily(feed = state.feed) {
  return state.config.feeds.find((f) => f.id === feed)?.grouped === true;
}

/** hckrnews-style choice of how much of each day to show; only on the Daily list. */
function renderModes() {
  const box = $("modes");
  $("subbar").hidden = !isDaily();
  if ($("subbar").hidden) return;
  box.replaceChildren(
    ...state.config.daily.modes.map((mode) => h(
      "button",
      { "aria-pressed": String(mode.id === state.mode), onclick: () => switchMode(mode.id) },
      mode.label,
    )),
  );
}

function switchMode(id) {
  if (id === state.mode) return;
  state.mode = id;
  state.days = state.config.daily.page_days;
  remember("mode", id);
  window.scrollTo({ top: 0 });
  loadStories();
}

function showEarlierDays() {
  state.days = Math.min(state.days + state.config.daily.page_days, state.config.daily.max_days);
  loadStories({ quiet: true }); // keep the list on screen while older days are added
}

function renderFeeds() {
  const tab = (feed, { badge = false } = {}) => h(
    "button",
    { "aria-pressed": String(feed.id === state.feed), onclick: () => switchFeed(feed.id) },
    feed.label,
    badge && state.counts[feed.id] > 0 && h("span", { class: "badge" }, String(state.counts[feed.id])),
  );
  const library = state.config.library ?? [];
  const inLibrary = library.find((feed) => feed.id === state.feed);
  $("feeds").replaceChildren(
    ...(inLibrary ? [tab(inLibrary)] : []), // say where you are when it is not one of the usual lists
    ...state.config.feeds.map((feed) => tab(feed)),
  );
  $("library").replaceChildren(...library.map((feed) => tab(feed, { badge: true })));
}

function renderCount() {
  const library = state.feed === "archived" || state.feed === "ignored";
  $("status").textContent = state.stories.length ? "" : library ? "Nothing here." : "Nothing here yet.";
  const info = state.stories.length
    ? `${state.stories.length} stories${library ? "" : ` · updated ${timeAgo(state.updatedAt)}`}`
    : "";
  $("count").textContent = info;
  $("count2").textContent = info; // the menu shows it too, for lists that have no sub-bar
}

/** The story cards, with a date heading wherever the day changes (the daily-top list). */
function renderList() {
  const items = [];
  let day = null;
  state.stories.forEach((story) => {
    if (story.day && story.day !== day) {
      day = story.day;
      items.push(renderDayHeading(day));
    }
    items.push(renderStoryRow(story));
  });
  if (state.hasMoreDays && state.stories.length) {
    items.push(h(
      "li", { class: "more" },
      h("button", { onclick: showEarlierDays }, "Show earlier days"),
    ));
  }
  $("stories").replaceChildren(...items);
}

/** "Today", "Yesterday", or "Mon 12 Oct". */
function dayLabel(day) {
  const date = new Date(`${day}T00:00:00`);
  const midnight = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const daysAgo = Math.round((midnight(new Date()) - midnight(date)) / 86400000);
  if (daysAgo === 0) return "Today";
  if (daysAgo === 1) return "Yesterday";
  return date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" }).replace(",", "");
}

function renderDayHeading(day) {
  const shown = state.stories.filter((story) => story.day === day).length;
  const pool = state.dayPools[day];
  return h(
    "li", { class: "day", role: "heading", "aria-level": "3" },
    h("span", { class: "day-label" }, dayLabel(day)),
    pool !== undefined && h("span", { class: "day-meta" }, `${shown} of ${pool}`),
  );
}

function switchFeed(id) {
  if (id === state.feed) return;
  state.feed = id;
  state.days = 0; // the Daily list starts again from its first few days
  remember("feed", id);
  window.scrollTo({ top: 0 });
  loadStories();
}

// ---------- preparing stories that have not been analyzed yet ----------

/** Start analyzing a story that has not been analyzed yet. Progress shows on its card and page. */
async function prepareStory(id) {
  const story = state.stories.find((s) => s.id === id);
  const apply = (job, detail = null) => {
    if (story) { story.job = job; story.job_detail = detail; refreshCard(story); }
    if (state.detail?.id === id) { state.detail.job = job; state.detail.job_detail = detail; renderPreparePrompt(state.detail); }
  };
  apply("queued");
  try {
    await fetch(`/api/stories/${id}/prepare`, { method: "POST" });
  } catch {
    apply("failed", "Could not reach the server");
    return;
  }
  pollJobs();
}

/** Tells the reader what the analysis is doing. A button only appears if it failed or never started. */
function renderPreparePrompt(detail) {
  const box = $("prepare-prompt");
  const busy = detail.job === "queued" || detail.job === "running";
  const failed = detail.job === "failed";
  box.hidden = !state.config.can_prepare || (detail.ready && !busy && !failed);
  if (box.hidden) return;
  $("prepare-text").textContent = busy
    ? (detail.ready
      ? "Still analyzing. More appears below as it is ready."
      : "Analyzing this story. It takes about a minute. The top comments are below meanwhile.")
    : failed
      ? `The analysis did not finish${detail.job_detail ? ` (${detail.job_detail})` : ""}.`
      : "This story has not been analyzed yet.";
  const button = $("prepare-btn");
  button.hidden = busy;
  button.textContent = failed ? "Try again" : "Analyze this story";
}

/**
 * While the open story is being analyzed, pull its page again so the summary, reaction and picks
 * appear as each one is finished. Left alone while the voice is on, so playback is never reset.
 */
async function refreshOpenStory(finishedIds = []) {
  const openId = state.detail?.id;
  if ($("player").hidden || openId === undefined || state.listening) return;
  const working = state.detail.job === "queued" || state.detail.job === "running";
  if (!working && !finishedIds.includes(openId)) return;
  let detail;
  try { detail = await api(`/api/stories/${openId}?picks=${state.picks}`); } catch { return; }
  if (state.detail?.id !== openId) return; // the reader moved on
  const changed = detail.job !== state.detail.job || detail.ready !== state.detail.ready
    || detail.depth !== state.detail.depth || detail.segments.length !== state.detail.segments.length;
  if (changed) showDetail(detail);
}

/** Check on background work every few seconds, and update the list when something finishes. */
function pollJobs() {
  if (state.pollTimer) return;
  state.pollTimer = setInterval(async () => {
    let jobs;
    try { jobs = await api("/api/jobs"); } catch { return; }
    const active = jobs.filter((job) => job.state === "queued" || job.state === "running");
    const finishedIds = state.stories
      .filter((story) => story.job && !active.some((job) => job.story_id === story.id))
      .map((story) => story.id);
    if (finishedIds.length) await loadStories({ quiet: true });
    await refreshOpenStory(finishedIds);
    if (!active.length) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }, 3000);
}

function refreshCard(story) {
  const old = $("stories").querySelector(`[data-story-id="${story.id}"]`);
  if (old) old.replaceWith(renderStoryRow(story));
}

/** "35m ago", "5h ago", "3d ago". */
function timeAgo(iso) {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** An icon from the sprite in index.html. */
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "ic");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

/** One number with its icon. A hot one (climbing fast, or a very busy thread) is drawn in orange. */
function stat(name, value, label, hot = false) {
  return h(
    "span", { class: `stat stat-${name}${hot ? " hot" : ""}`, title: hot ? `Hot: many ${label}` : null },
    icon(name), h("span", {}, String(value)), h("span", { class: "sr" }, ` ${label}${hot ? " (hot)" : ""}`),
  );
}

/** A stable color for a site, so each story without a picture still has its own look. */
function hueFor(text) {
  let hash = 0;
  for (const char of text) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return hash;
}

function renderStoryRow(story) {
  const site = story.domain ?? story.title;
  const media = story.thumb
    ? h("img", { class: "thumb", src: `/api/stories/${story.id}/thumb`, alt: "", loading: "lazy", decoding: "async" })
    : h(
      "span", { class: "tile", style: `--hue:${hueFor(site)}`, "aria-hidden": "true" },
      (site.match(/[a-z0-9]/i)?.[0] ?? "?").toUpperCase(),
    );
  const status = story.ready ? null
    : story.job === "failed" ? "analysis failed"
      : story.job === "queued" || story.job === "running" ? "preparing…" : null;
  const meta = [story.domain, timeAgo(story.created_at), status].filter(Boolean).join(" · ");
  const content = h(
    "span", { class: "row" },
    media,
    h(
      "span", { class: "body" },
      h(
        "h3", {},
        story.title,
        h("span", { class: "meta" }, ` ${meta}`),
      ),
    ),
    h(
      "span", { class: "stats" },
      stat("up", story.points, "points", story.hot_points),
      stat("comment", story.num_comments, "comments", story.hot_comments),
    ),
  );
  // The whole card is one button. Archive and ignore are its siblings, never children, so a tap on
  // them never opens the story. An unanalyzed story opens to a page that offers to prepare it.
  const open = h(
    "button",
    { class: `story${story.ready ? "" : " unprepared"}`, title: story.gist ?? "", onclick: () => openStory(indexOf(story)) },
    content,
  );
  return h("li", { class: "card", "data-story-id": String(story.id) }, open, h("div", { class: "actions" }, ...actionsFor(story)));
}

const indexOf = (story) => state.stories.findIndex((s) => s.id === story.id);

// ---------- archive and ignore ----------

const GLYPHS = {
  archive: "M4 7h16v3.5H4zM5.5 10.5V19h13v-8.5M10 14h4",
  ignore: "M3 3l18 18M10.6 6.1A9.6 9.6 0 0 1 12 6c5 0 8.5 4.2 9.5 6-.5 1-1.5 2.4-2.9 3.6M6.4 7.7C4.6 9 3.3 10.8 2.5 12c1.2 2.2 4.5 6 9.5 6 1.5 0 2.8-.4 3.9-1M9.9 9.9a3 3 0 0 0 4.2 4.2",
  restore: "M9 5L4 10l5 5M4 10h9a5.5 5.5 0 0 1 0 11h-2",
};

/** A small stroke icon drawn from GLYPHS (kept here so the shared sprite stays untouched). */
function glyph(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "ic glyph");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", GLYPHS[name]);
  svg.append(path);
  return svg;
}

function actionsFor(story) {
  if (story.state) { // in the Archived or Ignored tab: the only action is to bring it back
    return [
      h(
        "button",
        { class: "act with-text", "aria-label": `Restore: ${story.title}`, onclick: () => putAside(story, "restore") },
        glyph("restore"), "Restore",
      ),
    ];
  }
  const act = (kind, label) => h(
    "button",
    { class: `act act-${kind}`, "aria-label": `${label}: ${story.title}`, title: label, onclick: () => putAside(story, kind) },
    glyph(kind),
  );
  return [act("archive", "Archive"), act("ignore", "Ignore")];
}

const PUT_ASIDE_DONE = { archive: "Archived", ignore: "Ignored", restore: "Restored" };
const LIBRARY_OF = { archive: "archived", ignore: "ignored" };

/** Move a story into the Archived or Ignored list (or back out). The card goes immediately. */
async function putAside(story, action) {
  state.stories = state.stories.filter((s) => s.id !== story.id);
  if (story.state) state.counts[story.state] = Math.max(0, state.counts[story.state] - 1);
  if (LIBRARY_OF[action]) state.counts[LIBRARY_OF[action]] += 1;
  renderList();
  renderFeeds();
  renderCount();

  let ok = false;
  try {
    ok = (await fetch(`/api/stories/${story.id}/${action}`, { method: "POST" })).ok;
  } catch { /* handled below */ }
  if (!ok) {
    showToast(`Could not ${action} that story`);
    loadStories({ quiet: true }); // show what the server really has
    return;
  }
  const undo = action === "restore" ? null : async () => {
    await fetch(`/api/stories/${story.id}/restore`, { method: "POST" }).catch(() => {});
    loadStories({ quiet: true });
  };
  showToast(PUT_ASIDE_DONE[action], undo);
}

let toastTimer = null;

function showToast(message, undo = null) {
  let toast = $("toast");
  if (!toast) {
    toast = h("div", { id: "toast", class: "toast", role: "status" });
    document.body.append(toast);
  }
  toast.replaceChildren(
    h("span", {}, message),
    undo && h("button", { onclick: () => { hideToast(); undo(); } }, "Undo"),
  );
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, 6000);
}

function hideToast() { $("toast")?.classList.remove("show"); }

// ---------- opening and closing the player ----------

/**
 * Open a story as text. It never starts the voice by itself; that waits for the Listen button.
 * `autoplay` is only for carrying on to the next story while the reader is already listening.
 * `push: false` is for an address that already names the story (a shared link, reload, Forward).
 */
async function openStory(index, options = {}) {
  const story = state.stories[index];
  if (story.picks.length && !story.picks.includes(state.picks)) state.picks = story.picks[0];
  return openStoryById(story.id, { ...options, index });
}

/** Open a story by id. It need not be in the list on screen, so a link or a refresh always works. */
async function openStoryById(id, { autoplay = false, push = true, index = -1 } = {}) {
  const nav = ++state.nav;
  let detail;
  try {
    detail = await api(`/api/stories/${id}?picks=${state.picks}`);
  } catch {
    history.replaceState(null, "", listUrl()); // an id that does not exist: back to the list
    return;
  }
  if (nav !== state.nav) return; // the user moved on while this loaded

  state.storyIndex = index >= 0 ? index : state.stories.findIndex((story) => story.id === id);
  const alreadyOpen = !$("player").hidden;
  showDetail(detail);
  setListening(autoplay);
  if (alreadyOpen) {
    // Moving between stories adds no history entry, so Back still returns to the list in one step.
    history.replaceState({ story: id }, "", storyHash(id));
  } else {
    $("player").hidden = false;
    document.body.style.overflow = "hidden";
    if (push) {
      history.pushState({ story: id }, "", storyHash(id));
      state.pushed = true;
    }
  }
  $("player").scrollTop = 0;
  if (autoplay) playSegment(0);
  startAnalysisIfNeeded(detail);
}

/** Opening a story analyzes it at once if it has not been, so reading never waits on a button. */
function startAnalysisIfNeeded(detail) {
  // Analyzed under older settings, with no reaction card: fill it in. It is the cheap one (Jev).
  const lacksReaction = detail.ready && detail.depth === null && !detail.job
    && detail.analyzed_comments >= MIN_COMMENTS_FOR_REACTION;
  if (lacksReaction && state.config.prepare_on_open) {
    prepareStory(detail.id);
    return;
  }
  if (detail.ready || detail.job === "failed" || !state.config.prepare_on_open) {
    if (detail.job === "queued" || detail.job === "running") pollJobs();
    return;
  }
  prepareStory(detail.id); // jumps ahead of any background preparation
}

function showDetail(detail) {
  state.detail = detail;
  state.segment = -1;
  $("player-title").textContent = detail.title;
  $("player-links").replaceChildren(
    ...[
      detail.url && h("a", { href: detail.url, target: "_blank", rel: "noopener" }, "Article"),
      detail.url && " · ",
      h("a", { href: detail.hn_url, target: "_blank", rel: "noopener" }, "Discussion"),
    ].filter(Boolean),
  );
  $("bar-title").textContent = detail.title;
  document.querySelector(".reader-bar").classList.remove("away");
  $("player").classList.remove("title-scrolled");
  $("listen-prompt").hidden = detail.segments.length === 0; // nothing to read aloud
  renderPreparePrompt(detail);
  renderAudioNote(detail.audio_bytes);
  renderAnalysisNote(detail);
  const note = $("player-note");
  note.hidden = !detail.article_note;
  note.textContent = detail.article_note ?? "";
  renderPicksToggle(detail);
  renderSegments();
  renderDeepenButton(detail);
  setProgress(0);
  setNow("");
}

// ---------- the voice is opt-in ----------

/** Turn the voice on or off for this page. Off means text only: no audio is requested or played. */
function setListening(on) {
  state.listening = on;
  $("player").classList.toggle("listening", on);
  $("mini").hidden = !on;
  if (!on) {
    audio.pause();
    setNow("");
  }
}

/** The reader pressed Listen: reveal the mini player and start from the top. */
function startListening() {
  setListening(true);
  playSegment(0);
}

function applyMiniCollapsed(collapsed) {
  state.miniCollapsed = collapsed;
  $("mini").toggleAttribute("data-collapsed", collapsed);
  $("mini-collapse").setAttribute("aria-expanded", String(!collapsed));
  $("mini-collapse").setAttribute("aria-label", collapsed ? "Expand player" : "Shrink player");
  remember("mini", collapsed ? "collapsed" : "");
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** When the analysis was made and how much of the thread it read, with a way to redo it. */
function renderAnalysisNote(detail) {
  const note = $("analysis-note");
  note.hidden = !detail.analyzed_at;
  if (note.hidden) return;
  const behind = detail.current_comments - detail.analyzed_comments;
  const ageHours = (Date.now() - new Date(detail.analyzed_at).getTime()) / 3600000;
  const stale = behind >= 10 || (behind > 0 && behind / Math.max(detail.current_comments, 1) > 0.15) || ageHours > 24;
  note.classList.toggle("stale", stale);
  $("analysis-text").textContent =
    `Analyzed ${timeAgo(detail.analyzed_at)} · read ${detail.analyzed_comments} of ${detail.current_comments} comments ·`;
  $("refresh-analysis").disabled = false;
  $("refresh-analysis").textContent = "Refresh analysis";
}

async function refreshAnalysis() {
  const id = state.detail.id;
  const button = $("refresh-analysis");
  button.disabled = true;
  button.textContent = "Refreshing… about a minute";
  try {
    await fetch(`/api/stories/${id}/prepare?refresh=true`, { method: "POST" });
  } catch {
    button.disabled = false;
    button.textContent = "Could not reach the server. Try again";
    return;
  }
  const timer = setInterval(async () => {
    let jobs;
    try { jobs = await api("/api/jobs"); } catch { return; }
    const job = jobs.find((j) => j.story_id === id);
    if (job && (job.state === "queued" || job.state === "running")) return;
    clearInterval(timer);
    if (state.detail?.id !== id) return; // the reader moved on; the list picks the change up
    if (job?.state === "failed") {
      button.disabled = false;
      button.textContent = `Could not refresh (${job.detail}). Try again`;
      return;
    }
    showDetail(await api(`/api/stories/${id}?picks=${state.picks}`));
  }, 3000);
}

/** Show how much disk this story's saved voice uses, with a way to clear it. */
function renderAudioNote(bytes) {
  $("audio-note").hidden = !(bytes > 0);
  $("audio-size").textContent = bytes > 0 ? `Voice files: ${formatBytes(bytes)} ·` : "";
}

async function clearAudio() {
  const id = state.detail.id;
  audio.pause();
  let response;
  try {
    response = await fetch(`/api/stories/${id}/audio`, { method: "DELETE" });
  } catch {
    response = null;
  }
  if (!response?.ok) {
    setNow("Could not clear the voice files");
    return;
  }
  if (state.detail?.id === id) {
    state.detail.audio_bytes = 0;
    renderAudioNote(0);
  }
  if (state.listening) setNow("Cleared. Press play to make the voice again.");
}

function renderPicksToggle(detail) {
  const box = $("picks-toggle");
  box.hidden = detail.picks.length < 2;
  box.replaceChildren(
    h(
      "div", { class: "chips-track" },
      ...detail.picks.map((method) =>
        h("button", {
          "aria-pressed": String(method === state.picks),
          onclick: () => switchPicks(method),
        }, PICK_LABEL[method] ?? method)),
    ),
  );
}

async function switchPicks(method) {
  if (method === state.picks) return;
  const nav = ++state.nav;
  state.picks = method;
  remember("picks", method);
  audio.pause();
  const detail = await api(`/api/stories/${state.detail.id}?picks=${method}`);
  if (nav === state.nav) showDetail(detail);
}

/** Hide the article page. Every way of closing it ends here, so the address is always cleaned up. */
function closePlayerUi() {
  state.nav++;
  setListening(false);
  audio.removeAttribute("src");
  audio.load();
  $("player").hidden = true;
  document.body.style.overflow = "";
  state.pushed = false;
  // If the address still names the article, a refresh would reopen it. (After Back it is already clean.)
  if (storyIdFromHash(location.hash) !== null) history.replaceState(null, "", listUrl());
}

/** The back arrow. Undo our own history entry if we made one; otherwise just close, without leaving the site. */
function closePlayer() {
  if (state.pushed) history.back(); // popstate then closes the page
  else closePlayerUi();
}

/** Make the page match the address: a story hash opens that story as text, no hash closes it. */
async function syncFromUrl() {
  const id = storyIdFromHash(location.hash);
  const open = !$("player").hidden;
  if (id === null) {
    if (open) closePlayerUi();
    return;
  }
  if (open && state.detail?.id === id) return;
  await openStoryById(id, { push: false }); // whether or not it is in the list on screen
}

// ---------- playback ----------

function renderSegments() {
  $("segments").replaceChildren(
    ...state.detail.segments.map((segment, i) =>
      h(
        "li",
        { class: "segment", "data-kind": segment.kind, tabindex: "0", onclick: () => playSegment(i) },
        h("p", { class: "label" }, ...segmentLabel(segment)),
        ...segmentBody(segment),
      )),
  );
}

/** "#3 of 41", or "reply 2 · thread #7 of 41": where a comment sits in Hacker News's ranking. */
function rankText(segment) {
  if (!segment.rank) return null;
  const of = segment.thread_total ? ` of ${segment.thread_total}` : "";
  return segment.reply_rank ? `reply ${segment.reply_rank} · thread #${segment.rank}${of}` : `#${segment.rank}${of}`;
}

function segmentLabel(segment) {
  const rank = rankText(segment);
  return [
    h("span", { class: "kind" }, KIND_LABEL[segment.kind]),
    segment.author && h("span", { class: "by" }, segment.author),
    h(
      "span", { class: "label-actions" },
      rank && h(
        "span",
        { class: "rank-tag", title: "Its place in Hacker News's ranking. Hacker News hides comment votes, so rank is what can be shown." },
        rank,
      ),
      segment.comment_id && h(
        "button",
        {
          class: "ctx-btn",
          title: "See this comment in its whole thread",
          "aria-label": "See this comment in its whole thread",
          onclick: (event) => { event.stopPropagation(); openThread(state.detail.id, segment.comment_id); },
        },
        icon("thread"), "Thread",
      ),
    ),
  ];
}

// ---------- a comment in its whole conversation ----------

const thread = { open: false, pushed: false, opener: null };

/** Open the conversation a comment belongs to as a sheet over the article, with that comment marked. */
async function openThread(storyId, commentId) {
  thread.open = true;
  thread.opener = document.activeElement;
  $("thread").hidden = false;
  $("thread-title").textContent = "Thread";
  $("thread-sub").textContent = "";
  $("thread-hn").href = state.detail?.hn_url ?? "#";
  $("thread-body").replaceChildren(h("p", { class: "status" }, "Loading the thread…"));
  $("thread-close").focus({ preventScroll: true });
  history.pushState({ thread: true }, "", location.href); // so Back closes this, not the article
  thread.pushed = true;

  let data;
  try {
    data = await api(`/api/stories/${storyId}/comments/${commentId}`);
  } catch {
    if (thread.open) $("thread-body").replaceChildren(h("p", { class: "status" }, "This comment is not in the saved thread."));
    return;
  }
  if (!thread.open) return; // closed while it loaded
  renderThread(data);
}

function renderThread(data) {
  const count = data.nodes.length;
  $("thread-title").textContent = data.around ? `Comment #${data.rank} of ${data.thread_total}` : `Thread #${data.rank} of ${data.thread_total}`;
  $("thread-sub").textContent = `${count} ${count === 1 ? "comment" : "comments"}`;
  const body = $("thread-body");
  const nodes = data.nodes.map((node, index) => threadNode(node, data.nodes[index + 1]));
  body.replaceChildren(
    data.around && h("p", { class: "thread-note" }, "Nobody has replied to this comment yet. Here it is among the comments around it."),
    ...nodes,
  );
  const target = body.querySelector(".tc.target");
  // Centered, so the comments around it show; a long comment starts at the top so its beginning is not cut off.
  target?.scrollIntoView({ block: target.offsetHeight > body.clientHeight * 0.8 ? "start" : "center" });
}

/** One comment of the conversation. A comment with replies can be folded to hide everything under it. */
function threadNode(node, next) {
  const hasReplies = Boolean(next) && next.depth > node.depth;
  const article = h(
    "article",
    { class: `tc ${node.relation}`, style: `--depth:${Math.min(node.depth, 8)}`, "data-depth": node.depth },
    h(
      "header", { class: "tc-head" },
      hasReplies && h(
        "button",
        { class: "tc-fold", "aria-expanded": "true", title: "Fold the replies under this comment", onclick: () => toggleFold(article) },
        icon("chevron"),
      ),
      h("span", { class: "tc-author" }, node.author),
      node.relation === "target" && h("span", { class: "tc-here" }, "This comment"),
      node.relation === "ancestor" && h("span", { class: "tc-rel" }, "above it"),
      node.relation === "reply" && h("span", { class: "tc-rel" }, "reply"),
      h("a", { class: "tc-hn", href: node.link, target: "_blank", rel: "noopener" }, "Hacker News ↗"),
    ),
    h("p", { class: "tc-text" }, linkify(node.text, node.links)),
  );
  return article;
}

/** Fold or unfold a comment's replies: everything after it that sits deeper, until the next sibling. */
function toggleFold(article) {
  const folded = article.classList.toggle("folded");
  const button = article.querySelector(".tc-fold");
  button.setAttribute("aria-expanded", String(!folded));
  button.title = folded ? "Show the replies under this comment" : "Fold the replies under this comment";

  const nodes = [...$("thread-body").querySelectorAll(".tc")];
  let foldedAt = null; // depth of the nearest folded comment above, while inside it
  for (const node of nodes) {
    const depth = Number(node.dataset.depth);
    node.hidden = foldedAt !== null && depth > foldedAt;
    if (!node.hidden) foldedAt = node.classList.contains("folded") ? depth : null;
  }

  button.querySelector(".tc-count")?.remove();
  if (!folded) return;
  const depth = Number(article.dataset.depth);
  const below = nodes.slice(nodes.indexOf(article) + 1);
  const replies = below.findIndex((node) => Number(node.dataset.depth) <= depth);
  button.append(h("span", { class: "tc-count" }, `+${replies < 0 ? below.length : replies}`));
}

function hideThread() {
  thread.open = false;
  thread.pushed = false;
  $("thread").hidden = true;
  thread.opener?.focus?.({ preventScroll: true });
  thread.opener = null;
}

function closeThread() {
  if (thread.pushed) history.back(); // popstate then hides it
  else hideThread();
}

function segmentBody(segment) {
  if (segment.reaction) return reactionBody(segment.reaction);
  if (segment.debate) return debateBody(segment.debate);
  if (segment.bullets.length) {
    return [
      h("ul", { class: "bullets" }, ...segment.bullets.map((point) => h("li", {}, emphasize(point)))),
      segment.reason && h("p", { class: "why" }, segment.reason),
    ];
  }
  const text = h("p", { class: "text" }, emphasize(segment.display, segment.emphasis ?? []));
  const links = segment.links?.length > 0 && h(
    "p", { class: "links" },
    ...segment.links.map((link) => h("a", { href: link.href, target: "_blank", rel: "noopener noreferrer", title: link.href }, link.label)),
  );
  return [
    segment.intro && h("p", { class: "intro" }, `${segment.intro}:`),
    text,
    segment.full && excerptNote(segment, text, links),
    segment.reason && h("p", { class: "why" }, segment.reason),
    links,
  ];
}

/**
 * Under a comment that was cut short for the card: says so, and shows the rest in place.
 * The voice still reads only the excerpt; this is for the eyes.
 */
function excerptNote(segment, text, links) {
  const { full } = segment;
  const shown = segment.display.split(/\s+/).filter(Boolean).length;
  const label = h("span", {}, "");
  const toggle = h("button", { class: "linkish", "aria-expanded": "false" }, "");
  let expanded = false;
  const render = () => {
    label.textContent = expanded ? `All ${full.words} words · ` : `Showing ${shown} of ${full.words} words · `;
    toggle.textContent = expanded ? "Show less" : "Read the full comment";
    toggle.setAttribute("aria-expanded", String(expanded));
    text.replaceChildren(expanded ? linkify(full.text, full.links) : emphasize(segment.display, segment.emphasis ?? []));
    if (links) links.hidden = expanded; // the links are inside the text now
  };
  toggle.addEventListener("click", (event) => {
    event.stopPropagation(); // a tap here must not jump the voice to this card
    expanded = !expanded;
    render();
  });
  render();
  return h("p", { class: "excerpt-note" }, label, toggle);
}

function reactionBody(reaction) {
  const counts = reaction.top;
  const total = reaction.top_total || 1;
  return [
    h("p", { class: "text" }, emphasize(reaction.headline)),
    h(
      "div", { class: "mood", role: "img", "aria-label": "Agree versus disagree among the top comments" },
      ...STANCE_ORDER.filter((name) => counts[name] > 0).map((name) =>
        h("span", { class: name, style: `width:${(counts[name] / total) * 100}%` })),
    ),
    h(
      "p", { class: "legend" },
      ...STANCE_ORDER.filter((name) => counts[name] > 0).map((name) => h("span", { class: name }, `${counts[name]} ${name}`)),
    ),
    h(
      "p", { class: "fineprint" },
      reaction.thread_total
        ? `Based on the top ${reaction.top_total} of ${reaction.thread_total} comments in Hacker News's ranking. Votes are hidden, so rank stands in for them.`
        : `Based on the top ${reaction.top_total} comments in Hacker News's ranking. Votes are hidden.`,
    ),
    deepenPrompt(),
  ];
}

const MIN_COMMENTS_FOR_REACTION = 8; // below this a thread is too small to read a mood or find debates

/**
 * The "Analyze in depth" button: every analyzed story that has not had the deep read yet.
 * Claude reads the whole thread, so it only runs when asked.
 */
function renderDeepenButton(detail) {
  const button = $("deepen-btn");
  const busy = detail.job === "queued" || detail.job === "running";
  const eligible = detail.ready && detail.depth !== "deep" && state.config.can_prepare
    && detail.analyzed_comments >= MIN_COMMENTS_FOR_REACTION;
  button.hidden = !eligible && !(busy && detail.ready);
  button.disabled = busy;
  button.textContent = busy ? "Analyzing…" : "Analyze in depth";
}

/** On a quick reaction: offer the deep read (debates and a take), which Claude only does on request. */
function deepenPrompt() {
  const detail = state.detail;
  if (!detail || detail.depth !== "quick" || !state.config.can_prepare) return null;
  const busy = detail.job === "queued" || detail.job === "running";
  return h(
    "p", { class: "deepen" },
    "A quick read. Debates and a take need Claude to read the thread. ",
    h(
      "button",
      { class: "linkish", disabled: busy, onclick: (event) => { event.stopPropagation(); deepenStory(detail.id); } },
      busy ? "Analyzing…" : "Analyze in depth",
    ),
  );
}

async function deepenStory(id) {
  const apply = (job, detail = null) => {
    if (state.detail?.id !== id) return;
    state.detail.job = job;
    state.detail.job_detail = detail;
    renderPreparePrompt(state.detail);
    renderSegments();
    renderDeepenButton(state.detail);
  };
  apply("queued");
  try {
    await fetch(`/api/stories/${id}/deepen`, { method: "POST" });
  } catch {
    apply("failed", "Could not reach the server");
    return;
  }
  const story = state.stories.find((s) => s.id === id);
  if (story) story.job = "queued"; // so the poll notices when it finishes
  pollJobs();
}

function debateBody(debate) {
  const leansLabel = { a: "Leans A", b: "Leans B", neither: "Neither side clearly wins" }[debate.leans];
  return [
    h("p", { class: "debate-topic" }, debate.topic),
    h(
      "div", { class: "sides" },
      h("p", { class: `side${debate.leans === "a" ? " winner" : ""}` }, h("b", {}, "A"), h("span", {}, emphasize(debate.side_a))),
      h("p", { class: `side${debate.leans === "b" ? " winner" : ""}` }, h("b", {}, "B"), h("span", {}, emphasize(debate.side_b))),
    ),
    h(
      "div", { class: "verdict" },
      h("span", { class: "tag" }, `Model's take · ${debate.confidence} confidence · ${leansLabel}`),
      h("p", {}, emphasize(debate.verdict)),
    ),
    h("p", { class: "people" }, `In the thread: ${debate.authors.join(", ")} · `, h("a", { href: debate.link, target: "_blank", rel: "noopener" }, "Read the thread")),
  ];
}

function audioUrl(order) {
  return `/api/audio/${state.detail.id}/${order}?picks=${state.picks}&voice=${encodeURIComponent(state.voice)}`;
}

function playSegment(index) {
  const segments = state.detail.segments;
  if (index < 0 || index >= segments.length) return;
  if (!state.listening) setListening(true); // any way of starting a clip means the voice was asked for
  state.segment = index;
  const cards = document.querySelectorAll(".segment");
  cards.forEach((el, i) => {
    el.classList.toggle("active", i === index);
    el.classList.toggle("done", i < index);
    el.style.removeProperty("--clip");
  });
  cards[index]?.scrollIntoView({ block: "center", behavior: "smooth" });

  const segment = segments[index];
  setNow("Preparing voice…");
  audio.src = audioUrl(segment.order);
  audio.play().catch(() => setNow("Tap play to start"));
  prefetch(index + 1);
  updateMediaSession(segment);
}

/** Ask the server to speak the next clip now, so it is ready when this one ends. */
function prefetch(index) {
  const next = state.detail.segments[index];
  if (next) fetch(audioUrl(next.order)).catch(() => {});
}

function nextSegment() {
  if (state.segment + 1 < state.detail.segments.length) playSegment(state.segment + 1);
  else nextStory();
}

function nextStory() {
  for (let i = state.storyIndex + 1; i < state.stories.length; i++) {
    if (state.stories[i].ready) return openStory(i, { autoplay: state.listening });
  }
  audio.pause();
  setNow("That was the last story.");
}

function setNow(text) { $("now").textContent = text; }

function setProgress(fraction) {
  $("progress-bar").style.width = `${Math.min(100, Math.max(0, fraction * 100))}%`;
}

function setPlayIcon(playing) {
  $("play-icon").setAttribute("href", playing ? "#i-pause" : "#i-play");
  $("play").setAttribute("aria-label", playing ? "Pause" : "Play");
}

function updateMediaSession(segment) {
  if (!("mediaSession" in navigator)) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: segment.author ? `${KIND_LABEL[segment.kind]} · ${segment.author}` : KIND_LABEL[segment.kind],
    artist: state.detail.title,
    album: "Hackernews Lite",
  });
}

function applySpeed(speed) {
  state.speed = speed;
  audio.defaultPlaybackRate = speed; // survives each new clip; playbackRate alone resets on load
  audio.playbackRate = speed;
  $("speed-btn").textContent = `${speed}x`;
  remember("speed", String(speed));
}

// ---------- wiring ----------

audio.addEventListener("playing", () => {
  const total = state.detail.segments.length;
  setNow(`${KIND_LABEL[state.detail.segments[state.segment].kind]} ${state.segment + 1} of ${total}`);
  setPlayIcon(true);
});
audio.addEventListener("pause", () => setPlayIcon(false));
audio.addEventListener("waiting", () => setNow("Preparing voice…"));
audio.addEventListener("ended", nextSegment);
audio.addEventListener("timeupdate", () => {
  if (state.segment < 0 || !audio.duration) return;
  const within = audio.currentTime / audio.duration;
  document.querySelectorAll(".segment")[state.segment]?.style.setProperty("--clip", `${within * 100}%`);
  setProgress((state.segment + within) / state.detail.segments.length);
});
audio.addEventListener("error", () => {
  if (audio.getAttribute("src")) setNow("Could not make this clip. Is the server still running?");
});

$("play").addEventListener("click", () => {
  if (state.segment < 0) playSegment(0);
  else if (audio.paused) audio.play(); else audio.pause();
});
$("next").addEventListener("click", nextSegment);
$("prev").addEventListener("click", () => playSegment(Math.max(0, state.segment - 1)));
$("skip-story").addEventListener("click", nextStory);
$("back").addEventListener("click", closePlayer);
$("listen-btn").addEventListener("click", startListening);
$("clear-audio").addEventListener("click", clearAudio);
$("refresh-analysis").addEventListener("click", refreshAnalysis);
$("prepare-btn").addEventListener("click", () => prepareStory(state.detail.id));
$("deepen-btn").addEventListener("click", () => deepenStory(state.detail.id));
$("mini-collapse").addEventListener("click", () => applyMiniCollapsed(!state.miniCollapsed));
// Until the voice has been asked for, tapping a card must not start anything. A capturing listener
// on the list stops the tap before the card's own handler sees it. Buttons and links are left alone.
$("segments").addEventListener("click", (event) => {
  // Text only: tapping a card must not start playback. Buttons and links inside it still work.
  if (!state.listening && !event.target.closest("button, a")) event.stopPropagation();
}, true);
$("speed-btn").addEventListener("click", () => {
  const at = SPEEDS.indexOf(state.speed);
  applySpeed(SPEEDS[(at + 1) % SPEEDS.length]);
});

$("voice-toggle").addEventListener("click", () => {
  const panel = $("settings");
  panel.hidden = !panel.hidden;
  $("voice-toggle").setAttribute("aria-expanded", String(!panel.hidden));
});
$("voice").addEventListener("change", (event) => {
  state.voice = event.target.value;
  remember("voice", state.voice);
});

$("theme").addEventListener("change", (event) => {
  const choice = event.target.value;
  if (choice === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = choice;
  remember("theme", choice === "system" ? "" : choice);
});

// Back, Forward, a typed address and a reload all land here: the address decides what is open.
window.addEventListener("popstate", () => (thread.open ? hideThread() : syncFromUrl()));
window.addEventListener("hashchange", syncFromUrl);
$("thread-close").addEventListener("click", closeThread);
$("thread-backdrop").addEventListener("click", closeThread);

// The bar shows the story's title only once the big title at the top of the page has scrolled away.
new IntersectionObserver(
  ([entry]) => $("player").classList.toggle("title-scrolled", !entry.isIntersecting),
  { root: $("player") },
).observe($("player-title"));

document.addEventListener("keydown", (event) => {
  const typing = event.target instanceof Element && event.target.matches("select, input");
  if (thread.open && event.key === "Escape") return closeThread();
  if ($("player").hidden || typing) return;
  if (event.key === "Escape") return closePlayer();
  if (!state.listening) return; // text only: leave Space and the arrows to the browser
  if (event.key === " ") { event.preventDefault(); $("play").click(); }
  else if (event.key === "ArrowRight") $("next").click();
  else if (event.key === "ArrowLeft") $("prev").click();
});

if ("mediaSession" in navigator) {
  navigator.mediaSession.setActionHandler("play", () => audio.play());
  navigator.mediaSession.setActionHandler("pause", () => audio.pause());
  navigator.mediaSession.setActionHandler("nexttrack", nextSegment);
  navigator.mediaSession.setActionHandler("previoustrack", () => $("prev").click());
}

async function init() {
  state.config = await api("/api/config");
  state.voice = recall("voice", state.config.default_voice);
  if (!state.config.voices.includes(state.voice)) state.voice = state.config.default_voice;
  $("voice").replaceChildren(
    ...state.config.voices.map((v) => h("option", { value: v, selected: v === state.voice }, v)),
  );
  $("theme").value = document.documentElement.dataset.theme ?? "system";
  applySpeed(SPEEDS.includes(state.speed) ? state.speed : 1.5);
  applyMiniCollapsed(state.miniCollapsed);
  const params = new URLSearchParams(location.search);
  const requested = params.get("feed"); // for example /?feed=week
  if (requested) state.feed = requested;
  const daily = state.config.daily;
  const wantedMode = params.get("mode") || state.mode; // for example /?feed=daily&mode=top20
  state.mode = daily.modes.some((m) => m.id === wantedMode) ? wantedMode : daily.default_mode;
  const known = [...state.config.feeds, ...(state.config.library ?? [])];
  if (!known.some((feed) => feed.id === state.feed)) state.feed = state.config.default_feed;
  renderFeeds();
  // A reload or shared link such as #/story/123 opens that story as text. It does not need the
  // list, so the two load side by side and the story never waits for the list.
  const listLoaded = loadStories();
  await syncFromUrl();
  await listLoaded;
}

init().catch((error) => {
  console.error(error); // so a failure is visible in the browser console, not just a short message
  $("status").textContent = `Could not load: ${error.message}`;
});
