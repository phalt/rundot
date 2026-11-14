# Spec: rundot 0.8.0

Status: draft (2026-11-14)
Version target: 0.8.0

This builds on [SPEC-0.7.0.md](SPEC-0.7.0.md), [SPEC-0.6.0.md](SPEC-0.6.0.md), [SPEC-0.3.0.md](SPEC-0.3.0.md), [SPEC-0.2.0.md](SPEC-0.2.0.md) and [SPEC.md](SPEC.md). Anything not mentioned here is unchanged.

## Objective

`rundot read` opens a runbook in the browser as the whole rendered markdown document, prose included, and runs it there one block at a time. Each run block has a ▶ button, and its output streams in underneath it.

This is a first-class runner, not a preview. It drives the same `Runner` as `rundot run`, so inputs, secret masking, confirms, transcripts and resume behave the same. Only the front end is different.

The runbook format doesn't change.

**User:** people who'd rather not live in a terminal, such as a newcomer doing dev setup or an onboarding guide. Also Paul, when demoing rundot.

### Why add this

A runbook is documentation that runs. The terminal shows the running half and hides the documentation half: `rundot run` prints docstrings, but the prose, links, tables and images between blocks never appear. Someone following an onboarding guide needs that prose most. Today they read the doc on GitHub in one window and run it in another.

`rundot read` puts both halves on one page. The case for it:

- **Reading while running.** The doc and its output sit together. The reader sees why a step exists right next to what it printed.
- **A lower bar for other people.** Click ▶, fill in a form, click Done. No flags, no `[r]esume / [s]tart over / [q]uit`.
- **Demos.** A rendered page with live output explains "markdown that runs" faster than a terminal recording.
- **It's cheap to build.** `run()` already takes a `sink` and a `Prompter`. The browser is one new sink, one new prompter and a small HTTP server. `markdown-it-py` already renders HTML. Nothing new is needed at runtime.

The case against, to weigh:

