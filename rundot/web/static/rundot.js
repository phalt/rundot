// rundot web's page script. It reads the run's events from the server and answers its prompts. Everything is keyed
// by data-line (blocks) and data-step (step headings), as page.py writes them. The server is the source of truth:
// on every (re)connect it replays the run so far, and this script rebuilds the page from those events. Only the tab
// that started a run can answer it, so each tab names itself to the server, and every other tab is told it's busy.

const page = document.querySelector("main.rd-book");
if (page) book(page);
const fences = document.querySelectorAll("pre > code.language-mermaid");
if (fences.length) diagrams(fences); // Pele is only loaded on a page that has a diagram.

function book(main) {
  const base = `/book/${main.dataset.book}`; // already URL-encoded by page.py
  const form = main.querySelector("[data-inputs]");
  const summary = main.querySelector("[data-inputs-summary]");
  const blocks = new Map([...main.querySelectorAll(".rd-doc .rd-block")].map((b) => [b.dataset.line, b]));
  const steps = [...main.querySelectorAll(".rd-doc h2[data-step]")];
  const written = new Map([...blocks].map(([line, b]) => [line, code(b)?.textContent])); // code as written
  const banner = (name) => main.querySelector(`[data-banner="${name}"]`);
  const tab = tabName();
  let state; // reset on every connect, and when a new run starts

  function reset() {
    const { starting = false, busy = false } = state ?? {};
    state = { live: null, current: null, step: null, ended: true, pending: false, resuming: false, starting, busy };
    for (const [line, b] of blocks) {
      if (code(b) && written.get(line) !== undefined) code(b).textContent = written.get(line);
      b.classList.remove("rd-active");
      const output = b.querySelector(".rd-output");
      if (output) (output.textContent = ""), (output.hidden = true);
      const footer = b.querySelector(".rd-footer");
      if (footer) (footer.textContent = ""), (footer.hidden = true), (footer.className = "rd-footer");
      for (const field of b.querySelectorAll("input, select")) if (field.tagName === "INPUT") field.value = "";
    }
    for (const s of steps) mark(s, "");
    for (const name of ["resume", "error", "failed", "finished"]) banner(name).hidden = true;
    for (const error of main.querySelectorAll(".rd-field-error")) error.hidden = true;
    form.hidden = false;
    summary.hidden = true;
  }

  // The prompt a run is waiting on is live only until the next event, so a replayed one that was answered isn't.
  function controls() {
    for (const el of main.querySelectorAll(".rd-doc button, .rd-doc input, .rd-doc select")) el.disabled = true;
    const { live, current, busy } = state;
    const forForm = live?.kind === "start" || (live?.kind === "input" && !fieldIn(current, live.name));
    const formOn = !busy && ((state.ended && !state.pending) || forForm);
    for (const el of form.elements) el.disabled = !formOn;
    for (const button of main.querySelectorAll(".rd-banners button")) button.disabled = busy || !state.ended;
    if (!live || !current) return;
    const enable = (selector) => current.querySelectorAll(selector).forEach((el) => (el.disabled = false));
    if (live.kind === "block") enable('[data-action="block"]');
    if (live.kind === "confirm") enable('[data-action="done"], [data-action="stop"]');
    if (live.kind === "input" && !forForm) enable("input, select, button"), fieldIn(current, live.name).focus();
  }

  const handlers = {
    overview(e) {
      const shown = Object.entries(e.inputs).map(([name, value]) => `${name} ${value}`);
      summary.textContent = shown.length ? shown.join(" · ") : "no inputs";
      form.hidden = true;
      summary.hidden = false;
      state.resuming = e.resuming_at !== null;
      steps.slice(0, e.resuming_at ?? 0).forEach((s) => mark(s, "✓"));
    },
    step_started(e) {
      if (state.step && !markOf(state.step)) mark(state.step, "✓");
      state.step = steps[e.number - 1] ?? null;
    },
    docstring: (e) => at(e.line, e.text),
    confirm: (e) => at(e.line, e.text),
    input_block: (e) => at(e.line),
    input_collected(e) {
      const field = fieldIn(blocks.get(String(e.line)), e.name);
      if (field) field.value = e.value;
    },
    block_started(e) {
      const b = at(e.line, e.code);
      if (b) b.querySelector(".rd-output").hidden = true;
    },
    output(e) {
      const output = state.current?.querySelector(".rd-output");
      if (!output) return;
      const line = document.createElement("span");
      line.textContent = `${e.text}\n`;
      if (e.stderr) line.className = "rd-stderr";
      output.append(line);
      output.hidden = false;
      output.scrollTop = output.scrollHeight;
    },
    block_skipped: (e) => footer(blocks.get(String(e.line)), "↷ skipped", ""),
    block_finished(e) {
      const ok = e.exit_code === 0;
      const time = `${e.duration.toFixed(1)}s`;
      const text = ok ? `✓ exit 0 · ${time}` : `✗ exit ${e.exit_code} · ${time}`;
      footer(blocks.get(String(e.line)), text, ok ? "rd-ok" : "rd-fail");
    },
    run_finished(e) {
      Object.assign(state, { ended: true, starting: false, live: null });
      state.current?.classList.remove("rd-active");
      if (e.status === "ok") {
        if (state.step) mark(state.step, "✓");
        return show("finished", `Finished in ${e.duration.toFixed(1)}s.`);
      }
      if (state.step) mark(state.step, "✗");
      const where = e.step ? ` at “${e.step}”` : "";
      const why = {
        failed: e.exit_code == null ? `The run failed${where}.` : `A block failed${where}, with exit ${e.exit_code}.`,
        aborted: `The run was stopped${where}.`,
        interrupted: `The run was interrupted${where}.`,
      }[e.status];
      show("failed", e.message || why || `The run ended${where}: ${e.status}.`);
    },
    prompt(e) {
      state.live = e;
      const field = e.kind === "input" && (fieldIn(state.current, e.name) || fieldIn(form, e.name));
      if (field) problem(field, e.problem);
      // Start was this tab's answer to Start?, and the form's values are its answers to the inputs it asks for.
      if (state.starting && e.kind === "start") return answer("start", "y");
      if (state.starting && field && form.contains(field) && !e.problem && field.value !== "") {
        return answer("input", field.value);
      }
    },
    busy(e) {
      state.busy = e.book !== null;
      banner("busy").hidden = !state.busy;
      banner("busy").querySelector("[data-message]").textContent = e.book ?? "";
    },
    resume: (e) => show("resume", e.message),
    error: (e) => show("error", e.message),
  };

  function at(line, text) {
    const b = blocks.get(String(line));
    if (!b) return null;
    if (state.resuming) {
      // Run blocks before the first the run reaches were done in an earlier run.
      for (const [, earlier] of blocks) {
        if (earlier === b) break;
        if (earlier.dataset.kind === "run" && earlier.querySelector(".rd-footer").hidden) {
          footer(earlier, "done in an earlier run", "");
        }
      }
      state.resuming = false;
    }
    if (text !== undefined && code(b)) code(b).textContent = text;
    state.current?.classList.remove("rd-active");
    state.current = b;
    b.classList.add("rd-active");
    b.scrollIntoView({ block: "nearest", behavior: "smooth" });
    return b;
  }

  function footer(b, text, kind) {
    const f = b?.querySelector(".rd-footer");
    if (f) (f.textContent = text), (f.className = `rd-footer ${kind}`.trim()), (f.hidden = false);
  }

  function show(name, message) {
    banner(name).querySelector("[data-message]").textContent = message ?? "";
    banner(name).hidden = false;
  }

  async function post(path, body) {
    const response = await fetch(base + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, tab }),
    });
    if (response.ok) return;
    const text = await response.text();
    let busy = null;
    try { busy = JSON.parse(text).busy; } catch { /* not JSON: a message */ }
    if (busy) handlers.busy({ book: busy });
    else show("error", text);
    state.pending = false;
    controls();
  }

  function answer(kind, value) {
    state.live = null;
    controls();
    return post("/answer", { kind, value });
  }

  function run(resume) {
    Object.assign(state, { starting: true, pending: true });
    for (const name of ["resume", "error", "failed", "finished"]) banner(name).hidden = true;
    controls();
    return post("/run", { resume });
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const { live } = state;
    if (live?.kind === "input") return answer("input", fieldIn(form, live.name)?.value ?? "");
    if (live?.kind === "start") return answer("start", "y");
    if (state.ended) run(false);
  });
  main.addEventListener("submit", (event) => {
    if (event.target === form) return;
    event.preventDefault();
    if (state.live?.kind === "input") answer("input", fieldIn(state.current, state.live.name).value);
  });
  main.addEventListener("click", (event) => {
    const action = event.target.closest("button[data-action]")?.dataset.action;
    if (action === "block") answer("block", "y");
    if (action === "done") answer("confirm", "y");
    if (action === "stop") answer("confirm", "n");
    if (action === "resume") run(true);
    if (action === "restart") run(false);
  });

  const events = new EventSource(`${base}/events?tab=${encodeURIComponent(tab)}`);
  events.addEventListener("open", () => {
    state = undefined;
    reset();
    controls();
  });
  events.addEventListener("error", () => {
    if (events.readyState === EventSource.CONNECTING) show("error", "Lost touch with rundot web. Trying again…");
  });
  events.addEventListener("message", (message) => {
    const e = JSON.parse(message.data);
    if (e.event !== "busy" && e.event !== "resume") {
      if (state.ended) reset(), (state.ended = false), (state.pending = false);
      state.live = null;
    }
    handlers[e.event]?.(e);
    controls();
  });
}

