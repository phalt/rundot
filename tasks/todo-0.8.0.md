# rundot 0.8.0 tasks

Plan: [tasks/plan-0.8.0.md](plan-0.8.0.md). Spec: [specs/SPEC-0.8.0.md](../specs/SPEC-0.8.0.md).

Every task is done test-first, in red-green slices, and also clears the standing bar:
- `make format`, `make ty`, `make lint-imports` and `make test` are clean, with 100% line and branch coverage
- the `## 0.8.0` section of `CHANGELOG.md`, copied to `docs/CHANGELOG.md`, covers anything user-visible
- no test opens a real browser, binds anything but `127.0.0.1` port 0, or reads or writes the real `~/.local/state`

---

## Task 1: Runner pauses before each block, and events carry `line`

**Description:** `runner.run` takes `block_by_block: bool = False`. When it's set, `prompter.confirm()` is asked before each run block, and any answer but y finishes the run as `aborted`. `BlockStarted`, `Confirm` and `InputBlock` already carry the block's opening fence line as `block.line`, and `jsonlines.to_json` already writes it as `line` (0.7.0); a test pins it to the fence.

**Acceptance criteria:**
- [x] With `block_by_block=True`, the prompter is asked once before each run block and not before docstring, confirm or input blocks
- [x] Any answer but y finishes `aborted` at that step, and the block isn't run
- [x] `block_by_block` with `start` part way asks only for the blocks that will run
- [x] `line` on the three events is their opening fence line, and is in their JSON lines (already true since 0.7.0)
- [x] With the defaults, behaviour is unchanged (existing tests pass untouched)

**Files likely touched:** `rundot/runner.py`, `rundot/jsonlines.py`, `tests/test_runner.py`, `tests/test_jsonlines.py`

**Dependencies:** None

## Task 2: Render a whole runbook as HTML

**Description:** `runbook.render_html(text: str) -> str` renders everything after the frontmatter with markdown-it (`html=True`, tables on). Each `run`, `docstring`, `confirm` and `input` fence becomes a `div.rd-block` with `data-kind`, `data-line` and, for run blocks, `data-lang`. Its code is in a `<pre><code>`. Other fences are plain `<pre><code>`. Step headings get `data-step="<n>"`. Raw HTML is passed through for the sanitiser.

