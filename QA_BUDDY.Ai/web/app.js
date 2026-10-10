"use strict";

/* QABuddy.ai chat UI: modes, source filters, streamed answers with clickable citations. */

const SOURCE_COLORS = {
  selenium: "#3f9142",
  playwright: "#d9534f",
  test_cases: "#7c3aed",
  jira: "#2563eb",
  company_docs: "#0e8fa8",
  figma: "#a855f7",
  meetings: "#c77c02",
  lucid: "#e8590c",
  requirements: "#db2777",
  jenkins: "#9f1d1d",
};

const MODE_ICONS = {
  ask: '<path d="M4 4.5h12v8.5H9.5L6 16v-3H4z"/>',
  test_design: '<path d="M8 3h4M9 3v5l-4.2 7.1A1.4 1.4 0 0 0 6 17h8a1.4 1.4 0 0 0 1.2-1.9L11 8V3"/><path d="M6.6 12.5h6.8"/>',
  rca: '<circle cx="9" cy="9" r="5.5"/><path d="M13 13l4 4M9 6.5v3M9 11.6v.1"/>',
  framework: '<path d="M7 6l-4 4 4 4M13 6l4 4-4 4M11 4.5l-2 11"/>',
  onboarding: '<circle cx="10" cy="10" r="7"/><path d="M12.8 7.2l-1.6 4-4 1.6 1.6-4z"/>',
  flaky: '<path d="M2.5 10.5h3l2-5 3 9.5 2-4.5h5"/>',
  rtm: '<path d="M8.5 11.5l3-3M7.4 9.1L5.7 10.8a2.4 2.4 0 0 0 3.4 3.4l1.7-1.7M12.6 10.9l1.7-1.7a2.4 2.4 0 0 0-3.4-3.4L9.2 7.5"/>',
};

const state = {
  config: null,
  mode: "ask",
  selected: new Set(),
  history: [],
  busy: false,
  controller: null,
  lastIngestSeen: undefined, // finish time of the newest auto-ingest run seen; null = none yet
};

const $ = (selector, root = document) => root.querySelector(selector);

init();

async function init() {
  wireUi();
  try {
    state.config = await getJSON("api/config");
  } catch (error) {
    const box = document.createElement("div");
    box.className = "error-box";
    box.textContent = `Could not load the configuration: ${error.message}`;
    $("#welcome").append(box);
    return;
  }
  renderModes();
  setMode("ask");
  renderIndexInfo();
  refreshHealth();
  setInterval(refreshHealth, 30000);
  $("#question").focus();
}

/* ---------- wiring ---------- */

function wireUi() {
  const question = $("#question");
  $("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    if (state.busy) {
      state.controller?.abort();
      return;
    }
    send(question.value);
  });
  question.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!state.busy) send(question.value);
    }
  });
  question.addEventListener("input", autosize);
  $("#new-chat").addEventListener("click", newChat);
  $("#toggle-sources").addEventListener("click", () => {
    const active = activeSources().map((s) => s.key);
    const allOn = active.every((k) => state.selected.has(k));
    state.selected = allOn ? new Set(defaultSources(state.mode)) : new Set(active);
    renderSources();
  });
  $("#menu").addEventListener("click", () => setMenu(true));
  $("#scrim").addEventListener("click", () => {
    setMenu(false);
    closeDrawer();
  });
  $("#drawer-close").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeDrawer();
      setMenu(false);
    }
  });
}

function autosize() {
  const box = $("#question");
  box.style.height = "auto";
  box.style.height = `${Math.min(box.scrollHeight, 220)}px`;
}

function setMenu(open) {
  $("#app").classList.toggle("menu-open", open);
  $("#scrim").hidden = !open && $("#drawer").hidden;
}

/* ---------- modes and sources ---------- */

function modeByKey(key) {
  return state.config.modes.find((m) => m.key === key) || state.config.modes[0];
}

function activeSources() {
  return state.config.sources.filter((s) => s.phase <= 1);
}

function defaultSources(modeKey) {
  const mode = modeByKey(modeKey);
  const active = new Set(activeSources().map((s) => s.key));
  const wanted = mode.sources.length ? mode.sources.filter((k) => active.has(k)) : [...active];
  return wanted.length ? wanted : [...active];
}