// This tab's name. sessionStorage keeps it across reloads, and no other tab shares it, so a reloaded tab that started
// the run can carry on with it. Without storage, as in a private window that blocks it, a reload is a new tab.
function tabName() {
  try {
    const kept = sessionStorage.getItem("rundot-tab");
    if (kept) return kept;
    const made = crypto.randomUUID();
    sessionStorage.setItem("rundot-tab", made);
    return made;
  } catch {
    return crypto.randomUUID();
  }
}

function code(b) {
  return b.querySelector(":scope > pre > code");
}

function fieldIn(element, name) {
  return element?.querySelector(`[data-input="${CSS.escape(name)}"] :is(input, select)`) ?? null;
}

function problem(field, text) {
  const label = field.closest(".rd-field");
  const error = label.querySelector(".rd-field-error");
  error.textContent = text ?? "";
  error.hidden = !text;
  label.classList.toggle("rd-invalid", Boolean(text));
}

function mark(heading, text) {
  const m = heading.querySelector(".rd-step-mark");
  if (!m) return;
  m.textContent = text;
  m.className = `rd-step-mark${text === "✓" ? " rd-ok" : text === "✗" ? " rd-fail" : ""}`;
}

function markOf(heading) {
  return heading.querySelector(".rd-step-mark")?.textContent;
}