- It's a second front end to maintain, with HTML, CSS and JS in the package, which rundot has never had.
- A local server that runs code is a security surface (see [Security](#security)).
- It could pull rundot towards being a notebook. Strict order (decision 3) is the line that stops that.

**This is worth shipping if** (see [Success criteria](#success-criteria)):

1. someone other than Paul does a real runbook end to end in `rundot read` without help, such as a dev-setup guide or `examples/hello.md`, and
2. Paul would use it in place of `docs/demo.svg` in a talk or the README.

### User stories

- As a newcomer, I run `rundot read SETUP.md` and my browser opens on the guide. I read it, fill in the inputs, and click ▶ on each block in turn, seeing its output under it.
- As a newcomer, when a block fails, I read the error under it, fix the problem, and click **Retry step** without going back to the terminal.
- As a newcomer, a `confirm` shows up as a callout with **Done** and **Stop**, so I know exactly what I have to do by hand.
- As Paul, `rundot read` with no runbook opens my library as an index page, and I click a book to open it.
- As Paul, a run in the browser leaves the same transcript and resume state as `rundot run`, so I can start in the browser and finish in the terminal, or the other way round.

## Decisions

1. **The command is `rundot read [RUNBOOK]`.** `RUNBOOK` is resolved the way `rundot run` resolves it: a path, or a name in the library. With no argument it opens the library index (see [The library index](#the-library-index)). Paul chose the name `read` over `web`, `run --web`, `serve` and `open` (2026-11-14): it says what the page is for, reading the runbook while it runs. As with every command, the name wins over a book called `read.md`, which runs with `rundot run read`.
2. **It's the same Runner.** The server calls `runner.run()` on a worker thread with a `WebPrompter` and a sink that sends events to the page. No block runs except through `run()`.
3. **Strict order, one block per click.** Only the next run block's ▶ is enabled. Earlier blocks are done, and later ones are locked. Paul chose this over free-form running (2026-11-14). Docstring blocks just render, and confirm and input blocks become controls in the page (decision 6).
4. **Blocks run where `rundot read` was started,** the same as `rundot run` (0.7.0), with `RUNDOT_RUNBOOK_DIR` set.
5. **The whole document is rendered,** with markdown-it-py: frontmatter `title` as the heading, `description` under it, then every heading, paragraph, list, table, link, image and code block. Fenced blocks that aren't `run`, `docstring`, `confirm` or `input` render as plain code with no button.
6. **Prompts are inline.** Frontmatter inputs are a form at the top with a **Start** button, and Start is the `Start? [y/N]` answer. Secrets are password fields. An `input` block becomes the same kind of field where it sits in the doc. A `confirm` is a callout with **Done** (yes) and **Stop** (no, which aborts).
7. **Failure keeps the server up.** A failed block, a Stop or an abort ends the run as `rundot run` does, and state is saved as in 0.7.0. The page then offers **Retry step**, which resumes from the start of the step (0.7.0 decision 1), and **Start over**, which is the same as `--restart`.
8. **Resume works across front ends.** Opening a runbook that has saved state shows a banner with **Resume** and **Start over**, the browser's version of `[r]esume / [s]tart over`. Any 0.7.0 changed-runbook warning is shown in the banner too.
9. **Transcripts are unchanged.** If `output_path` is set, the same transcript is written.
10. **One run at a time per server,** across every book opened from the index too. Paul chose this (2026-11-14). A second tab, or another book opened while a run is going, shows a banner naming the running book and can't click. Reloading a tab replays the run's events so far, so nothing is lost.
11. **The server lives until Ctrl-C.** When a run finishes, the page offers **Run again**, and **Back to library** when it was opened from the index. Ctrl-C while a block is running interrupts it the way Ctrl-C does under `rundot run`: state is saved as `interrupted`, then the server exits.
12. **The browser opens itself** with `webbrowser.open`. `--no-browser` only prints the URL. `--port` picks a port, and by default the OS picks a free one.
13. **No new runtime dependencies.** The server is the stdlib's `http.server` (`ThreadingHTTPServer`). Output streams over Server-Sent Events. The page is plain HTML with one small script.
14. **It works offline.** The CSS, JS and fonts ship inside the package as static files, with no CDN.
15. **`--dry`, `--no-input`, `--json` and `--step` don't apply** to `rundot read`. It's stepped by nature, it always has a person, and it isn't for agents.
16. **It looks like rundot.dev** (see [Styles](#styles)). Paul chose this (2026-11-14).
17. **Raw HTML renders the way GitHub renders it:** allowed tags and attributes are kept and everything else is stripped (see [Security](#security)). Paul chose this over escaping it (2026-11-14), so a book that uses `<details>` or `<img width>` looks as it does on GitHub.
18. **The URL carries a one-time token** (see [Security](#security)). Paul chose this (2026-11-14).

## The page

```
┌──────────────────────────────────────────────────────┐
│ Say hello                                            │
│ Who to say hello to                                  │
│ NAME [ Paul              ]            [ Start ]      │
├──────────────────────────────────────────────────────┤
│ A runbook is just markdown. rundot runs the blocks…  │
│                                                      │
│ ## Greet                                       1/2 ✓ │
│ ┌ sh ───────────────────────────────────── [ ▶ ] ┐   │
│ │ echo "Hello, Paul!"                            │   │
│ ├ output ────────────────────────────────────────┤   │
│ │ Hello, Paul!                                   │   │
│ └ ✓ exit 0 · 0.0s ───────────────────────────────┘   │
│                                                      │
│ ## Count the letters                           2/2   │
│ ┌ python ───────────────────────────────── [ ▶ ] ┐   │
│ │ import os …                                    │   │
│ └────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────┘
```

- Each step heading shows `n/total` and a ✓ or ✗ once the step is done, matching the terminal's step rule (0.6.0 decision 3).
- Code is always shown. Before Start it's shown as written, with `<NAME>`. After Start it's shown with plain inputs filled in. Secrets are never filled in, as in the terminal.
- Output keeps the 0.6.0 meanings: stderr lines are marked red, and the block closes with `✓ exit 0 · 0.0s`, `✗ exit 3 · 0.0s` or `↷ skipped`. Blocks skipped because of a resume show `done in an earlier run`.
- Colour follows 0.6.0 decision 1, in rundot.dev's palette (see [Styles](#styles)).
- The page scrolls to the active block as the run moves on.

## Styles

The page uses rundot.dev's own `style.css` values, so it looks like the site. The site isn't in this repo; the values below are copied from it (2026-11-14), as `docs/stylesheets/extra.css` already does for the docs.

| Token | Value | Used for |
|---|---|---|
| `--bg` / `--bg-2` | `#08090b` / `#0e1013` | Page, and the header band |
| `--panel` | `#121418` | Code blocks, output, callouts, form fields |
| `--line` / `--line-2` | `#22252b` / `#2d3138` | Borders and rules |
| `--fg` / `--fg-2` / `--fg-3` | `#eceef1` / `#a3a9b3` / `#6b717c` | Text, secondary text, dim (times, frames) |
| `--accent` / `--accent-2` | `#34d399` / `#6ee7b7` | Success, the enabled ▶, links, the dot in the run.dot wordmark |
| `--blue` | `#6ea8ff` | Structure: step headings' `n/total`, the active block's border |
| `--amber` | `#f5c451` | Confirm callouts and the resume banner |
| `--red` | `#f87171` | Failure only. rundot.dev has no red, so this is the one addition |
| `--radius` | `14px` (10px for blocks) | Corners |

- **Dark only,** like rundot.dev. There's no light theme.
- **Fonts:** Geist for text, Geist Mono for code and output, and Instrument Serif italic for `em`, as on the site. rundot.dev loads them from Google Fonts. Here the woff2 files ship in the package with their OFL licence, to keep decision 14, falling back to the system stacks the site already lists.
- **Header:** the `run.dot` wordmark (700, `letter-spacing: -.04em`, mint dot), then the book's title.
- Pills, buttons and borders follow the site's `.pill`, `.nav a` and `.term` rules.

## The library index

`rundot read` with no argument lists the library (`rundot library`) as a page: each book's title and description, with a link to `/book/<name>`. A book opened from the index runs where `rundot read` was started, the same as `rundot <name>`. An empty or missing library shows the same message as `rundot library`.

## Runner changes

- `run()` gains `block_by_block: bool = False`. When it's set, `prompter.confirm()` is called before each run block. Any answer but y stops the run as `aborted`, the same as `--step` does per step. The web prompter answers it when ▶ is clicked.
- `BlockStarted`, `Confirm` and `InputBlock` events carry the block's `line`, so the page can find the rendered block to update. The JSON lines output (0.7.0) gains a `line` field. This is additive.
- Nothing else in the Runner changes.

## Server

| Route | Method | What it does |
|---|---|---|
| `/` | GET | The library index, or redirects to the runbook when one was given |
| `/book/<name>` | GET | The rendered runbook page |
| `/book/<name>/events` | GET | SSE stream of the run's events, from the start of the run (decision 10) |
| `/book/<name>/answer` | POST | The answer to the prompt the Runner is waiting on: inputs, Start, ▶, Done or Stop |
| `/book/<name>/run` | POST | Start, resume or start over |
| `/static/…` | GET | The packaged CSS, JS and fonts |
| `/book/<name>/file/…` | GET | Relative images and files from the runbook's directory |

- Events on the wire are the same objects as `--json` lines (`jsonlines.to_json`), so there's one event schema.
- `WebPrompter.ask()` and `.confirm()` block the runner thread on a queue until `/answer` posts. An answer that doesn't match the prompt being waited on is refused with 409.
- An input the page sends is checked by `inputs.collect` the same way as one typed in the terminal, and a `pattern` or `choices` failure comes back to the field as an error message.

## Security

- Binds to `127.0.0.1` only. There's no option to bind elsewhere.
- Requests whose `Host` isn't `127.0.0.1:<port>` or `localhost:<port>` are refused, which blocks DNS rebinding.
- POSTs whose `Origin` isn't the server's own are refused, so another site open in the browser can't click ▶.
- **A one-time token.** The server makes a random token (`secrets.token_urlsafe(32)`) at start and opens `http://127.0.0.1:<port>/?t=<token>`. The first request with the right token gets an `HttpOnly`, `SameSite=Strict` cookie and a redirect to the same URL without `?t=`. Every request after that needs the cookie, and anything without it gets 403. This stops other local processes and users, which the `Host` and `Origin` checks don't. `--no-browser` prints the URL with the token.
- **Raw HTML is sanitised, GitHub style.** markdown-it renders it (`html=True`), then a sanitiser built on the stdlib's `html.parser` keeps only an allowlist of tags and attributes modelled on GitHub's: `details`, `summary`, `img`, `kbd`, `sub`, `sup`, `br`, `div`/`p` with `align`, `img` with `src`, `alt`, `width` and `height`, and so on. Everything else is stripped, including `script`, `style`, `iframe`, `form`, every `on*` attribute, and any URL whose scheme isn't `http`, `https`, `mailto` or relative. The page also sends `Content-Security-Policy: default-src 'self'`, so markup that slips through still can't run a script.
- Relative images are served from the runbook's directory only, through `/book/<name>/file/…`, and paths that leave it are refused.
- Secret values never reach the browser after they're submitted: output is masked before it's sent, as for the terminal and the transcript.

## Commands (CLI)

```
rundot read [RUNBOOK] [--port N] [--no-browser]
```

On start it prints `rundot read: http://127.0.0.1:<port>/` and `Ctrl-C to stop`.

## Documentation

- `docs/cli.md`: the `read` command and its flags.
- `docs/index.md` and `README.md`: a short "Run it in your browser" section with a screenshot.
- `CHANGELOG.md`: 0.8.0 entry.

## Testing strategy

- Red-green slices with 100% line and branch coverage of the Python, as usual.
- `WebPrompter`: tested with queues and threads, with no server.
- `run(block_by_block=True)`: unit tests next to the existing `step_by_step` ones.
- Rendering: the HTML for `tests/runbooks` fixtures, including every block kind, raw HTML being sanitised, and non-rundot fences getting no button.
- Server: a real `ThreadingHTTPServer` on port 0 in a thread, driven with `http.client`: routes, SSE replay, the 409 on a wrong answer, and the `Host` and `Origin` refusals.
- `webbrowser.open` is patched out in tests.
- Sanitiser: allowed tags and attributes survive; `script`, `on*`, `javascript:` and paths outside the runbook's directory don't.
- Token: no cookie gets 403, the right `?t=` sets the cookie, and a wrong one doesn't.
- The JS isn't covered by coverage. It's kept small, and checked by hand with a **manual checklist** in the plan, not a browser test in CI. Paul chose this (2026-11-14).

## Out of scope for 0.8.0

- Running blocks out of order, or rerunning a single block on its own.
- Serving to other machines, or several people driving one run.
- Editing the runbook in the page, or live reload when the file changes.
- `--dry` in the browser.
- Windows (unchanged).

## Success criteria

- [ ] `rundot read examples/hello.md` opens the rendered doc, and Start then ▶ ▶ runs it with output under each block
- [ ] Only the next run block can be clicked; prose, tables and non-rundot fences render with no button
- [ ] A `confirm` shows Done and Stop, and an `input` block shows a field validated against its `pattern` and `choices`
- [ ] A failed block offers Retry step, which reruns from the start of that step without restarting the server
- [ ] A run stopped in the browser resumes with `rundot run`, and a run stopped in the terminal resumes in the browser
- [ ] Transcripts from `rundot read` match `rundot run` for the same runbook and inputs
- [ ] No secret value is ever sent to the browser
- [ ] Requests with a foreign `Host` or `Origin`, or without the token's cookie, are refused
- [ ] Raw HTML renders as on GitHub, and nothing in a runbook can run a script in the page
- [ ] The page uses rundot.dev's colours and fonts, offline
- [ ] The manual checklist in the plan passes
- [ ] `rundot read` with no argument lists the library, and a book opens from it
- [ ] Works with no network access, and no new runtime dependencies
- [ ] **Proof:** someone other than Paul runs a real runbook end to end in `rundot read` without help
- [ ] **Proof:** Paul is happy to put a `rundot read` screenshot or recording in the README
- [ ] Docs and changelog updated as listed

## Open questions

None. Answered by Paul (2026-11-14): the URL has a token, raw HTML renders as on GitHub, JS is checked with a manual checklist, one run at a time per server, and the page uses rundot.dev's styles.