function renderModes() {
  const box = $("#modes");
  box.replaceChildren();
  for (const mode of state.config.modes) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mode";
    button.setAttribute("role", "radio");
    button.dataset.mode = mode.key;
    button.innerHTML = `<span class="mode-icon"><svg viewBox="0 0 20 20" aria-hidden="true">${MODE_ICONS[mode.key] || MODE_ICONS.ask}</svg></span><span></span>`;
    button.lastChild.textContent = mode.label;
    button.title = mode.hint;
    button.addEventListener("click", () => {
      setMode(mode.key);
      setMenu(false);
      $("#question").focus();
    });
    box.append(button);
  }
}

function setMode(key) {
  state.mode = key;
  const mode = modeByKey(key);
  for (const button of document.querySelectorAll(".mode")) {
    button.setAttribute("aria-checked", String(button.dataset.mode === key));
  }
  $("#mode-label").textContent = mode.label;
  $("#question").placeholder = mode.hint;
  state.selected = new Set(defaultSources(key));
  renderSources();
  renderExamples();
}

function renderSources() {
  const box = $("#sources");
  box.replaceChildren();
  for (const source of state.config.sources) {
    const disabled = source.phase > 1;
    const row = document.createElement("label");
    row.className = "source" + (disabled ? " disabled" : "") + (!disabled && !source.chunks ? " empty" : "");
    row.title = `${source.label}: data/${source.folder}` + (disabled ? " (Phase 2)" : ` · ${source.chunks} chunks`);
    const box1 = document.createElement("input");
    box1.type = "checkbox";
    box1.checked = !disabled && state.selected.has(source.key);
    box1.disabled = disabled;
    box1.addEventListener("change", () => {
      if (box1.checked) state.selected.add(source.key);
      else state.selected.delete(source.key);
      updateFilterHint();
    });
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = colorOf(source.key);
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = source.label;
    const count = document.createElement("span");
    if (disabled) {
      count.className = "badge";
      count.textContent = "Phase 2";
    } else {
      count.className = "count";
      count.textContent = source.chunks ? source.chunks.toLocaleString() : "empty";
    }
    row.append(box1, swatch, name, count);
    box.append(row);
  }
  updateFilterHint();
}

function updateFilterHint() {
  const active = activeSources();
  const chosen = active.filter((s) => state.selected.has(s.key));
  const allOn = chosen.length === active.length;
  $("#toggle-sources").textContent = allOn ? "Mode default" : "Select all";
  $("#filter-hint").textContent = allOn
    ? "searching all sources"
    : chosen.length
      ? `searching ${chosen.length} of ${active.length} sources`
      : "pick at least one source";
  const empty = chosen.filter((s) => !s.chunks).map((s) => s.label);
  $("#sources-note").textContent = empty.length ? `No data indexed yet: ${empty.join(", ")}.` : "";
}

function renderExamples() {
  const box = $("#examples");
  box.replaceChildren();
  const current = modeByKey(state.mode);
  const picks = current.examples.map((text) => [current, text]);
  for (const mode of state.config.modes) {
    if (picks.length >= 6) break;
    if (mode.key !== current.key && mode.examples.length) picks.push([mode, mode.examples[0]]);
  }
  for (const [mode, text] of picks) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "example";
    button.innerHTML = '<span class="ex-mode"></span><span class="ex-text"></span>';
    button.firstChild.textContent = mode.label;
    button.lastChild.textContent = text;
    button.addEventListener("click", () => {
      if (mode.key !== state.mode) setMode(mode.key);
      send(text);
    });
    box.append(button);
  }
}

function renderIndexInfo() {
  const total = state.config.sources.reduce((sum, s) => sum + (s.chunks || 0), 0);
  const finished = state.config.last_ingest?.finished;
  const parts = [`${total.toLocaleString()} chunks indexed`];
  if (finished) parts.push(`updated ${relativeTime(finished)}`);
  $("#index-info").textContent = parts.join(" · ");
}

/* ---------- status ---------- */