// Diagrams: Pele draws each mermaid fence once the page's fonts are ready, since it measures its labels. A fence
// it can't draw stays as code, with Pele's error under it.
async function diagrams(fences) {
  const [pele] = await Promise.all([import("./pele/pele.js"), document.fonts.ready]);
  for (const fence of fences) {
    const pre = fence.parentElement;
    const figure = document.createElement("figure");
    figure.className = "rd-mermaid";
    pre.before(figure);
    const style = getComputedStyle(figure);
    try {
      const { svg } = pele.render(fence.textContent, {
        fontFamily: style.fontFamily,
        fontFamilyMono: style.getPropertyValue("--mono").trim(),
        images: false,
        linkSchemes: ["http", "https", "mailto"],
      });
      figure.append(svgNode(svg));
      pre.hidden = true;
    } catch (error) {
      figure.remove();
      const p = document.createElement("p");
      p.className = "rd-mermaid-error";
      p.textContent = error?.message ?? String(error);
      pre.after(p);
    }
  }
}

// The page's CSP blocks style attributes in markup, and Pele's SVG uses them for its theme variables and corners.
// So in each tag they're renamed before the SVG is parsed, then set through CSSOM, which CSP allows. Pele escapes
// the text and attributes it writes, so a tag can't hold a > or a quote of its own.
function svgNode(svg) {
  const renamed = svg.replace(/<[^>]+>/g, (tag) => tag.replace(/ style="/g, ' data-rd-style="'));
  const root = new DOMParser().parseFromString(renamed, "image/svg+xml").documentElement;
  if (!(root instanceof SVGSVGElement)) throw new Error("Pele's output isn't an SVG");
  document.adoptNode(root);
  for (const el of [root, ...root.querySelectorAll("[data-rd-style]")]) {
    if (!el.hasAttribute("data-rd-style")) continue;
    el.style.cssText = el.getAttribute("data-rd-style");
    el.removeAttribute("data-rd-style");
  }
  return root;
}
