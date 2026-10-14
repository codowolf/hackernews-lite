# Hackernews Lite

A local reader for Hacker News. For each story it summarizes the article, picks the comments worth reading, shows how the thread reacted, and reads it all aloud. It runs on your own machine as a small web app made for a phone-sized screen.

## What it does

| Feature | How it works |
|---|---|
| Story lists | Daily (the best 5, 10 or 20 stories of each day, like hckrnews), Top, 24h, 48h, Week and Best. All are set in `config.toml`. |
| Hot stories | A story's upvote or comment number turns orange when it is climbing fast or very busy (see [Hot stories](#hot-stories)). |
| Article summary | A short gist and key points from the linked article. |
| Comment picks | The comments worth your time, chosen for first-hand experience, corrections and substance rather than votes (which HN hides). |
| Thread reaction | How many top comments agree, disagree or are mixed, with the best of each side. "Analyze in depth" adds the main debates and a take. |
| Full threads | Tap a comment to open its whole conversation, root to leaf, with working links. |
| Listening | Each card can be read aloud by a local voice. Clips are made on first play and kept. |
| Housekeeping | Archive or ignore stories, shown in their own tabs. A monthly budget stops paid calls. |

Stories are analyzed when you open them, and the first few of each list are prepared in the background. The top comments are readable at once, before any model has run.

## Quick start