async function refreshHealth() {
  let health = null;
  try {
    health = await getJSON("api/health");
  } catch {
    /* rendered as offline below */
  }
  const rows = [];
  if (!health) {
    rows.push(["error", "QABuddy", "server not reachable"]);
  } else {
    const index = health.qdrant;
    rows.push(index.ok ? ["ok", "Index", `${index.engine || "Qdrant"} · ${index.collection}`] : ["error", "Index", index.error]);
    const embeddings = health.embeddings;
    rows.push(!embeddings.ok ? ["error", "Embeddings", embeddings.error] : embeddings.note ? ["warn", "Embeddings", embeddings.note] : ["ok", "Embeddings", embeddings.model]);
    rows.push(health.llm.configured ? ["ok", health.llm.provider, health.llm.model] : ["warn", "LLM", "not configured: sources only"]);
    if (health.auto_ingest?.enabled) rows.push(autoIngestRow(health.auto_ingest));
    noticeNewIngest(health.auto_ingest?.last_run?.finished);
  }
  if (state.config) renderIndexInfo(); // keeps "updated N min ago" current
  const box = $("#status");
  box.replaceChildren();
  for (const [stateName, name, value, title] of rows) {
    const row = document.createElement("div");
    row.className = "status-row";
    row.title = title || `${name}: ${value}`;
    row.innerHTML = '<span class="dot"></span><b></b><span class="val"></span>';
    row.children[0].dataset.state = stateName;
    row.children[1].textContent = name;
    row.children[2].textContent = value;
    box.append(row);
  }
}

function autoIngestRow(auto) {
  const every = auto.every_minutes === 60 ? "hourly" : `every ${auto.every_minutes} min`;
  const next = auto.next_run ? ` · next ${clock(auto.next_run)}` : "";
  const last = auto.last_run;
  const lastText = last ? ` Last run ${clock(last.finished)} (${last.seconds} s): ${last.summary}.` : " No run yet.";
  const title = `Auto-ingest ${every}: git pull, Jira sync, then index what changed.${lastText}`;
  if (auto.running) return ["ok", "Auto-ingest", `${every} · running now`, title];
  if (last?.status === "error") return ["warn", "Auto-ingest", `last run failed${next}`, title];
  return [last?.status === "warning" ? "warn" : "ok", "Auto-ingest", `${every}${next}`, title];
}

// After a background run, reload the chunk counts and the "updated" time.
async function noticeNewIngest(finished) {
  const seen = state.lastIngestSeen;
  state.lastIngestSeen = finished || null;
  if (seen === undefined || !finished || finished === seen) return; // first poll, no run yet, or nothing new
  try {
    state.config = await getJSON("api/config");
  } catch {
    return;
  }
  renderSources();
  renderIndexInfo();
}

function clock(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/* ---------- chat ---------- */

function newChat() {
  state.controller?.abort();
  state.history = [];
  const thread = $("#thread");
  for (const node of [...thread.children]) if (node.id !== "welcome") node.remove();
  $("#welcome").hidden = false;
  closeDrawer();
  setMenu(false);
  $("#question").value = "";
  autosize();
  $("#question").focus();
}

async function send(raw) {
  const question = raw.trim();
  if (!question || state.busy) return;
  const sources = [...state.selected];
  if (!sources.length) {
    updateFilterHint();
    $("#filter-hint").textContent = "pick at least one source first";
    return;
  }
  $("#welcome").hidden = true;
  $("#question").value = "";
  autosize();
  appendUser(question);
  const view = appendAssistant();
  const payload = { question, history: state.history.slice(-6), mode: state.mode, sources };
  state.history.push({ role: "user", content: question });
  setBusy(true);

  let answer = "";
  let found = [];
  try {
    await streamChat(payload, {
      sources(data) {
        found = data.sources;
        view.searching(found.length ? `Found ${found.length} sources. Writing the answer…` : "No matching sources");
        view.sources(found, null);
      },
      delta(text) {
        answer += text;
        view.answer(answer, found);
      },
      done(data) {
        view.done(answer, data);
      },
      error(data) {
        view.error(data.message);
      },
    });
  } catch (error) {
    if (error.name === "AbortError") view.stopped(answer, found);
    else view.error(error.message);
  } finally {
    setBusy(false);
  }
  if (answer) state.history.push({ role: "assistant", content: answer });
}

function setBusy(busy) {
  state.busy = busy;
  state.controller = busy ? new AbortController() : null;
  const button = $("#send");
  button.classList.toggle("stop", busy);
  button.setAttribute("aria-label", busy ? "Stop" : "Send");
  button.innerHTML = busy
    ? '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="5.5" y="5.5" width="9" height="9" rx="1.5"/></svg>'
    : '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10h11M11 5l5 5-5 5"/></svg>';
}

async function streamChat(payload, handlers) {
  const response = await fetch("api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: state.controller.signal,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data && handlers[event]) handlers[event](JSON.parse(data));
    }
  }
}