**Acceptance criteria:**
- [x] Every fixture in `tests/runbooks` renders; `data-line` matches `Block.line` for every block `parse` finds
- [x] Non-rundot fences (e.g. ```` ```json ````) have no `rd-block`
- [x] Prose, lists, tables, links and images render as markdown-it renders them
- [x] Step headings carry `data-step` in order, matching `Runbook.steps`
- [x] Code is HTML-escaped inside blocks

**Files likely touched:** `rundot/runbook.py`, `tests/test_runbook.py`

**Dependencies:** None

## Task 3: Sanitiser

**Description:** `rundot/web/sanitise.py`, with `sanitise(html: str) -> str`. It's a stdlib `html.parser` allowlist modelled on GitHub's: tags such as `details`, `summary`, `img`, `kbd`, `sub`, `sup`, `br`, `div` and `p` with `align`, `img` with `src`, `alt`, `width` and `height`, plus everything markdown-it emits. It also keeps `class` and `data-*` attributes on the `rd-block` hooks from task 2. Everything else is dropped. Add the `rundot.web` layer to the import-linter contracts.

**Acceptance criteria:**
- [x] Allowed tags and attributes survive unchanged
- [x] `script`, `style`, `iframe`, `object`, `form` and their content are removed
- [x] `on*` attributes, `style` attributes and `javascript:`, `data:` and `vbscript:` URLs are removed, including mixed case and entity-encoded schemes
- [x] `<svg><script>`, `<img src=x onerror=…>` and unclosed tags can't produce a script
- [x] Text is re-escaped, so `&lt;script&gt;` in a paragraph stays text
- [x] `rundot.web` sits in its own layer above `transcript | state | jsonlines`, and isn't allowed click or rich

**Files likely touched:** `rundot/web/__init__.py`, `rundot/web/sanitise.py`, `pyproject.toml`, `tests/web/test_sanitise.py`

**Dependencies:** Task 2

## Task 4: Run hub and `WebPrompter`

**Description:** `rundot/web/hub.py`. `RunHub` owns at most one run. It has `start(path, start_run, resume)`, a replay buffer of JSON events, `subscribe()` returning a queue that's fed the buffer and then live events, `answer(kind, value)`, and `stop()`. `WebPrompter` implements `Prompter` by publishing a `prompt` event and blocking until `answer` gives a value.

**Acceptance criteria:**
- [x] A second `start` while a run is going raises `Busy`, naming the running book
- [x] `ask` for an input publishes `{"event": "prompt", "kind": "input", "name": …, "problem": …}` and returns the answer
- [x] A refused value comes back as a second `prompt` with `problem` set, from `inputs.collect`
- [x] `confirm` for Start, ▶ and a confirm block are told apart by `kind`; an answer of the wrong kind raises `WrongAnswer`, and so does an answer when nothing is waiting
- [x] A subscriber that joins mid-run gets every earlier event first, in order
- [x] `stop()` while a prompt is waiting makes the run finish `interrupted`, and the thread ends
- [x] Secret values never appear in any published event

**Files likely touched:** `rundot/web/hub.py`, `tests/web/test_hub.py`

**Dependencies:** Task 1

## Task 5: HTTP server and security

**Description:** `rundot/web/server.py`: a `ThreadingHTTPServer` on `127.0.0.1`, with the routes in the spec's Server table. It handles the token and cookie, `Host` and `Origin` checks, `Content-Security-Policy: default-src 'self'`, SSE from `hub.subscribe()`, static files from the package, and files from the runbook's directory with containment. Pages come from `page.py`, using simple placeholders for now. Task 6 gives them their look.

**Acceptance criteria:**
- [x] Tested with a real server on port 0 in a thread, driven by `http.client`
- [x] Without the cookie, every route is 403; `/?t=<right>` sets an `HttpOnly`, `SameSite=Strict` cookie and redirects without `?t=`; a wrong token sets nothing
- [x] A foreign `Host` is refused; a POST with a foreign or missing `Origin` is refused
- [x] Every HTML response has the CSP header
- [x] `/book/<name>/events` replays and then streams; it ends cleanly when the server stops
- [x] `/answer` returns 409 for `WrongAnswer`, 400 for bad JSON, and 204 otherwise
- [x] `/run` starts, resumes or starts over through the `start_run` callable; it returns 409 with the busy book's name while another run is going
- [x] `/book/<name>/file/…` serves files in the runbook's directory and refuses `..`, absolute paths and symlinks that leave it
- [x] An unknown book or path is 404

**Files likely touched:** `rundot/web/server.py`, `rundot/web/page.py`, `tests/web/test_server.py`

**Dependencies:** Tasks 3 and 4

## Task 6: Page, styles, script and fonts

**Description:** The book page and the library index, as in the spec and the review mock-ups:
- the header with the wordmark and the ← Library link;
- title and description;
- the inputs form at the very top, folding into a summary line after Start;
- `rd-block`s filled with a ▶, an output area and a footer;
- confirm callouts, `input` block fields, and the resume, busy, failed and finished banners.

`rundot.css` holds the rundot.dev tokens from the spec. `rundot.js` handles the `EventSource`, `fetch` answers and the DOM. The fonts are woff2 Latin subsets with `OFL.txt`.

**Acceptance criteria:**
- [x] `page.book(...)` output, for `all_features.md`, has the header link, the form with one field per frontmatter input (password for secrets, `select` for choices), and every `rd-block` with its controls
- [x] Everything a runbook contributes to the page is escaped or sanitised; a test feeds hostile titles, descriptions and input descriptions
- [x] `page.library(...)` lists every book with its title and description, and the same empty message as `rundot library`
- [x] The built wheel contains `rundot/web/static/**` and `OFL.txt` (a test runs `uv build` into `tmp_path`, or inspects the hatch file list)
- [x] No URL in the CSS, JS or templates points off the server
- [x] A ```` ```mermaid ```` fence draws as a diagram with Pele, themed with the `--pele-*` mapping in the spec's Diagrams table; Pele is only loaded on pages with one, and a fence it can't draw stays as code with its error
- [x] Pele's SVG for a flowchart, a sequence diagram and a Gantt chart is checked for `style` attributes and `<style>`; if it needs them, the CSP change goes to Paul before it's made
- [x] The built wheel contains `rundot/web/static/pele/` with Pele's licence and pinned version
- [ ] The manual checklist in the plan passes

**Files likely touched:** `rundot/web/page.py`, `rundot/web/templates/*.html`, `rundot/web/static/rundot.css`, `rundot/web/static/rundot.js`, `rundot/web/static/fonts/*`, `rundot/web/static/pele/*`, `tests/runbooks/mermaid.md`, `tests/web/test_page.py`

**Dependencies:** Task 5

## Task 7: `rundot web` and `rundot run --web`

**Description:** The `web` command takes `[RUNBOOK] [--port N] [--no-browser]`. `run` gains `--web`, which hands off to the same function. The CLI:
- builds `start_run` from the current `run_runbook` wiring (transcript, `StateRecorder`, saved inputs);
- builds `resume_info` from `_resuming` and `_changes`;
- starts the server, opens the browser and prints the URL;
- stops cleanly on Ctrl-C.

**Acceptance criteria:**
- [ ] `rundot web X`, `rundot run X --web` and `rundot X --web` all start the same server, for the same book (`webbrowser.open` and `serve_forever` patched)
- [ ] With no `RUNBOOK`, it opens the library index, not the picker
- [ ] It prints `rundot web: http://127.0.0.1:<port>/?t=<token>` and `Ctrl-C to stop`; `--no-browser` doesn't call `webbrowser.open`
- [ ] `--web` with `--dry`, `--step`, `--show-code`, `--no-input`, `--json`, `--resume` or `--restart` is a usage error (exit 2)
- [ ] End to end, through the server: a run of `transcript-example.md` writes the same transcript as `rundot run` with the same inputs, apart from times
- [ ] End to end: a run failed in the web resumes with `rundot run`, and one failed with `rundot run` shows the resume banner in the web
- [ ] Ctrl-C saves `interrupted` and exits 130, as `rundot run` does
- [ ] A broken runbook fails `rundot web` the same way it fails `rundot run`, before the server starts

**Files likely touched:** `rundot/cli.py`, `tests/test_cli.py`, `tests/web/test_end_to_end.py`

**Dependencies:** Task 6

## Task 8: Docs and changelog

**Acceptance criteria:**
- [ ] `docs/cli.md`: `rundot web`, its flags, `run --web`, and the security notes (localhost only, the token)
- [ ] `docs/index.md` and `README.md`: a short "Run it in your browser" section with a screenshot
- [ ] `docs/runbook-format.md`: a note that raw HTML renders as on GitHub in `rundot web`
- [ ] `docs/runbook-format.md` and `docs/cli.md`: mermaid fences render as diagrams in `rundot web`, with [Pele](https://pele.run) credited and linked; `rundot run` shows them as code
- [ ] `CHANGELOG.md` and `docs/CHANGELOG.md` have a `## 0.8.0` section
- [ ] `test_docs.py` passes with the new pages

**Files likely touched:** `docs/cli.md`, `docs/index.md`, `docs/runbook-format.md`, `README.md`, `CHANGELOG.md`, `docs/CHANGELOG.md`, `docs/web.png`

**Dependencies:** Tasks 1–7