You need [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer.

```
git clone https://github.com/codowolf/hackernews-lite.git
cd hackernews-lite
uv sync
cp .env.example .env     # then add your Jev key (optional, see below)
uv run hackernews-lite serve
```

Open http://localhost:8731. Add `--lan` to open it from a phone on the same wifi. The command prints the address. There is no login, so only use `--lan` on a network you trust.

The first time you play a clip, the app downloads the voice model (about 340 MB) into `models/`. It happens once.

## Claude and Jev

The app uses two different models for two different jobs. This keeps Claude usage low, which matters most when it runs on a subscription.

| Job | Who does it | Cost per story |
|---|---|---|
| Article gist and key points | Claude (Haiku) | about 5,000 tokens in, 400 out |
| Bold highlights on the picked quotes | Claude (Haiku), sees only those quotes | about 3,000 in, 700 out |
| Scoring every comment to pick the best | Jev | about $0.002 per story, no Claude tokens |
| Agree / disagree label on the top 15 comments | Jev | included in the above |
| Debates and the model's take | Claude (Haiku), **only when you press "Analyze in depth"** | about 8,000 in, 7,000 out |

That comes to about 8,000 Claude tokens in and 1,100 out per story. See [Token use](#token-use) for the measurements. During development, having Claude pick the comments itself took about 56,000 in and 9,000 out per story, which is why Jev does it.

**Claude** writes the prose: summaries, highlights and the in-depth take.
- By default it runs through [Claude Code](https://claude.com/claude-code) in headless mode (`claude -p`), on your Claude subscription. There is no API key and no per-call bill, but it counts against your usage limits. Install Claude Code and log in once.
- To pay per call instead, set `provider = "anthropic"` under `[llm]` in `config.toml` and add `ANTHROPIC_API_KEY` to `.env`.

**Jev** is [TypeSafe](https://docs.typesafe.ai)'s model for typed questions: you ask a yes/no question about a piece of text and get a probability back. The app asks a few such questions about every comment ("does it describe the author's own experience?", "does it correct the article?", "is it hostile?"). It combines the answers with weights you can edit under `[curation.weights]`. Jev is fast and very cheap, so the full thread can be scored without spending Claude tokens.
- Put the key in `.env` as `TYPESAFE_API_KEY=...` (or export it). `.env` is not checked in.
- **Without a Jev key the app still works.** Summaries and the voice are unaffected. The comment picks and the thread reaction are off, and the top comments are shown in Hacker News's own order. Nothing falls back to Claude silently, because that would use much more of your allowance. To have Claude pick comments instead, set `methods = ["llm"]` under `[curation]`. To have Claude do the reaction, set `stance = "deep"`.

## Token use

Measured on 14 October 2026 with the default settings: 12 stories from the Daily list, each prepared from scratch through the web app, then 5 of them analyzed in depth. Claude was Haiku 5.5 at low effort through `claude -p`. Jev was `jev-latest`.

**One story, from opening it to having it ready**

| Step | Tokens in (median) | Tokens out (median) | Notes |
|---|---|---|---|
| Article summary (Claude) | 5,100 | 400 | Range 2,600 to 8,700 in. Two summaries ran longer, 1,700 to 1,900 out. |
| Highlights (Claude) | 2,900 | 700 | Sees only the picked quotes. |
| **Claude total** | **about 8,000** | **about 1,100** | Mean 8,100 in and 1,300 out, leaving out one startup call (below). |
| Comment scoring (Jev) | 39,000 | none | 33 to 92 comments per story. Output is free. |
| Reaction labels (Jev) | 8,600 | none | Always the top 15 comments. |
| **Jev cost** | | | **$0.002 per story** on average, $0.0026 at most. |
| Time | | | 15 seconds median, 27 at most. |

**"Analyze in depth" (only when you press it)**

| | Tokens in | Tokens out | Time |
|---|---|---|---|
| Debates and the model's take (Claude) | about 8,200 | about 7,000 (5,500 to 8,800) | 24 to 39 seconds |

One call each, and the output is the larger part. This is the most expensive thing the app does, and it only runs on request.

**What it means for you**

| | Per story | 5 stories a day for a month (150) | 30 a day for a month (900) |
|---|---|---|---|
| Claude tokens in | 8,000 | 1.2 million | 7.2 million |
| Claude tokens out | 1,100 | 165,000 | 1 million |
| Jev | $0.002 | $0.30 | $1.80 |
| Same Claude usage at API prices | $0.0014 | $0.20 | $1.20 |

The last row prices the tokens at the Haiku rates in `config.toml` ($0.10 per million in, $0.50 per million out). Check them against Anthropic's current prices. An in-depth analysis comes to about $0.004 each at those rates.

- **Subscription (default).** The Claude calls cost nothing per call, so only Jev counts toward `[budget]`. What you spend is part of your plan's usage limit, and I did not measure what share of it that is. It depends on your plan.
- **API (`provider = "anthropic"`).** You pay the last row above, and the monthly budget stops paid calls when it is reached.
- **What makes a story cost more.** Longer articles raise the summary's input. More comments raise Jev's input (Jev is cheap, so this matters little). The number of comments does **not** change Claude's usage, because Claude only reads the article and the picked quotes, never the thread, unless you ask for the in-depth analysis.
- **Startup cost.** The very first Claude call after starting the app used 55,000 input tokens, against a typical 3,000 for that step. It happened once in 27 Claude calls, and I did not confirm the cause. With `claude -p`, input counts include cached context that Claude Code adds, so individual calls can vary. Treat the averages above as typical, not exact.
- **No article.** Two of the 12 stories had no readable article, so they skipped the summary and used only the highlight call (about 3,000 in).

**Using less**

| To do this | Change |
|---|---|
| Stop preparing stories you did not open | `list_top = 0` under `[prepare]` |
| Skip the highlights call | Not an option yet. It is a small share (about a third of Claude's input). |
| Stop opening a story from starting its analysis | `on_open = false` under `[prepare]`. Stories then show their comments until you ask for an analysis. |
| Fetch stories without any model use | `uv run hackernews-lite run --no-llm` |

Your own numbers are in `data/cost_log.jsonl`: one line per call, with the model, tokens (or Jev comments), cost and story id. `uv run hackernews-lite cost` shows the month's total.

## Requirements

| Needed | For |
|---|---|
| Python 3.12+ and `uv` | Everything |
| Claude Code, logged in (or an Anthropic API key) | Summaries and highlights |
| A TypeSafe key | Comment picks and the reaction (optional) |
| Google Chrome (optional) | Articles on sites that block plain downloads. The app retries once in headless Chrome. |
| Node (optional) | Only for the JavaScript tests |

Developed and checked on macOS in desktop Chrome at phone width. Linux should work. It has not been tried on Windows or on a real phone.

## Commands

| Command | What it does |
|---|---|
| `uv run hackernews-lite serve` | Start the web app. Options: `--port`, `--lan`. |
| `uv run hackernews-lite run --no-llm --top 5` | Fetch stories, comments and articles for the default list. Spends nothing. |
| `uv run hackernews-lite run --top 5` | The same, plus summaries and comment picks. |
| `uv run hackernews-lite run --feed week` | Use another list: `daily`, `top`, `24h`, `48h`, `week` or `best`. |
| `uv run hackernews-lite run --story 12345` | One story by id. Add `--force` to redo cached steps. |
| `uv run hackernews-lite run --story 12345 --picks both` | Run both comment pickers (`llm`, `jev` or `both`) to compare them. |
| `uv run hackernews-lite show 12345` | Print what was prepared, as the segments that would be read aloud. |
| `uv run hackernews-lite thumbs` | Fetch preview images for saved stories. |
| `uv run hackernews-lite cost` | This month's paid spend against the budget. |
| `uv run pytest`, `uv run ruff check .`, `uv run mypy` | Tests, lint, types. |

## Configuration

Everything tunable is in `config.toml`. Secrets are not: they live in `.env`.

| Section | Controls |
|---|---|
| `[feeds]`, `[daily]` | The story lists, and how much of each day the Daily list shows |
| `[llm]` | Claude provider, and the model, effort and token limit for each job |
| `[curation]`, `[curation.weights]` | Which comment picker runs, how many picks, and how much each signal counts |
| `[jev]` | The Jev model and how many requests run at once |
| `[prepare]` | Whether opening a story analyzes it, and how many stories are prepared ahead |
| `[budget]`, `[pricing.*]` | The monthly cap and the per-model prices used to count spend |
| `[speech]` | The voice |
| `[trending]` | When a story's upvote or comment count turns orange (see [Hot stories](#hot-stories)) |

Prompts are in `prompts/`. They are versioned: raise the `version` in a prompt's header after editing it, and the affected stories are redone.

## Hot stories

In the story lists, a story's upvote number and its comment number each turn orange (and bold) when that number is running hot. Hovering shows "Hot: many points" or "Hot: many comments". The two numbers are judged separately, so a story can have one, both or neither.

| Number | Turns orange when | Default |
|---|---|---|
| Upvotes | Its average upvotes per hour since it was posted reaches the bar | 30 per hour |
| Comments | Its comment count reaches the bar | 500 comments |

- **Minimum.** A story with fewer than 100 upvotes is never orange, however fast it is climbing. This keeps a story with 60 points posted ten minutes ago from looking hot.
- **Age.** The rate is points divided by hours since posting, measured to when the points were read from Hacker News. Anything under an hour old counts as an hour old, so a brand-new story is not inflated.
- **When it updates.** The numbers come from Hacker News each time a list is refreshed (at most hourly by default, see `max_age_minutes` under `[feeds]`). A story's colour can change on a refresh, but not live while the page is open.
- **Fixed bars.** The thresholds are absolute, not relative to the day, so a busy news day shows more orange than a quiet one. A story that scored high over several days can have a low average rate and stay uncoloured.

Change the bars under `[trending]` in `config.toml`:

```toml
[trending]
min_points_per_hour = 30  # upvotes per hour since posting
min_comments = 500        # comments
min_points = 100          # never below this many upvotes
```

The rule is a pure function in `src/hackernews_lite/domain/trending.py`, with tests in `tests/unit/test_trending.py`.

## How it works

Each story passes through stages that save their output to `data/stories/<id>/`. A stage stores a hash of its input, so unchanged input is never paid for twice. Editing a weight or a prompt only redoes what depends on it.

```
Hacker News (Algolia + Firebase)         article page (plain, or Chrome if blocked)
            │                                        │
        story + comments                    clean article text
            │                                        │
   Jev: score + label every comment        Claude: gist, key points
            │                                        │
   picks, reaction ──► Claude: bold highlights ◄─────┘
                                  │
                   segments ──► web app ──► Kokoro voice (on demand, cached)
```

Vendors sit behind small interfaces (`LlmClient`, `CommentScorer`, `StanceLabeler`, `ArticleFetcher`, `TtsProvider`). Only `pipeline.py` and `cli.py` know which ones are in use, so swapping one means adding a class.

| Path | What it is |
|---|---|
| `src/hackernews_lite/domain/` | Pure models and rules (threads, ranking, daily list, text). No I/O. |
| `src/hackernews_lite/sources/` | Hacker News, article fetching (plain and Chrome) and preview images |
| `src/hackernews_lite/prep/` | The stages: article, comment picks, reaction, highlights, thumbnails, segments |
| `src/hackernews_lite/llm/` | The Claude seam: `claude -p`, the Anthropic API, and the budget wrapper |
| `src/hackernews_lite/scoring/` | The Jev seam: comment scoring and labeling |
| `src/hackernews_lite/speech/` | The Kokoro voice and the audio cache |
| `src/hackernews_lite/storage/` | On-disk layout, the cost log and your archive/ignore state |
| `src/hackernews_lite/web/` | The FastAPI app, the background job queue and the front end in `static/` (plain JavaScript, no build step) |
| `prompts/` | Editable prompts |
| `tests/` | Python tests, plus JavaScript tests in `tests/js/` |

Local state is in `data/` (stories, audio, cost log, your archive list) and `models/` (the voice). Neither is checked in. Delete a story's folder to redo it from scratch.

## Not supported yet

These links are skipped. The story is marked "no article" and only its comments are offered.

| Case | Why |
|---|---|
| Paywalled pages | Needs a login or a reader proxy |
| PDFs | Needs text extraction and page limits |
| Video and social posts | No article text |
| GitHub repos | Not an article. The README could be used. |

## Credits

| What | Used for |
|---|---|
| [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) (open weights) | The local voice, downloaded on first play |
| [Algolia HN Search](https://hn.algolia.com/api) and the Hacker News API | Stories and comments |
| [trafilatura](https://trafilatura.readthedocs.io) | Pulling clean text out of article pages |

## License

MIT. See [LICENSE](LICENSE).