function appendUser(text) {
  const node = $("#tpl-user").content.firstElementChild.cloneNode(true);
  node.querySelector(".bubble").textContent = text;
  $("#thread").append(node);
  scrollDown(true);
}

function appendAssistant() {
  const node = $("#tpl-assistant").content.firstElementChild.cloneNode(true);
  $("#thread").append(node);
  scrollDown(true);
  const searching = node.querySelector(".searching");
  const answerBox = node.querySelector(".answer");
  const notice = node.querySelector(".notice");
  const used = node.querySelector(".sources-used");
  const foot = node.querySelector(".msg-foot");
  let frame = 0;

  return {
    searching(text) {
      node.querySelector(".searching-text").textContent = text;
    },
    sources(list, cited) {
      renderSourceCards(used, list, cited);
    },
    answer(text, list) {
      searching.hidden = true;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        renderMarkdown(answerBox, text, list);
        scrollDown();
      });
    },
    done(text, data) {
      cancelAnimationFrame(frame);
      searching.hidden = true;
      renderMarkdown(answerBox, text, data.sources);
      renderSourceCards(used, data.sources, data.cited);
      if (data.notice) {
        notice.textContent = data.notice;
        notice.hidden = false;
      }
      renderFoot(foot, text, data);
      scrollDown();
    },
    stopped(text, list) {
      searching.hidden = true;
      renderMarkdown(answerBox, text ? `${text}\n\n*Stopped.*` : "*Stopped.*", list);
    },
    error(message) {
      searching.hidden = true;
      const box = document.createElement("div");
      box.className = "error-box";
      box.textContent = message;
      answerBox.after(box);
      scrollDown();
    },
  };
}

function renderSourceCards(container, list, cited) {
  if (!list?.length) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  const citedSet = new Set(cited || []);
  const head = container.querySelector(".sources-head");
  head.replaceChildren();
  const title = document.createElement("b");
  title.textContent = "Sources";
  const detail = document.createElement("span");
  detail.textContent = cited
    ? `${citedSet.size} cited · ${list.length} retrieved (hybrid: semantic + keyword)`
    : `${list.length} retrieved (hybrid: semantic + keyword)`;
  head.append(title, detail);
  const cards = container.querySelector(".source-cards");
  cards.replaceChildren();
  for (const source of list) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "source-card" + (cited && !citedSet.has(source.n) ? " uncited" : "");
    card.style.setProperty("--src", colorOf(source.source));
    card.innerHTML = '<span class="n"></span><span class="t"></span><span class="s"></span>';
    card.children[0].textContent = source.n;
    card.children[1].textContent = source.title;
    card.children[2].textContent = [source.label, source.location].filter(Boolean).join(" · ");
    card.title = `${source.label} · ${source.title}${source.location ? ` · ${source.location}` : ""}`;
    card.addEventListener("click", () => openDrawer(source));
    cards.append(card);
  }
}

function renderFoot(foot, text, data) {
  const meta = foot.querySelector(".foot-meta");
  meta.replaceChildren();
  const bits = [];
  if (data.model) bits.push(`${data.provider} · ${data.model}`);
  const usage = data.usage || {};
  if (usage.prompt_tokens != null) bits.push(`${usage.prompt_tokens.toLocaleString()} in / ${(usage.completion_tokens || 0).toLocaleString()} out tokens`);
  const t = data.timings || {};
  if (t.retrieval_ms != null) bits.push(`retrieval ${(t.retrieval_ms / 1000).toFixed(1)} s`);
  if (t.total_ms != null) bits.push(`total ${(t.total_ms / 1000).toFixed(1)} s`);
  for (const bit of bits) {
    const span = document.createElement("span");
    span.textContent = bit;
    meta.append(span);
  }
  const copy = foot.querySelector(".copy");
  copy.onclick = async () => {
    await copyText(text);
    copy.textContent = "Copied";
    setTimeout(() => (copy.textContent = "Copy answer"), 1500);
  };
  foot.hidden = false;
}

