# Implementation Plan: rundot 0.8.0

Status: draft (2026-11-15)
Spec: [specs/SPEC-0.8.0.md](../specs/SPEC-0.8.0.md)
Tasks: [tasks/todo-0.8.0.md](todo-0.8.0.md)

## Overview

Teach the runner to pause before each run block, and have `rundot.runbook` render a whole runbook as HTML. On top of that, build `rundot/web/`: a sanitiser, a run hub that drives one run at a time, and an HTTP server. Then add the page's CSS, JS and fonts, and wire `rundot web` and `rundot run --web` into the CLI.

## Architecture decisions

- **The runner learns one thing:** `run(..., block_by_block=True)`, which calls `prompter.confirm()` before each run block. `BlockStarted`, `Confirm` and `InputBlock` already carry their block's `line`, and `jsonlines` already writes it (0.7.0). It knows nothing about the web.
- **Only `rundot.runbook` parses markdown,** as the `runbook-owns-markdown` contract says. It gains `render_html(text) -> str`, which renders the body with markdown-it (`html=True`, tables on) and tags each rundot fence as `<div class="rd-block" data-kind="run" data-line="12" data-lang="sh">`. Other fences render as plain `<pre>`. Headings that start steps get `data-step`. The web layer fills in buttons and output around these hooks, and never reads markdown itself.
- **`rundot/web/` is a package with four modules**, all free of click and rich, under the `cli-owns-terminal` contract:
  - `sanitise.py`: a stdlib `html.parser` allowlist sanitiser, a pure function from HTML to HTML.
  - `hub.py`: `WebPrompter` and `RunHub`. The hub owns the single run: its worker thread, a replay buffer of JSON events, and the one prompt being waited on. `WebPrompter.ask` and `.confirm` put a prompt on the hub and block on a `queue.Queue` until `hub.answer()` is called. A wrong answer raises `WrongAnswer`, which the server turns into 409.
  - `server.py`: `ThreadingHTTPServer` and its handler. It owns routes, the token cookie, `Host` and `Origin` checks, CSP, SSE, static files and runbook-directory files.
  - `page.py`: builds the book page and the library index from small string templates in `rundot/web/templates/`. There's no template engine: placeholders are filled with `html.escape`d values.
- **The CLI wires runs; the web layer only starts them.** The CLI already owns transcripts, run state and resume detection. The server is given two callables built by the CLI:
  - `start_run(prompter, sink, resume)`, which wraps today's `run_runbook` wiring: transcript, `StateRecorder` and saved inputs.
  - `resume_info(path)`, which returns the banner text, or `None` when there's nothing to resume.
  
  So `rundot.web` imports `runner`, `runbook`, `jsonlines` and `library`, but never `transcript` or `state`, and the transcript and state code paths stay single.
- **Layers:** a new layer for `rundot.web` sits between `rundot.cli` and `rundot.transcript | rundot.state | rundot.jsonlines`, because `web` uses `jsonlines.to_json` and modules in one layer must be independent.
- **The run's thread, not the request thread, runs blocks.** The runner thread is a daemon. Ctrl-C in the main thread interrupts the running backend the same way `test_interrupt.py` already covers, the hub records `interrupted`, and then the server shuts down.
- **One event schema.** SSE `data:` lines are exactly `jsonlines.to_json` output, plus a few hub-only events (`prompt`, `busy`, `resume`). On connect, the server replays the buffer before streaming live.
- **Static files ship in the package** under `rundot/web/static/`: `rundot.css`, `rundot.js`, and `fonts/` with Geist, Geist Mono and Instrument Serif woff2 and `OFL.txt`. Hatch includes package data by default, and a test checks that they're in the built wheel.
- **The JS is one file with no build step,** about 200 lines: an `EventSource`, a `fetch` for answers, and DOM updates keyed by `data-line`. It's checked by the manual checklist below, not by coverage.
- No new runtime dependencies. `http.server`, `html.parser`, `secrets`, `queue`, `threading` and `webbrowser` are enough.

## Task list

Full task bodies are in [todo-0.8.0.md](todo-0.8.0.md).

Tasks 1–2 change the runner and runbook, 3–5 build `rundot/web/` with no user-visible change, 6 adds the page's look, 7 wires in the CLI, and 8 covers docs. Each task leaves `main` releasable.

1. Runner: `block_by_block`, and `line` on events
2. Runbook: render the whole document as HTML
3. Sanitiser
4. Run hub and `WebPrompter`
5. HTTP server and security
6. Page, styles, script and fonts
7. `rundot web` and `rundot run --web`
8. Docs and changelog

## Manual checklist

Run before tasks 6 and 7 are ticked, and again before release, in a desktop browser and a phone-width window. Uses `examples/hello.md`, `tests/runbooks/all_features.md` and a library with three books.

- [ ] `rundot web examples/hello.md` opens the browser on the page; the URL has no `?t=` after the first load
- [ ] The Library link is at the top, and the inputs form is at the very top, under the title and description
- [ ] A pattern mismatch shows its message under the field; a secret field shows dots; Start folds the form into one line with `****`
- [ ] Only the next ▶ is enabled; output streams in under its block; stderr lines are marked red
- [ ] A confirm shows Done and Stop; Stop ends the run as aborted
- [ ] An `input` block shows its field in place, and is checked against `pattern` and `choices`
- [ ] A failing block shows Retry step and Start over; Retry reruns from the start of the step
- [ ] Reloading mid-run replays everything so far; a second tab is read-only with the busy banner
- [ ] Stop a run in the browser, then `rundot run` the same book: it offers to resume, and the other way round
- [ ] `rundot web` with no argument lists the library; a book opens; a running book shows `running`
- [ ] A runbook with `<details>`, `<img width>` and a `<script>` renders the first two and drops the third
- [ ] With the network off, fonts and styles still load
- [ ] Ctrl-C in the terminal mid-block stops the block, saves `interrupted`, and exits

## Risks

- **A runner thread stuck waiting on a prompt when the server stops.** The hub puts a "stop" answer on the queue at shutdown, and `WebPrompter` raises `KeyboardInterrupt` for it, so the runner finishes as `interrupted`. Tested in task 4.
- **The sanitiser misses something.** CSP `default-src 'self'` is the backstop, and the tests include the usual XSS vectors: `javascript:`, `on*` attributes, `<svg><script>`, `<img src=x onerror>`, mixed-case tags and entities in URLs.
- **SSE connections holding server threads.** `ThreadingHTTPServer` uses daemon threads, and each SSE handler loops on a queue with a timeout, so it notices the server stopping.
- **Font files growing the wheel.** About 250 KB for the subsets used. Checked in task 6, with Latin subsets only.
- **The JS breaking without a test noticing.** The manual checklist, and keeping the JS small and keyed only by `data-line`.