/* ---------- markdown and citations ---------- */

const CITATION = /\[(\d{1,2}(?:\s*[,;]\s*\d{1,2})*)\]/g;

function renderMarkdown(target, text, sources) {
  target.innerHTML = DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: false }));
  for (const table of target.querySelectorAll("table")) {
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    table.replaceWith(wrap);
    wrap.append(table);
  }
  for (const link of target.querySelectorAll("a[href]")) {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  for (const pre of target.querySelectorAll("pre")) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy-code";
    button.textContent = "Copy";
    button.addEventListener("click", async () => {
      await copyText((pre.querySelector("code") || pre).innerText);
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = "Copy"), 1200);
    });
    pre.append(button);
  }
  linkCitations(target, sources || []);
}

function linkCitations(root, sources) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => (node.parentElement.closest("code, pre, a, .cite") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.nodeValue;
    CITATION.lastIndex = 0;
    const matches = [...text.matchAll(CITATION)];
    if (!matches.length) continue;
    const fragment = document.createDocumentFragment();
    let last = 0;
    for (const match of matches) {
      fragment.append(text.slice(last, match.index));
      for (const part of match[1].split(/\s*[,;]\s*/)) {
        const n = Number(part);
        const source = sources[n - 1];
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "cite" + (source ? "" : " bad");
        chip.textContent = n;
        if (source) {
          chip.title = `${source.label} · ${source.title}${source.location ? ` · ${source.location}` : ""}`;
          chip.style.setProperty("--src", colorOf(source.source));
          chip.addEventListener("click", () => openDrawer(source));
        } else {
          chip.title = "Not one of the retrieved sources";
        }
        fragment.append(chip);
      }
      last = match.index + match[0].length;
    }
    fragment.append(text.slice(last));
    node.replaceWith(fragment);
  }
}

/* ---------- source drawer ---------- */

function openDrawer(source) {
  const drawer = $("#drawer");
  drawer.style.setProperty("--src", colorOf(source.source));
  $("#drawer-kicker").textContent = `[${source.n}] ${source.label}`;
  $("#drawer-title").textContent = source.title;
  const meta = $("#drawer-meta");
  meta.replaceChildren();
  const badges = [];
  if (source.location) badges.push(source.location);
  if (source.doc_id) badges.push(source.doc_id.split("/").slice(-1)[0]);
  for (const [name, rank] of Object.entries(source.ranks || {})) badges.push(`${name} #${rank}`);
  for (const text of badges) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = text;
    meta.append(badge);
  }
  const actions = $("#drawer-actions");
  actions.replaceChildren();
  if (source.url) {
    const link = document.createElement("a");
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = source.url.includes("github.com") ? "Open on GitHub ↗" : "Open original ↗";
    actions.append(link);
  }
  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "Copy text";
  copy.addEventListener("click", async () => {
    await copyText(source.text);
    copy.textContent = "Copied";
    setTimeout(() => (copy.textContent = "Copy text"), 1200);
  });
  actions.append(copy);
  $("#drawer-text").textContent = source.text;
  $("#drawer-text").scrollTop = 0;
  drawer.hidden = false;
  $("#scrim").hidden = false;
  for (const chip of document.querySelectorAll(".cite.active")) chip.classList.remove("active");
}

function closeDrawer() {
  $("#drawer").hidden = true;
  $("#scrim").hidden = !$("#app").classList.contains("menu-open");
}

/* ---------- helpers ---------- */

async function getJSON(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function colorOf(key) {
  return SOURCE_COLORS[key] || "#0f766e";
}

function scrollDown(force = false) {
  const thread = $("#thread");
  const nearBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 160;
  if (force || nearBottom) thread.scrollTop = thread.scrollHeight;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
}

function relativeTime(iso) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(iso).toLocaleDateString();
}
