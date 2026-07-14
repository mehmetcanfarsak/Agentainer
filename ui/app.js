"use strict";
// Agentainer UI -- vanilla JS, no framework, no build step, no CDN.
// A beginner-friendly, multi-swarm control plane over the file-based mailroom:
//   * a Swarms dashboard (every swarm on the machine, start/stop, open)
//   * a guided Create flow (from an example, hand-edited, or built for you by a
//     coding-agent you talk to in an in-browser terminal)
//   * per-swarm views (agents grid + topology, mail app, terminal, activity,
//     settings) -- every per-swarm request threads ?swarm=<name>
//   * global Settings (the ONE shared Telegram bot)
// The token rides on every request via ?token= and is persisted in localStorage
// so a reload reconnects automatically (the control plane is loopback-bound).

(function () {
  const $ = (id) => document.getElementById(id);
  const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const TOKEN_KEY = "agentainer.token";
  const TG_NUDGE_KEY = "agentainer.tgNudgeDismissed";

  let TOKEN = "";
  const state = {
    view: "dashboard",     // dashboard | create | builder | agents | mail | activity | settings
    swarms: [],            // last /api/swarms .swarms
    defaultSwarm: null,
    swarm: null,           // selected swarm name (per-swarm views)
    settings: null,        // last /api/settings (global; shared Telegram)
    status: null,          // last per-swarm /api/status
    agent: null,           // open agent (mail/terminal detail)
    peer: null,            // open contact
    contacts: [],
    thread: [],
    tab: "mail",           // mail | terminal (agent detail)
    config: null,          // last per-swarm /api/config
    create: null,          // create-flow state
    builder: null,         // { name, agentType }
    wrap: read("paneWrap"),
    rate: read("showRate"),
    notify: read("notifyOptIn"),
    rates: {},
    lastAttention: 0,
    activityN: 100,
    threadShown: 50,
  };
  const ACTIVITY_PAGE = 100;
  const THREAD_PAGE = 50;
  const timers = {};
  function read(k) { try { return !!localStorage.getItem(k); } catch (_) { return false; } }
  function write(k, v) { try { if (v) localStorage.setItem(k, "1"); else localStorage.removeItem(k); } catch (_) {} }

  // ---- tiny utilities ----------------------------------------------------

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function initials(name) {
    if (name === "user") return "You";
    return String(name).slice(0, 2).toUpperCase();
  }
  function hueFor(name) {
    let h = 0;
    for (let i = 0; i < String(name).length; i++) h = (h * 31 + String(name).charCodeAt(i)) % 360;
    return h;
  }
  function avatar(name, cls) {
    const special = name === "user" ? 215 : name === "system" ? 0 : hueFor(name);
    const sat = name === "system" ? "0%" : "62%";
    return `<span class="avatar ${cls || ""}" style="background:hsl(${special} ${sat} 48%)">${esc(initials(name))}</span>`;
  }
  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    return sameDay
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString([], { month: "short", day: "numeric" });
  }
  function fmtDur(s) {
    s = Math.max(0, Math.floor(s || 0));
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m";
    return Math.floor(s / 3600) + "h" + Math.floor((s % 3600) / 60) + "m";
  }
  // Minimal, dependency-free, XSS-safe markdown -> HTML (mail bodies).
  function md(src) {
    const lines = esc(src).replace(/\r/g, "").split("\n");
    const inline = (t) => t
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/~~([^~]+)~~/g, "<del>$1</del>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    let html = "", i = 0;
    const isBlock = (l) => /^\s*(```|#{1,6}\s|[-*+]\s|\d+\.\s|&gt;)/.test(l);
    while (i < lines.length) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        const buf = []; i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
        i++;
        html += `<pre class="md-pre"><code>${buf.join("\n")}</code></pre>`;
        continue;
      }
      const h = line.match(/^\s{0,3}(#{1,6})\s+(.+)$/);
      if (h) { html += `<h${h[1].length} class="md-h">${inline(h[2])}</h${h[1].length}>`; i++; continue; }
      if (/^\s*[-*+]\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) items.push(inline(lines[i++].replace(/^\s*[-*+]\s+/, "")));
        html += "<ul>" + items.map((x) => `<li>${x}</li>`).join("") + "</ul>";
        continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) items.push(inline(lines[i++].replace(/^\s*\d+\.\s+/, "")));
        html += "<ol>" + items.map((x) => `<li>${x}</li>`).join("") + "</ol>";
        continue;
      }
      if (/^\s*&gt;\s?/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) buf.push(inline(lines[i++].replace(/^\s*&gt;\s?/, "")));
        html += `<blockquote>${buf.join("<br>")}</blockquote>`;
        continue;
      }
      if (/^\s*$/.test(line)) { i++; continue; }
      const buf = [];
      while (i < lines.length && !/^\s*$/.test(lines[i]) && !isBlock(lines[i])) buf.push(inline(lines[i++]));
      html += `<p>${buf.join("<br>")}</p>`;
    }
    return html;
  }

  function clearTimers() { Object.values(timers).forEach(clearInterval); Object.keys(timers).forEach((k) => delete timers[k]); }
  function banner(msg) { $("banner").textContent = msg || ""; }
  let toastT = null;
  function toast(msg) {
    const t = $("toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 2400);
  }
  function cssq(s) { return String(s).replace(/"/g, '\\"'); }
  function infoIcon(text) {
    return `<span class="info" tabindex="0" role="button" aria-label="More info: ${esc(text)}" data-tip="${esc(text)}"></span>`;
  }

  // ---- API ---------------------------------------------------------------

  function withToken(path) { return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN); }
  function qs(params) {
    const parts = [];
    for (const k in params) {
      const v = params[k];
      if (v != null && v !== "") parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
    }
    return parts.length ? "?" + parts.join("&") : "";
  }
  // Per-swarm query string (?swarm=<current> [+ extra]).
  function swq(extra) { return qs(Object.assign({ swarm: state.swarm }, extra || {})); }

  // ---- connection honesty ------------------------------------------------
  const conn = { lastOk: 0, down: false, ticker: null };
  function markConn(ok) { if (ok) { conn.lastOk = Date.now(); conn.down = false; } else { conn.down = true; } renderConn(); }
  function renderConn() {
    const node = $("connind"); if (!node || node.hidden) return;
    const age = conn.lastOk ? Date.now() - conn.lastOk : Infinity;
    if (!conn.down && age < 8000) {
      node.className = "connind live";
      node.textContent = "● Live · " + fmtDur(age / 1000) + " ago";
      node.setAttribute("data-tip", "Connected and live: the server last replied recently. The UI auto-refreshes and flips to Reconnecting if the server goes silent.");
    } else {
      node.className = "connind stale";
      node.textContent = "◌ Reconnecting…";
      node.setAttribute("data-tip", "No recent server reply: the UI is trying to reconnect and data shown may be stale.");
    }
  }
  function rawFetch(path, opts) {
    return fetch(withToken(path), opts).then(
      (r) => { markConn(true); return r; },
      (err) => { markConn(false); throw err; },
    );
  }
  function apiGet(path) {
    return rawFetch(path, { headers: { Accept: "application/json" } }).then((r) => {
      if (r.status === 401) throw new Error("unauthorized");
      return r.json();
    });
  }
  function apiPost(path, body) {
    return rawFetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
    }).then((r) => r.json().then((j) => ({ ok: r.ok, status: r.status, j })).catch(() => ({ ok: r.ok, status: r.status, j: {} })));
  }

  // ---- connect / shell ---------------------------------------------------

  function connect() {
    TOKEN = ($("token").value || "").trim();
    if (!TOKEN) { banner("enter a token first"); return; }
    apiGet("/api/swarms").then((data) => {
      write(TOKEN_KEY, false); try { localStorage.setItem(TOKEN_KEY, TOKEN); } catch (_) {}
      onConnected(data);
    }).catch((e) => { banner("connect failed: " + e.message); });
  }

  function tryStoredToken() {
    let stored = "";
    try { stored = localStorage.getItem(TOKEN_KEY) || ""; } catch (_) {}
    if (!stored) return;
    TOKEN = stored;
    apiGet("/api/swarms").then(onConnected).catch(() => { TOKEN = ""; });
  }

  function onConnected(data) {
    $("login").hidden = true;
    $("view").hidden = false;
    $("switcher").hidden = false;
    $("connind").hidden = false;
    if (!conn.ticker) conn.ticker = setInterval(renderConn, 1000);
    renderConn();
    banner("");
    state.swarms = (data && data.swarms) || [];
    state.defaultSwarm = (data && data.default) || null;
    loadSettings();
    go("dashboard");
  }

  function loadSettings() {
    apiGet("/api/settings").then((s) => {
      state.settings = s;
      renderTelegramNudge();
    }).catch(() => {});
  }

  // Header swarm switcher: current context + a dropdown of all swarms.
  function renderSwitcher() {
    const box = $("switcher"); if (!box || box.hidden) return;
    const cur = state.swarm;
    const label = cur ? cur : "All swarms";
    const dot = cur ? dotClass(state.swarms.find((s) => s.name === cur)) : "";
    box.innerHTML = `
      <button class="switcher-btn" id="switcherBtn" aria-haspopup="true" aria-expanded="false" data-tip="Switch between swarms or return to the dashboard. Every swarm on this machine is listed here.">
        ${cur ? `<span class="status-dot ${dot}"></span>` : `<span aria-hidden="true">◆</span>`}
        <span class="nm">${esc(label)}</span><span class="caret">▾</span>
      </button>`;
    $("switcherBtn").onclick = toggleSwitcherMenu;
  }
  function dotClass(sw) {
    if (!sw) return "stopped";
    if (sw.attention > 0) return "attention";
    if (sw.running > 0) return "running pulse";
    return "stopped";
  }
  function toggleSwitcherMenu() {
    const box = $("switcher");
    const existing = box.querySelector(".switcher-menu");
    if (existing) { existing.remove(); return; }
    const items = state.swarms.map((s) => `
      <button class="switcher-item ${s.name === state.swarm ? "active" : ""}" data-open="${esc(s.name)}">
        <span class="status-dot ${dotClass(s)}"></span>
        <span class="nm" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.name)}</span>
        <span class="sub">${s.running}/${s.total}</span>
      </button>`).join("") || `<div class="muted" style="padding:.5rem">No swarms yet.</div>`;
    const menu = el(`
      <div class="switcher-menu" role="menu">
        <button class="switcher-item ${state.swarm ? "" : "active"}" data-dash="1"><span aria-hidden="true">◆</span><span class="nm">Swarms dashboard</span></button>
        <div class="switcher-sep"></div>
        ${items}
        <div class="switcher-sep"></div>
        <button class="switcher-item" data-new="1"><span aria-hidden="true">＋</span><span class="nm">New swarm…</span></button>
        <button class="switcher-item" data-settings="1"><span aria-hidden="true">⚙</span><span class="nm">Global settings</span></button>
      </div>`);
    box.appendChild(menu);
    menu.querySelector("[data-dash]").onclick = () => { menu.remove(); state.swarm = null; go("dashboard"); };
    menu.querySelector("[data-new]").onclick = () => { menu.remove(); startCreate(); };
    menu.querySelector("[data-settings]").onclick = () => { menu.remove(); if (state.swarm) go("settings"); else if (state.swarms[0]) { state.swarm = state.swarms[0].name; go("settings"); } else toast("create a swarm first"); };
    for (const b of menu.querySelectorAll("[data-open]")) b.onclick = () => { menu.remove(); openSwarm(b.dataset.open); };
    setTimeout(() => document.addEventListener("click", closeSwitcherOnce, true), 0);
  }
  function closeSwitcherOnce(e) {
    const box = $("switcher");
    if (box && !box.contains(e.target)) { const m = box.querySelector(".switcher-menu"); if (m) m.remove(); }
    document.removeEventListener("click", closeSwitcherOnce, true);
  }

  function openSwarm(name) {
    state.swarm = name;
    state.agent = null;
    go("agents");
  }

  // Availability toggle only makes sense inside a selected swarm.
  function syncAvailability(available) {
    $("availToggle").checked = !!available;
    $("availLbl").textContent = available ? "You: available" : "You: away";
  }

  function go(view) {
    state.view = view;
    clearTimers();
    const perSwarm = ["agents", "mail", "activity", "settings"].includes(view);
    $("availWrap").hidden = !(perSwarm && state.swarm);
    renderSwitcher();
    if (view === "dashboard") renderDashboard();
    else if (view === "create") renderCreate();
    else if (view === "builder") renderBuilder();
    else if (view === "agents") { renderAgents(); pollStatus(); }
    else if (view === "mail") renderMail();
    else if (view === "activity") renderActivity();
    else if (view === "settings") renderSettings();
  }

  // Sub-navigation shown atop every per-swarm view.
  function subnav(active) {
    const items = [["agents", "Agents"], ["activity", "Activity"], ["settings", "Settings"]];
    return `<div class="subnav">${items.map(([v, l]) =>
      `<button class="${v === active ? "active" : ""}" data-sub="${v}">${l}</button>`).join("")}</div>`;
  }
  function wireSubnav() {
    for (const b of document.querySelectorAll("[data-sub]")) b.onclick = () => go(b.dataset.sub);
  }

  // ---- dashboard (swarms home) -------------------------------------------

  function renderDashboard() {
    apiGet("/api/swarms").then((data) => {
      state.swarms = (data && data.swarms) || [];
      state.defaultSwarm = (data && data.default) || null;
      renderSwitcher();
      const box = $("view");
      if (!state.swarms.length) {
        box.innerHTML = `
          <div class="hero">
            <div class="badge-emoji">🐝</div>
            <div class="big">Welcome to Agentainer</div>
            <p class="muted">You have no swarms yet. A swarm is a team of coding-agent CLIs that talk to each other through file-based mail. Create your first one in a few clicks.</p>
            <button class="btn big" id="heroCreate">＋ Create your first swarm</button>
          </div>`;
        $("heroCreate").onclick = startCreate;
        return;
      }
      const cards = state.swarms.map((s) => {
        const dot = dotClass(s);
        const att = s.attention > 0 ? `<span class="pill busy" data-tip="Messages from agents in this swarm are waiting on your reply.">${s.attention} needs you</span>` : "";
        const sup = s.supervisor_alive === true ? `<span class="pill ok" data-tip="The liveness supervisor for this swarm is running.">supervised</span>` : "";
        return `
          <div class="card swarmcard clickable" data-open="${esc(s.name)}">
            <div class="top">
              ${avatar(s.name, "lg")}
              <div style="min-width:0;flex:1 1 auto">
                <div class="nm">${esc(s.name)}</div>
                <div class="path" title="${esc(s.path || "")}">${esc(s.root || s.path || "")}</div>
              </div>
              <span class="status-dot ${dot}" data-tip="Live status: green = agents running, amber = needs your attention, grey = stopped."></span>
            </div>
            <div class="stats">
              <span class="pill ${s.running > 0 ? "ok" : "mute"}" data-tip="How many of this swarm's agents currently have a running tmux session.">${s.running}/${s.total} running</span>
              ${att}${sup}
            </div>
            <div class="acts">
              <button class="btn ghost sm" data-upall="${esc(s.name)}" data-tip="Start every stopped agent in this swarm and its supervisor.">▶ Start all</button>
              <button class="btn ghost sm" data-downall="${esc(s.name)}" data-tip="Stop every running agent in this swarm.">■ Stop all</button>
              <button class="btn sm" data-open2="${esc(s.name)}">Open →</button>
            </div>
          </div>`;
      }).join("");
      box.innerHTML = `
        <div class="sectiontitle">
          <h2>Swarms <span class="muted" style="font-weight:500">(${state.swarms.length})</span></h2>
          <div class="toolrow">
            <button class="btn ghost sm" id="dashRefresh">Refresh</button>${infoIcon("Re-fetch the status of every swarm on this machine.")}
          </div>
        </div>
        <div class="dashgrid">
          ${cards}
          <div class="card newcard" id="newSwarm" role="button" tabindex="0">
            <div><div class="plus">＋</div><b>New Swarm</b><div class="muted" style="font-size:.85rem">From an example or built by an agent</div></div>
          </div>
        </div>`;
      $("dashRefresh").onclick = renderDashboard;
      const nc = $("newSwarm");
      nc.onclick = startCreate;
      nc.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); startCreate(); } };
      for (const c of box.querySelectorAll(".swarmcard.clickable"))
        c.onclick = (e) => { if (e.target.closest("[data-upall],[data-downall],[data-open2]")) return; openSwarm(c.dataset.open); };
      for (const b of box.querySelectorAll("[data-open2]")) b.onclick = () => openSwarm(b.dataset.open2);
      for (const b of box.querySelectorAll("[data-upall]")) b.onclick = () => swarmLifecycle("up", b.dataset.upall, b);
      for (const b of box.querySelectorAll("[data-downall]")) b.onclick = () => swarmLifecycle("down", b.dataset.downall, b);
      timers.dash = setInterval(() => { if (state.view === "dashboard") refreshDashboardStats(); }, 5000);
    }).catch((e) => banner(e.message));
  }

  function refreshDashboardStats() {
    apiGet("/api/swarms").then((data) => {
      state.swarms = (data && data.swarms) || [];
      renderSwitcher();
      // Patch each card's dot + running pill without a full rebuild.
      for (const s of state.swarms) {
        const card = document.querySelector(`.swarmcard[data-open="${cssq(s.name)}"]`);
        if (!card) { renderDashboard(); return; }
        const d = card.querySelector(".status-dot"); if (d) d.className = "status-dot " + dotClass(s);
        const p = card.querySelector(".stats .pill"); if (p) { p.className = "pill " + (s.running > 0 ? "ok" : "mute"); p.textContent = `${s.running}/${s.total} running`; }
      }
    }).catch(() => {});
  }

  function swarmLifecycle(kind, name, btn) {
    if (kind === "down" && !confirm(`Stop ALL agents in "${name}"? Each tmux session (and any in-flight turn) is killed.`)) return;
    if (btn) btn.disabled = true;
    apiPost("/api/swarms/" + kind, { name }).then((res) => {
      if (btn) btn.disabled = false;
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      const n = kind === "up" ? (res.j.started || []).length : res.j.stopped;
      toast(kind === "up" ? `started ${n} agent(s) in ${name}` : `stopped ${name}`);
      setTimeout(refreshDashboardStats, 700);
    });
  }

  // ---- create flow -------------------------------------------------------

  function startCreate() {
    state.swarm = null;
    state.create = { step: "choose", mode: null, examples: null, example: null, name: "", agentType: "claude", notes: "", root: "", filter: "" };
    go("create");
  }
  function createGo(step) { state.create.step = step; renderCreate(); }

  const CREATE_STEPS = [["choose", "Choose"], ["configure", "Configure"], ["build", "Build & launch"]];
  function stepper(active) {
    const idx = { choose: 0, "example-pick": 0, "example-preview": 1, "example-edit": 1, "example-agent": 1, custom: 1, configure: 1, build: 2 }[active] ?? 0;
    return `<div class="stepper">${CREATE_STEPS.map(([k, l], i) =>
      `${i ? '<span class="arrow">→</span>' : ""}<span class="st ${i <= idx ? "on" : ""}"><span class="n">${i + 1}</span>${l}</span>`).join("")}</div>`;
  }

  function renderCreate() {
    clearTimers();
    const c = state.create;
    const head = `
      <div class="sectiontitle">
        <h2>New swarm</h2>
        <button class="btn ghost sm" id="cCancel">Cancel</button>
      </div>
      ${stepper(c.step)}`;
    let bodyFn;
    if (c.step === "choose") bodyFn = renderCreateChoose;
    else if (c.step === "example-pick") bodyFn = renderExamplePick;
    else if (c.step === "example-preview") bodyFn = renderExamplePreview;
    else if (c.step === "example-edit") bodyFn = renderExampleEdit;
    else if (c.step === "example-agent") bodyFn = renderExampleAgent;
    else if (c.step === "custom") bodyFn = renderCustom;
    else bodyFn = renderCreateChoose;
    $("view").innerHTML = head + `<div id="createBody"></div>`;
    $("cCancel").onclick = () => go("dashboard");
    bodyFn();
  }

  function renderCreateChoose() {
    $("createBody").innerHTML = `
      <div class="choicegrid">
        <button class="card choice" id="chooseExample">
          <div class="emoji">📚</div>
          <b>Start from an example</b>
          <div class="muted">Pick from ready-made swarms. Preview the config, then use it as-is, edit it yourself, or have a coding-agent tailor it for you.</div>
        </button>
        <button class="card choice" id="chooseCustom">
          <div class="emoji">🛠️</div>
          <b>Build a custom swarm</b>
          <div class="muted">Pick a coding-agent CLI, give it a name, and talk to it in an in-browser terminal. It writes the config; you click Approve.</div>
        </button>
      </div>`;
    $("chooseExample").onclick = () => { state.create.mode = "example"; loadExamples(); createGo("example-pick"); };
    $("chooseCustom").onclick = () => { state.create.mode = "custom"; createGo("custom"); };
  }

  function loadExamples() {
    if (state.create.examples) return;
    apiGet("/api/examples").then((d) => {
      state.create.examples = Array.isArray(d) ? d : (d.examples || d.templates || []);
      if (state.create.step === "example-pick") renderExamplePick();
    }).catch(() => {
      state.create.examples = [];
      if (state.create.step === "example-pick") renderExamplePick();
    });
  }

  function renderExamplePick() {
    const c = state.create;
    const exs = c.examples;
    const box = $("createBody");
    if (exs === null) { box.innerHTML = `<div class="card panel"><p class="empty">Loading examples…</p></div>`; return; }
    const filtered = exs.filter((x) => {
      const q = (c.filter || "").toLowerCase();
      return !q || (x.title || x.name || "").toLowerCase().includes(q) || (x.summary || "").toLowerCase().includes(q);
    });
    const rows = filtered.map((x) => `
      <button class="exitem" data-ex="${esc(x.name)}">
        <div class="info2">
          <div class="tt">${esc(x.title || x.name)}</div>
          <div class="sm2">${esc(x.summary || "")}</div>
        </div>
        <span class="cnt">${esc(String(x.agents || 0))} agent${x.agents === 1 ? "" : "s"}</span>
      </button>`).join("") || `<p class="empty">No examples match "${esc(c.filter)}".</p>`;
    box.innerHTML = `
      <div class="card panel">
        <div class="row" style="margin-bottom:.7rem">
          <button class="btn ghost sm" id="exBack">← Back</button>
          <input class="field" id="exSearch" placeholder="Search examples…" value="${esc(c.filter || "")}" style="flex:1 1 200px" aria-label="Search examples"/>
        </div>
        <div class="exlist">${rows}</div>
      </div>`;
    $("exBack").onclick = () => createGo("choose");
    const search = $("exSearch");
    search.oninput = () => { c.filter = search.value; const l = box.querySelector(".exlist"); if (l) renderExampleRows(l, c); };
    for (const b of box.querySelectorAll("[data-ex]")) b.onclick = () => { c.example = exs.find((e) => e.name === b.dataset.ex); createGo("example-preview"); };
  }
  function renderExampleRows(container, c) {
    const q = (c.filter || "").toLowerCase();
    const filtered = c.examples.filter((x) => !q || (x.title || x.name || "").toLowerCase().includes(q) || (x.summary || "").toLowerCase().includes(q));
    container.innerHTML = filtered.map((x) => `
      <button class="exitem" data-ex="${esc(x.name)}">
        <div class="info2"><div class="tt">${esc(x.title || x.name)}</div><div class="sm2">${esc(x.summary || "")}</div></div>
        <span class="cnt">${esc(String(x.agents || 0))} agent${x.agents === 1 ? "" : "s"}</span>
      </button>`).join("") || `<p class="empty">No examples match.</p>`;
    for (const b of container.querySelectorAll("[data-ex]")) b.onclick = () => { c.example = c.examples.find((e) => e.name === b.dataset.ex); createGo("example-preview"); };
  }

  function renderExamplePreview() {
    const c = state.create, x = c.example || {};
    $("createBody").innerHTML = `
      <div class="card panel">
        <div class="row" style="justify-content:space-between">
          <div><h3 style="margin:0">${esc(x.title || x.name)}</h3><div class="muted">${esc(x.summary || "")} · ${esc(String(x.agents || 0))} agents</div></div>
          <button class="btn ghost sm" id="pvBack">← Back</button>
        </div>
        <div class="fld" style="margin-top:.8rem">
          <label>Config preview (read-only) ${infoIcon("The full agentainer.yaml for this example. Choose an option below to use it as-is, edit it, or have an agent tailor it.")}</label>
          <textarea class="field code" rows="14" readonly>${esc(x.raw || "(no preview available)")}</textarea>
        </div>
        <div class="rowend">
          <button class="btn ghost" id="pvEdit">✏️ Edit it yourself</button>${infoIcon("Open the YAML in an editor so you can tweak agents, roles, and settings before creating the swarm.")}
          <button class="btn" id="pvAgent">🤖 Have an agent edit it for me</button>${infoIcon("Create the swarm from this example, then open an in-browser terminal where a coding-agent adapts it to your needs.")}
        </div>
      </div>`;
    $("pvBack").onclick = () => createGo("example-pick");
    $("pvEdit").onclick = () => { c.name = suggestName(x.name); c.rawYaml = x.raw || ""; createGo("example-edit"); };
    $("pvAgent").onclick = () => { c.name = suggestName(x.name); createGo("example-agent"); };
  }

  function suggestName(base) {
    const taken = new Set(state.swarms.map((s) => s.name));
    let n = String(base || "swarm").replace(/[^A-Za-z0-9_-]/g, "-");
    if (!taken.has(n)) return n;
    for (let i = 2; i < 999; i++) if (!taken.has(n + "-" + i)) return n + "-" + i;
    return n + "-copy";
  }

  function renderExampleEdit() {
    const c = state.create;
    $("createBody").innerHTML = `
      <div class="card panel">
        <div class="row" style="margin-bottom:.7rem">
          <button class="btn ghost sm" id="edBack">← Back</button>
          <h3 style="margin:0">Edit &amp; create</h3>
        </div>
        <div class="fld"><label>Swarm name ${infoIcon("Unique name for the new swarm; used for its tmux session prefix and in the registry.")}</label>
          <input class="field" id="edName" value="${esc(c.name)}" placeholder="my-swarm"/></div>
        <div class="fld" style="margin-top:.6rem"><label>Workspace root (optional) ${infoIcon("Directory the agents work in; defaults to ./workspace inside the swarm's folder.")}</label>
          <input class="field" id="edRoot" value="${esc(c.root || "")}" placeholder="leave blank for default"/></div>
        <div class="fld" style="margin-top:.6rem"><label>agentainer.yaml ${infoIcon("The full config. It is validated on the server when you create the swarm; errors come back so you can fix them.")}</label>
          <textarea class="field code" id="edYaml" rows="18">${esc(c.rawYaml || "")}</textarea></div>
        <div class="rowend"><button class="btn" id="edCreate">Create swarm</button></div>
      </div>`;
    $("edBack").onclick = () => createGo("example-preview");
    $("edCreate").onclick = () => {
      const name = $("edName").value.trim();
      if (!name) { toast("name is required"); return; }
      const rawYaml = $("edYaml").value;
      const root = $("edRoot").value.trim();
      createFromYaml(name, rawYaml, root, $("edCreate"));
    };
  }

  function createFromYaml(name, rawYaml, root, btn) {
    if (btn) btn.disabled = true;
    apiPost("/api/swarms/create", { name, raw_yaml: rawYaml, root: root || undefined }).then((res) => {
      if (btn) btn.disabled = false;
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast("created " + name);
      refreshAfterCreate(name);
    });
  }

  // Refetch the swarm list, then open the freshly-created swarm.
  function refreshAfterCreate(name) {
    apiGet("/api/swarms").then((d) => {
      state.swarms = (d && d.swarms) || [];
      openSwarm(name);
      toast("swarm ready — start its agents when you are");
    }).catch(() => openSwarm(name));
  }

  const CLIS = [
    ["claude", "🟣 Claude"], ["codex", "🟢 Codex"], ["gemini", "🔵 Gemini"], ["hermes", "🟠 Hermes"],
  ];
  function cliPicker(selected) {
    return `<div class="clipick">${CLIS.map(([v, l]) =>
      `<button class="clicard ${v === selected ? "active" : ""}" data-cli="${v}">${esc(l)}</button>`).join("")}</div>`;
  }
  function wireCliPicker(box, onPick) {
    for (const b of box.querySelectorAll("[data-cli]")) b.onclick = () => { onPick(b.dataset.cli); for (const x of box.querySelectorAll("[data-cli]")) x.classList.toggle("active", x === b); };
  }

  function renderExampleAgent() {
    const c = state.create;
    const box = $("createBody");
    box.innerHTML = `
      <div class="card panel">
        <div class="row" style="margin-bottom:.7rem"><button class="btn ghost sm" id="eaBack">← Back</button><h3 style="margin:0">Let an agent tailor it</h3></div>
        <p class="muted">We'll create <b>${esc(c.name)}</b> from this example, then open a terminal where the coding-agent adapts it. Describe what you want changed.</p>
        <div class="fld"><label>Swarm name ${infoIcon("Unique name for the new swarm.")}</label><input class="field" id="eaName" value="${esc(c.name)}"/></div>
        <div class="fld" style="margin-top:.6rem"><label>Which coding-agent should build it? ${infoIcon("The CLI that will run in the builder terminal and edit the config. It must be installed and configured on this machine.")}</label>${cliPicker(c.agentType)}</div>
        <div class="fld" style="margin-top:.6rem"><label>What should it change? ${infoIcon("Notes handed to the builder agent as its opening instructions, e.g. 'use 3 reviewers and add a security specialist'.")}</label>
          <textarea class="field" id="eaNotes" rows="4" placeholder="e.g. rename the agents for a fintech team and add a compliance reviewer">${esc(c.notes || "")}</textarea></div>
        <div class="rowend"><button class="btn" id="eaGo">Create &amp; open builder</button></div>
      </div>`;
    $("eaBack").onclick = () => createGo("example-preview");
    wireCliPicker(box, (v) => { c.agentType = v; });
    $("eaGo").onclick = () => {
      c.name = $("eaName").value.trim();
      c.notes = $("eaNotes").value;
      if (!c.name) { toast("name is required"); return; }
      $("eaGo").disabled = true;
      // Create from template first (so there's a file to adapt), then build(adapt).
      apiPost("/api/swarms/create", { name: c.name, template: c.example.name }).then((res) => {
        if (!res.ok) { $("eaGo").disabled = false; toast("error: " + (res.j.error || "failed")); return; }
        buildSwarm(c.name, c.agentType, "adapt", c.notes);
      });
    };
  }

  function renderCustom() {
    const c = state.create;
    const box = $("createBody");
    box.innerHTML = `
      <div class="card panel">
        <div class="row" style="margin-bottom:.7rem"><button class="btn ghost sm" id="cuBack">← Back</button><h3 style="margin:0">Build a custom swarm</h3></div>
        <p class="muted">Pick a coding-agent, name your swarm, then talk to the agent in a terminal. It will ask what you want and write the config for you.</p>
        <div class="fld"><label>Which coding-agent should build it? ${infoIcon("The CLI that runs in the builder terminal to design your swarm from scratch. It must be installed and configured on this machine.")}</label>${cliPicker(c.agentType)}</div>
        <div class="formgrid" style="margin-top:.6rem">
          <div class="fld"><label>Swarm name ${infoIcon("Unique name for the new swarm.")}</label><input class="field" id="cuName" value="${esc(c.name || "")}" placeholder="my-swarm"/></div>
          <div class="fld"><label>Workspace root (optional) ${infoIcon("Directory the agents work in; defaults to ./workspace.")}</label><input class="field" id="cuRoot" value="${esc(c.root || "")}" placeholder="leave blank for default"/></div>
        </div>
        <div class="fld" style="margin-top:.6rem"><label>What do you want to build? (optional) ${infoIcon("Opening instructions for the builder agent describing the swarm you want.")}</label>
          <textarea class="field" id="cuNotes" rows="4" placeholder="e.g. a 3-agent research team: a planner, two researchers, and an editor">${esc(c.notes || "")}</textarea></div>
        <div class="rowend"><button class="btn" id="cuGo">Create &amp; open builder</button></div>
      </div>`;
    $("cuBack").onclick = () => createGo("choose");
    wireCliPicker(box, (v) => { c.agentType = v; });
    $("cuGo").onclick = () => {
      c.name = $("cuName").value.trim();
      c.root = $("cuRoot").value.trim();
      c.notes = $("cuNotes").value;
      if (!c.name) { toast("name is required"); return; }
      $("cuGo").disabled = true;
      apiPost("/api/swarms/create", { name: c.name, root: c.root || undefined }).then((res) => {
        if (!res.ok) { $("cuGo").disabled = false; toast("error: " + (res.j.error || "failed")); return; }
        buildSwarm(c.name, c.agentType, "scratch", c.notes);
      });
    };
  }

  function buildSwarm(name, agentType, mode, notes) {
    apiPost("/api/swarms/build", { name, agent_type: agentType, mode, notes: notes || "" }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); if (state.create) createGo(state.create.step); return; }
      state.builder = { name, agentType, mode };
      state.swarm = name;
      go("builder");
    });
  }

  // ---- builder terminal (talk to the coding-agent) -----------------------

  function renderBuilder() {
    clearTimers();
    const b = state.builder || {};
    $("view").innerHTML = `
      <div class="sectiontitle">
        <h2>Build “${esc(b.name)}”</h2>
        <button class="btn ghost sm" id="bCancel">Cancel</button>
      </div>
      ${stepper("build")}
      <div class="card panel" style="margin-bottom:1rem">
        <p style="margin:.1rem 0">Talk to the <b>${esc(b.agentType || "coding-agent")}</b> in the terminal below. Ask for the swarm you want; it will write <code>agentainer.yaml</code>. When it says it's done, click <b>Approve &amp; Launch</b>.</p>
      </div>
      <div class="card terminal">
        <div class="thead">
          <b>Builder terminal · ${esc(b.name)}</b>
          <label class="wrapchk"><input type="checkbox" id="wrapPane" ${state.wrap ? "checked" : ""}/> Wrap text</label>
          <span class="muted" style="font-size:.78rem">refreshes 2s</span>
        </div>
        <pre class="pane tall${state.wrap ? " wrap" : ""}" id="pane">— starting builder —</pre>
        <div class="keyrow">${KEYS.map((k) => `<button class="keybtn" data-key="${esc(k.key)}" title="${esc(k.title)}">${esc(k.label)}</button>`).join("")}</div>
        <div class="typerow">
          <input class="field" id="typeText" placeholder="Message the builder agent, press Enter…" aria-label="Message the builder agent"/>
          <button class="btn" id="typeSend">Send</button>
        </div>
      </div>
      <div class="rowend" style="margin-top:1rem">
        <button class="btn ghost" id="bDiscard">Discard swarm</button>${infoIcon("Forget this swarm from the registry (its files remain on disk).")}
        <button class="btn big" id="bApprove">✓ Approve &amp; Launch</button>${infoIcon("Validate the config the agent wrote, then launch the swarm. If the YAML is invalid the error is shown so the agent can fix it.")}
      </div>`;
    $("bCancel").onclick = () => go("dashboard");
    $("bDiscard").onclick = () => {
      if (!confirm(`Discard "${b.name}"? It is removed from the registry; files stay on disk.`)) return;
      apiPost("/api/swarms/remove", { name: b.name }).then(() => { toast("discarded " + b.name); go("dashboard"); });
    };
    $("bApprove").onclick = approveBuilder;
    const inp = $("typeText");
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); sendBuilderType(); } });
    $("typeSend").onclick = sendBuilderType;
    for (const btn of document.querySelectorAll(".keybtn")) btn.onclick = () => sendBuilderKey(btn.dataset.key);
    $("wrapPane").onchange = (e) => { state.wrap = e.target.checked; write("paneWrap", state.wrap); $("pane").classList.toggle("wrap", state.wrap); };
    loadBuilderPane();
    timers.builder = setInterval(loadBuilderPane, 2000);
  }
  function loadBuilderPane() {
    apiGet("/api/pane" + qs({ swarm: state.builder.name, agent: "builder" })).then((d) => {
      const p = $("pane"); if (!p) return;
      const atBottom = p.scrollHeight - p.scrollTop - p.clientHeight < 40;
      p.textContent = d.pane || "— (builder starting… if this stays empty, the endpoint or CLI may be unavailable) —";
      if (atBottom) p.scrollTop = p.scrollHeight;
    }).catch(() => {});
  }
  function sendBuilderType() {
    const text = $("typeText").value; if (!text.trim()) return;
    apiPost("/api/type" + qs({ swarm: state.builder.name }), { swarm: state.builder.name, agent: "builder", text }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      $("typeText").value = ""; setTimeout(loadBuilderPane, 400);
    });
  }
  function sendBuilderKey(key) {
    apiPost("/api/key" + qs({ swarm: state.builder.name }), { swarm: state.builder.name, agent: "builder", key }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      setTimeout(loadBuilderPane, 300);
    });
  }
  function approveBuilder() {
    const name = state.builder.name;
    $("bApprove").disabled = true;
    apiPost("/api/swarms/approve", { name }).then((res) => {
      $("bApprove").disabled = false;
      if (!res.ok) { toast("not ready: " + (res.j.error || "config invalid")); return; }
      toast("launched " + name);
      apiGet("/api/swarms").then((d) => { state.swarms = (d && d.swarms) || []; openSwarm(name); }).catch(() => openSwarm(name));
    });
  }

  // ---- agents overview (per-swarm) ---------------------------------------

  const STATE_LABEL = { working: "working", waiting: "waiting", attention: "needs you", stalled: "stalled", stopped: "stopped" };
  const STATE_TIP = {
    working: "This agent is actively running a turn right now.",
    waiting: "The agent is up but idle, waiting for its next message.",
    attention: "The agent has finished and is waiting on your reply.",
    stalled: "Busy past its timeout so its turn-completion signal was likely lost; use Esc or Restart to recover.",
    stopped: "No tmux session; the agent is not processing mail. Start it to bring it online.",
  };

  function statusPills(a) {
    const s = a.state || (a.running ? (a.busy ? "working" : "waiting") : "stopped");
    let label = STATE_LABEL[s] || s;
    if (s === "working" && a.working_s) label = "working " + fmtDur(a.working_s);
    const dot = s === "working" ? '<span class="dotpulse"></span>' : "";
    const stp = `<span class="pill st-${s}" data-tip="${esc(STATE_TIP[s] || STATE_LABEL[s] || s)}">${dot}${esc(label)}</span>`;
    const un = a.unread ? `<span class="pill busy" data-tip="Unread messages in this agent's inbox, waiting to be read and processed.">${a.unread} unread</span>` : "";
    const q = a.queue_depth ? `<span class="pill mute" data-tip="Messages held in the orchestrator queue for this agent; the inbox releases them one at a time.">${a.queue_depth} queued</span>` : "";
    const act = a.running
      ? `<button class="pill downbtn" data-down="${esc(a.name)}">■ Stop</button>${infoIcon("Kill this agent's tmux session and any in-flight turn. Config is untouched.")}`
      : `<button class="pill upbtn" data-up="${esc(a.name)}">▶ Start</button>${infoIcon("Launch this agent's tmux session and run its CLI from the config; it begins reading mail.")}`;
    const compact = a.running
      ? `<button class="pill compactbtn" data-compact="${esc(a.name)}">⊟ Context Compact</button>${infoIcon("Type /compact into this agent's live session and press Enter, asking its CLI to compact its context window. Bypasses mail.")}`
      : "";
    const recover = s === "stalled"
      ? `<button class="pill recover" data-esc="${esc(a.name)}">⎋ Esc</button>${infoIcon("Send a literal Escape keypress into this agent's pane to interrupt a stuck turn. The session is not killed.")}`
        + `<button class="pill recover" data-restart="${esc(a.name)}">↻ Restart</button>${infoIcon("Stop then relaunch this agent's tmux session from the config; any in-flight turn is lost.")}`
      : "";
    return stp + act + compact + recover + un + q;
  }

  function renderAgents() {
    if (timers.status) { clearInterval(timers.status); delete timers.status; }
    if (timers.rate) { clearInterval(timers.rate); delete timers.rate; }
    const agents = (state.status && state.status.agents) || [];
    const cards = agents.map((a) => `
      <div class="card agentcard" data-agent="${esc(a.name)}">
        <div class="top">
          ${avatar(a.name)}
          <div style="min-width:0">
            <div class="name">${esc(a.name)} ${infoIcon("Open this agent: its mail thread with you and its live terminal.")}</div>
            <div class="muted" style="font-size:.8rem" data-tip="The CLI this agent runs, which sets how its turn-completion is detected.">${esc(a.type)}</div>
          </div>
          ${state.rate ? `<span class="rateline" data-rateline="${esc(a.name)}"></span>` : ""}
        </div>
        <div class="role" data-role="${esc(a.name)}" data-tip="This agent's configured role and purpose, from agentainer.yaml.">${esc(a.role_preview || "")}</div>
        <div class="meta">${statusPills(a)}</div>
        <div class="muted" style="font-size:.78rem" data-tip="Agents this one may mail, per the can_talk_to ACL. Cooperative, not an OS boundary.">talks to: ${esc((a.can_talk_to || []).join(", ") || "—")}</div>
      </div>`).join("");
    const notifyTgl = ("Notification" in window)
      ? `<label class="tgl"><input type="checkbox" id="notifyTgl" ${state.notify ? "checked" : ""}/> Notify ${infoIcon("Opt in to a desktop notification when agents go from zero to needing your reply.")}</label>` : "";
    const bulk = agents.length ? `
          <button class="btn ghost sm bulk-btn" id="startAll">Start all</button>${infoIcon("Start every currently stopped agent in this swarm at once.")}
          <button class="btn ghost sm bulk-btn" id="stopAll">Stop all</button>${infoIcon("Stop every running agent at once. Requires confirmation.")}
          <button class="btn ghost sm bulk-btn" id="restartAll">Restart all</button>${infoIcon("Restart all agents at once; running sessions are killed then relaunched. Requires confirmation.")}
          <button class="btn ghost sm" id="compactAllBtn">Compact all</button>${infoIcon("Type /compact into every running agent's session. Requires confirmation.")}` : "";
    const body = cards
      ? `<div class="grid">${cards}</div>`
      : `<div id="emptyAgents"><p class="empty">This swarm has no agents yet. Add one in Settings, or edit its config.</p></div>`;
    $("view").innerHTML = `
      ${subnav("agents")}
      <div class="sectiontitle">
        <h2>${esc(state.swarm)} <span class="muted" style="font-weight:500">· ${agents.length} agent${agents.length === 1 ? "" : "s"}</span></h2>
        <div class="toolrow">
          <label class="tgl"><input type="checkbox" id="rateTgl" ${state.rate ? "checked" : ""}/> Show rate ${infoIcon("Toggle a per-agent messages-per-minute rate (5-minute window) on each card.")}</label>
          ${notifyTgl}
          ${bulk}
          <button class="btn ghost sm" id="refreshBtn">Refresh</button>${infoIcon("Re-fetch agent status now.")}
        </div>
      </div>
      ${topologyCard(agents)}
      ${body}`;
    wireSubnav();
    $("refreshBtn").onclick = pollStatus;
    if ($("startAll")) $("startAll").onclick = () => bulkAction("up");
    if ($("stopAll")) $("stopAll").onclick = () => bulkAction("down");
    if ($("restartAll")) $("restartAll").onclick = () => bulkAction("restart");
    if ($("compactAllBtn")) $("compactAllBtn").onclick = compactAll;
    $("rateTgl").onchange = (e) => toggleRate(e.target.checked);
    if ($("notifyTgl")) $("notifyTgl").onchange = (e) => toggleNotify(e.target.checked);
    if (state.rate) { loadRates(); timers.rate = setInterval(loadRates, 5000); }
    for (const c of document.querySelectorAll(".agentcard"))
      c.onclick = (e) => { if (e.target.closest("[data-up],[data-down],[data-esc],[data-restart],[data-compact]")) return; openAgent(c.dataset.agent); };
    for (const g of document.querySelectorAll(".gnode")) {
      g.onclick = () => openAgent(g.dataset.agent);
      g.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openAgent(g.dataset.agent); } };
    }
    agents.forEach((a) => apiGet("/api/agent" + swq({ agent: a.name }))
      .then((d) => { const n = document.querySelector(`[data-role="${cssq(a.name)}"]`); if (n) n.textContent = (d.agent && d.agent.role) || "(no role set)"; })
      .catch(() => {}));
    timers.status = setInterval(pollStatus, 5000);
  }

  function maybeNotify(attention) {
    const prev = state.lastAttention || 0;
    state.lastAttention = attention;
    if (!state.notify || !("Notification" in window)) return;
    if (attention > 0 && prev === 0 && Notification.permission === "granted") {
      try { new Notification("Agentainer · " + (state.swarm || ""), { body: attention + " message" + (attention > 1 ? "s" : "") + " awaiting your reply" }); } catch (_) {}
    }
  }
  function toggleNotify(on) {
    if (on && "Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().then((p) => {
        if (p !== "granted") { state.notify = false; write("notifyOptIn", false); const t = $("notifyTgl"); if (t) t.checked = false; toast("notifications blocked by the browser"); }
      });
    }
    state.notify = on; write("notifyOptIn", on);
  }
  function toggleRate(on) { state.rate = on; write("showRate", on); if (state.view === "agents") renderAgents(); }
  function loadRates() {
    if (!state.rate) return;
    apiGet("/api/rate" + swq({ window: 5 })).then((d) => {
      state.rates = (d && d.rates) || {};
      for (const node of document.querySelectorAll("[data-rateline]")) { const v = state.rates[node.dataset.rateline] || 0; node.textContent = v.toFixed(1) + "/min"; }
    }).catch(() => {});
  }

  function startAgent(name, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "starting…"; }
    apiPost("/api/up" + swq(), { agent: name }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); if (btn) { btn.disabled = false; btn.textContent = "▶ Start"; } return; }
      toast(res.j.started ? "starting " + name : name + " already running");
      setTimeout(() => { pollStatus(); refreshMailStatus(); }, 800);
    });
  }
  function stopAgent(name, btn) {
    if (!confirm("Stop " + name + "? Its tmux session (and any in-flight turn) will be killed.")) return;
    if (btn) { btn.disabled = true; btn.textContent = "stopping…"; }
    apiPost("/api/down" + swq(), { agent: name }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); if (btn) { btn.disabled = false; btn.textContent = "■ Stop"; } return; }
      toast(res.j.stopped ? "stopped " + name : name + " already down");
      setTimeout(() => { pollStatus(); refreshMailStatus(); }, 400);
    });
  }
  function bulkAction(kind) {
    if (kind === "down" && !confirm("Stop ALL running agents? Each tmux session (and any in-flight turn) will be killed.")) return;
    if (kind === "restart" && !confirm("Restart ALL agents? Running sessions are killed, then every configured agent is relaunched.")) return;
    const btns = Array.from(document.querySelectorAll(".bulk-btn"));
    btns.forEach((b) => { b.disabled = true; });
    let p;
    if (kind === "up") p = apiPost("/api/up_all" + swq(), {}).then((res) => bulkToast(res, "started"));
    else if (kind === "down") p = apiPost("/api/down_all" + swq(), {}).then((res) => bulkToast(res, "stopped"));
    else p = apiPost("/api/down_all" + swq(), {}).then((res) => { if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; } return apiPost("/api/up_all" + swq(), {}).then((r2) => bulkToast(r2, "started", "restarted")); });
    p.catch(() => toast("network error")).finally(() => { btns.forEach((b) => { b.disabled = false; }); pollStatus(); });
  }
  function bulkToast(res, key, verb) {
    if (!res || !res.ok) { toast("error: " + ((res && res.j.error) || "failed")); return; }
    toast((verb || key) + " " + ((res.j[key] || []).length));
  }

  function pollStatus() {
    apiGet("/api/status" + swq()).then((data) => {
      state.status = data;
      banner("");
      syncAvailability(data.user_available);
      maybeNotify(data.attention || 0);
      if (state.view === "agents") {
        const agents = data.agents || [];
        const shown = Array.from(document.querySelectorAll(".agentcard")).map((c) => c.dataset.agent);
        const same = shown.length === agents.length && agents.every((a, i) => shown[i] === a.name);
        if (!same) renderAgents();
        else agents.forEach((a) => { const card = document.querySelector(`.agentcard[data-agent="${cssq(a.name)}"] .meta`); if (card) card.innerHTML = statusPills(a); });
      }
    }).catch((e) => banner(e.message));
  }

  // ---- topology graph ----------------------------------------------------

  function topologyCard(agents) {
    if (!agents.length) return "";
    return `<div class="card panel" style="margin-bottom:1rem">
      <h3 style="margin:0 0 .4rem">Who talks to whom</h3>
      <div style="overflow-x:auto">${drawTopology(agents)}</div></div>`;
  }
  function drawTopology(agents) {
    const byName = {};
    agents.forEach((a) => { byName[a.name] = a; });
    const nodes = agents.map((a) => a.name);
    if (agents.some((a) => (a.can_talk_to || []).includes("user"))) nodes.push("user");
    const W = 560, H = 300, cx = W / 2, cy = H / 2, r = Math.min(W, H) / 2 - 48, N = nodes.length;
    const pos = {};
    nodes.forEach((n, i) => { const ang = -Math.PI / 2 + (i * 2 * Math.PI) / N; pos[n] = { x: cx + r * Math.cos(ang), y: cy + r * Math.sin(ang) }; });
    let edges = "";
    agents.forEach((a) => (a.can_talk_to || []).forEach((p) => {
      if (pos[a.name] && pos[p]) {
        const s = pos[a.name], t = pos[p];
        const dx = t.x - s.x, dy = t.y - s.y, len = Math.hypot(dx, dy) || 1, R = 21;
        const live = byName[p] && byName[p].unread > 0;
        edges += `<line x1="${s.x + (dx / len) * R}" y1="${s.y + (dy / len) * R}" x2="${t.x - (dx / len) * R}" y2="${t.y - (dy / len) * R}" class="edge${live ? " edge-live" : ""}" marker-end="url(#arrow)"><title>Mail route under the can_talk_to ACL; a pulsing arrow means unread mail is waiting.</title></line>`;
      }
    }));
    const circles = nodes.map((n) => {
      const p = pos[n];
      const isUser = n === "user";
      const st = isUser ? null : ((byName[n] && byName[n].state) || "waiting");
      const fill = isUser ? "hsl(215 62% 48%)" : `hsl(${hueFor(n)} 62% 48%)`;
      const ring = (st && st !== "waiting") ? `<circle cx="${p.x}" cy="${p.y}" r="23" fill="none" class="gring gring-${st}"/>` : "";
      const dim = st === "stopped" ? ' opacity="0.55"' : "";
      return `<g${!isUser ? ` class="gnode" data-agent="${esc(n)}" role="button" tabindex="0" aria-label="open ${esc(n)}"` : ""}${dim}>
        <title>${esc("Open " + (n === "user" ? "you" : n))} — colored ring shows live state; a dimmed circle is stopped.</title>
        ${ring}
        <circle cx="${p.x}" cy="${p.y}" r="19" fill="${fill}"/>
        <text x="${p.x}" y="${p.y + 4}" text-anchor="middle" class="gnode-t">${esc(initials(n))}</text>
        <text x="${p.x}" y="${p.y + 36}" text-anchor="middle" class="gnode-l">${esc(n === "user" ? "you" : n)}</text></g>`;
    }).join("");
    return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;min-width:420px;height:auto;color:var(--muted)" role="img" aria-label="agent communication graph">
      <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>
      <g class="edges">${edges}</g>${circles}</svg>`;
  }

  // ---- activity timeline -------------------------------------------------

  const KIND_CLASS = {
    route: "ok", delivered: "ok", "user-send": "ok", read: "mute", "read-receipt": "mute",
    bounce: "no", "rate-limited": "no", ping: "busy", "user-held": "busy",
    "user-available": "ok", "user-away": "mute",
  };
  const KIND_TITLE = {
    route: "The orchestrator routed a message to its next hop along an allowed path.",
    delivered: "A message was placed in the recipient's inbox.",
    "user-send": "You, the operator, sent this message to an agent from the UI.",
    read: "The recipient moved the message to read/, signaling it was processed.",
    "read-receipt": "A read receipt returned to the sender.",
    bounce: "Delivery refused; the recipient is not in the sender's can_talk_to ACL.",
    "rate-limited": "Message dropped; the sender hit the runaway-loop rate cap.",
    ping: "A periodic keep-alive ping to check the agent is alive.",
    "user-held": "A message to you was held because you are marked away.",
    "user-available": "You turned availability on, so mail to you flows again.",
    "user-away": "You turned availability off, so new mail to you is held.",
  };

  function renderActivity() {
    state.activityN = ACTIVITY_PAGE;
    $("view").innerHTML = `
      ${subnav("activity")}
      <div class="sectiontitle">
        <h2>Activity <span class="muted" style="font-weight:500">· ${esc(state.swarm)}</span></h2>
        <button class="btn ghost sm" id="refreshBtn">Refresh</button>${infoIcon("Reload the latest events from the durable JSONL event log.")}
      </div>
      <div class="card" style="padding:.3rem .2rem"><div id="timeline" class="timeline"><p class="empty">Loading…</p></div></div>
      <div class="pager" id="activityPager"></div>`;
    wireSubnav();
    $("refreshBtn").onclick = loadTimeline;
    loadTimeline();
    timers.activity = setInterval(loadTimeline, 5000);
  }
  function loadTimeline() {
    apiGet("/api/logs" + swq({ n: state.activityN })).then((d) => {
      const raw = d.logs || [];
      const logs = raw.slice().reverse();
      const box = $("timeline"); if (!box) return;
      box.innerHTML = logs.map((r) => {
        const kind = r.kind || "?";
        const cls = KIND_CLASS[kind] || "mute";
        const route = [r.from_, r.to].filter(Boolean).join(" → ");
        const extra = [route, r.id, r.reason].filter(Boolean).map(esc).join(" · ");
        return `<div class="event">
          <span class="t">${esc(fmtTime(r.ts))}</span>
          <span class="pill ${cls}" data-tip="${esc(KIND_TITLE[kind] || kind)}">${esc(kind)}</span>
          <b>${esc(r.agent || "")}</b>
          <span class="muted">${extra}</span></div>`;
      }).join("") || '<p class="empty">No events yet.</p>';
      const pager = $("activityPager"); if (!pager) return;
      if (raw.length >= state.activityN) {
        pager.innerHTML = `<button class="btn ghost sm" id="activityMore">Show older events</button>${infoIcon("Load 100 earlier events from the durable JSONL log.")}`;
        $("activityMore").onclick = () => { state.activityN += ACTIVITY_PAGE; loadTimeline(); };
      } else {
        pager.innerHTML = `<span class="muted">${raw.length} event${raw.length === 1 ? "" : "s"} · all shown</span>`;
      }
    }).catch((e) => banner(e.message));
  }

  // ---- mail app (agent detail) -------------------------------------------

  function openAgent(name) {
    state.agent = name;
    state.peer = "user";
    state.tab = "mail";
    state.threadShown = THREAD_PAGE;
    go("mail");
  }
  function orderedContacts(contacts) {
    const rank = (c) => (c.name === "user" ? 0 : c.kind === "agent" ? 1 : 2);
    return contacts.slice().sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  }

  function renderMail() {
    clearTimers();
    const agent = state.agent;
    $("view").innerHTML = `
      <div class="mailhead">
        <button class="btn ghost sm" id="backBtn">← Agents</button>${infoIcon("Go back to this swarm's Agents overview.")}
        <div class="who">${avatar(agent, "sm")}<h2>${esc(agent)}</h2><span id="mailStatus"></span></div>
        <span style="flex:1"></span>
        <div class="tabs">
          <button class="tab ${state.tab === "mail" ? "active" : ""}" data-tab="mail">Mail</button>${infoIcon("Browse contacts and exchange file-based mail (inbox, outbox, read) with this agent.")}
          <button class="tab ${state.tab === "terminal" ? "active" : ""}" data-tab="terminal">Terminal</button>${infoIcon("Live tmux pane plus direct keystrokes into this session, bypassing mail.")}
        </div>
      </div>
      <div id="agentBody"></div>`;
    $("backBtn").onclick = () => go("agents");
    for (const b of document.querySelectorAll(".tab")) b.onclick = () => { state.tab = b.dataset.tab; renderMail(); };
    refreshMailStatus();
    if (state.tab === "terminal") renderTerminalTab();
    else renderMailTab();
  }

  function renderMailTab() {
    document.body.dataset.pane = "list";
    $("agentBody").innerHTML = `
      <div class="mail">
        <div class="card contacts" id="contacts"></div>
        <div class="thread-wrap">
          <div class="card thread">
            <div class="scroll" id="threadScroll"><p class="empty">Select a contact.</p></div>
            <div id="composeArea"></div>
          </div>
        </div>
      </div>`;
    loadContacts();
    loadThread();
    timers.mail = setInterval(() => { loadContacts(); loadThread(); refreshMailStatus(); }, 4000);
  }

  function renderTerminalTab() {
    document.body.dataset.pane = "thread";
    $("agentBody").innerHTML = `
      <div class="card terminal">
        <div class="thead">
          <b>Live terminal · ${esc(state.agent)}</b>
          <label class="wrapchk"><input type="checkbox" id="wrapPane" ${state.wrap ? "checked" : ""}/> Wrap text ${infoIcon("Wrap long lines in the captured pane snapshot; display only.")}</label>
          <span class="muted" style="font-size:.78rem">capture-pane · refreshes 2s</span>
        </div>
        <pre class="pane${state.wrap ? " wrap" : ""}" id="pane">— loading —</pre>
        <div class="keyrow">${KEYS.map((k) => `<button class="keybtn" data-key="${esc(k.key)}" title="${esc(k.title)}">${esc(k.label)}</button>`).join("")}</div>
        <div class="typerow">
          <input class="field" id="typeText" placeholder="Type straight into ${esc(state.agent)}'s session, press Enter…" aria-label="Type into the agent session"/>${infoIcon("Text fed directly into the agent's tmux pane as live keystrokes; bypasses mail.")}
          <button class="btn" id="typeSend">Send</button>
        </div>
        <p class="muted" style="font-size:.8rem;margin:.5rem 0 0">Types directly into the tmux pane (bypasses mail). An empty pane means the session isn't running.</p>
      </div>`;
    const inp = $("typeText");
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); sendType(); } });
    $("typeSend").onclick = sendType;
    for (const b of document.querySelectorAll(".keybtn")) b.onclick = () => sendKey(b.dataset.key);
    $("wrapPane").onchange = (e) => { state.wrap = e.target.checked; write("paneWrap", state.wrap); $("pane").classList.toggle("wrap", state.wrap); };
    loadPane();
    timers.pane = setInterval(() => { loadPane(); refreshMailStatus(); }, 2000);
  }

  function refreshMailStatus() {
    apiGet("/api/agent" + swq({ agent: state.agent })).then((d) => {
      const a = d.agent; if (!a) return;
      const s = $("mailStatus"); if (s) s.innerHTML = statusPills(a);
    }).catch(() => {});
  }
  function loadContacts() {
    apiGet("/api/contacts" + swq({ agent: state.agent })).then((d) => { state.contacts = orderedContacts(d.contacts || []); renderContacts(); }).catch((e) => banner(e.message));
  }
  function renderContacts() {
    const box = $("contacts"); if (!box) return;
    box.innerHTML = state.contacts.map((c) => {
      const label = c.name === "user" ? "You (operator)" : c.name;
      const time = c.last_time ? `<span class="t" data-tip="Time of the most recent message with this contact.">${esc(fmtTime(c.last_time))}</span>` : "";
      const badge = c.unread ? `<span class="badge" data-tip="Unread messages from this contact still in your inbox.">${c.unread}</span>` : "";
      return `
        <div class="contact ${c.name === state.peer ? "active" : ""}" data-peer="${esc(c.name)}">
          ${avatar(c.name, "sm")}
          <div class="info2">
            <div class="cn"><b>${esc(label)}</b>${infoIcon("Open this contact's message thread.")}${time}</div>
            <div class="prev">${esc(c.last_preview || (c.count ? "" : "no messages yet"))}</div>
          </div>
          ${badge}
        </div>`;
    }).join("") || '<p class="empty">No contacts.</p>';
    for (const node of box.querySelectorAll(".contact")) node.onclick = () => selectPeer(node.dataset.peer);
  }
  function selectPeer(peer) {
    state.peer = peer;
    state.threadShown = THREAD_PAGE;
    document.body.dataset.pane = "thread";
    renderContacts();
    loadThread();
  }
  function loadThread() {
    if (!state.peer) return;
    apiGet("/api/thread" + swq({ agent: state.agent, peer: state.peer }))
      .then((d) => { state.thread = d.messages || []; renderThread(); })
      .catch((e) => banner(e.message));
  }
  function renderThread() {
    const scroll = $("threadScroll"); if (!scroll) return;
    const sig = JSON.stringify([state.threadShown, state.thread.map((m) => [m.from, m.to, m.time, m.status, m.body])]);
    if (scroll.dataset.sig === sig) { renderCompose(); return; }
    scroll.dataset.sig = sig;
    const newPeer = scroll.dataset.peer !== state.peer;
    scroll.dataset.peer = state.peer;
    const atBottom = newPeer || scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 60;
    if (!state.thread.length) {
      scroll.innerHTML = `<p class="empty">No messages between <b>${esc(state.agent)}</b> and <b>${esc(state.peer === "user" ? "you" : state.peer)}</b> yet.</p>`;
    } else {
      const start = Math.max(0, state.thread.length - state.threadShown);
      const earlier = start > 0
        ? `<button class="m-earlier" type="button">Show earlier messages (${start})</button>${infoIcon("Reveal older messages further back in this conversation.")}` : "";
      scroll.innerHTML = earlier + state.thread.slice(start).map((m) => {
        const cls = m.from === "system" ? "system" : m.direction === "out" ? "out" : "in";
        const head = cls === "system" ? "system" : `${esc(m.from)} → ${esc(m.to)} · ${esc(fmtTime(m.time))}`;
        const status = cls === "system" ? "" : statusTag(m.status);
        return `<div class="msg ${cls}"><div class="m-head">${head}</div>${mailBody(m.body)}${status}</div>`;
      }).join("");
      const eb = scroll.querySelector(".m-earlier");
      if (eb) eb.onclick = () => { state.threadShown += THREAD_PAGE; renderThread(); };
      for (const btn of scroll.querySelectorAll(".m-more")) btn.onclick = () => toggleCollapse(btn);
    }
    if (atBottom) scroll.scrollTop = scroll.scrollHeight;
    renderCompose();
  }

  const COLLAPSE_LINES = 10;
  function mailBody(raw) {
    const src = (raw || "").trim();
    const lines = src.split("\n");
    if (lines.length <= COLLAPSE_LINES) return `<div class="m-body">${md(src)}</div>`;
    const hidden = lines.length - COLLAPSE_LINES;
    const short = md(lines.slice(0, COLLAPSE_LINES).join("\n"));
    const full = md(src);
    return `<div class="m-body collapsible" data-collapsed="1">`
      + `<div class="m-short">${short}</div>`
      + `<div class="m-full" hidden>${full}</div>`
      + `<button class="m-more" type="button" data-more="${hidden}">Show ${hidden} more line${hidden > 1 ? "s" : ""}</button>${infoIcon("Expand this long message to reveal its hidden lines.")}`
      + `</div>`;
  }
  function toggleCollapse(btn) {
    const box = btn.closest(".collapsible"); if (!box) return;
    const collapse = box.dataset.collapsed !== "1";
    box.dataset.collapsed = collapse ? "1" : "0";
    box.querySelector(".m-short").hidden = !collapse;
    box.querySelector(".m-full").hidden = collapse;
    const n = btn.dataset.more;
    btn.textContent = collapse ? `Show ${n} more line${n > 1 ? "s" : ""}` : "Show less";
  }
  function statusTag(s) {
    const map = { queued: ["◷", "waiting"], delivered: ["✓", "delivered"], read: ["✓✓", "read"], archived: ["⤓", "archived"] };
    const S_TIP = {
      queued: "Written to the outbox but not yet delivered to the recipient's inbox.",
      delivered: "Delivered to the recipient's inbox; not yet confirmed read.",
      read: "The recipient opened it and moved it to read/, confirming a read receipt.",
      archived: "Archived out of the active inbox; the conversation is finished.",
    };
    const e = map[s];
    const tip = e && S_TIP[s] ? ` data-tip="${esc(S_TIP[s])}"` : "";
    return e ? `<div class="m-status s-${s}"${tip}>${e[0]} ${e[1]}</div>` : "";
  }
  function renderCompose() {
    const area = $("composeArea"); if (!area) return;
    const mode = state.peer === "user" ? "compose" : "note";
    if (area.dataset.mode === mode) return;
    area.dataset.mode = mode;
    if (mode === "compose") {
      area.innerHTML = `
        <div class="compose">
          <textarea class="field" id="reply" rows="1" placeholder="Message ${esc(state.agent)} as the user…" aria-label="Message as user"></textarea>${infoIcon("Write a message to this agent as the operator; Ctrl/Cmd+Enter or Send delivers it as file-based mail to the agent's inbox, not into the tmux pane.")}
          <button class="btn" id="sendReply">Send</button>${infoIcon("Deliver the message above as file-based mail; it lands in the agent's inbox and does not interrupt a running turn.")}
        </div>`;
      const ta = $("reply");
      ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 240) + "px"; });
      ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); sendReply(); } });
      $("sendReply").onclick = sendReply;
    } else {
      area.innerHTML = `<div class="composenote">You correspond as the <b>user</b>. Open <b>You (operator)</b> to write to ${esc(state.agent)}, or use <b>Terminal</b> to type straight into its session.</div>`;
    }
  }
  function sendReply() {
    const text = $("reply").value;
    if (!text.trim()) return;
    apiPost("/api/send" + swq(), { to: state.agent, text }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      $("reply").value = ""; $("reply").style.height = "auto";
      toast("sent to " + state.agent);
      loadThread(); loadContacts();
    });
  }

  // ---- terminal (live pane + direct type-in) -----------------------------

  const KEYS = [
    { key: "Escape", label: "Esc", title: "Send Escape; can dismiss a trust/permission modal or interrupt a live turn." },
    { key: "Enter", label: "Enter", title: "Send Enter/Return; submits the agent's current input line." },
    { key: "Tab", label: "Tab", title: "Send Tab; triggers CLI tab-completion." },
    { key: "Up", label: "↑", title: "Send the Up arrow; recalls the previous command." },
    { key: "Down", label: "↓", title: "Send the Down arrow; steps forward through history." },
    { key: "Left", label: "←", title: "Send the Left arrow." },
    { key: "Right", label: "→", title: "Send the Right arrow." },
    { key: "C-c", label: "Ctrl-C", title: "Send Ctrl-C; interrupts the running command or a live turn." },
    { key: "C-u", label: "Ctrl-U", title: "Send Ctrl-U; clears the current input line." },
    { key: "C-l", label: "Ctrl-L", title: "Send Ctrl-L; repaints the terminal." },
  ];

  function sendKey(key, agent) {
    const name = agent || state.agent;
    apiPost("/api/key" + swq(), { agent: name, key }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast(key + " → " + name);
      if (name === state.agent) setTimeout(loadPane, 300);
    });
  }
  function restartAgent(name) {
    toast("restarting " + name + "…");
    apiPost("/api/down" + swq(), { agent: name }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      apiPost("/api/up" + swq(), { agent: name }).then((r2) => {
        if (!r2.ok) { toast("error: " + (r2.j.error || "failed")); return; }
        toast("restarted " + name);
        setTimeout(() => { pollStatus(); refreshMailStatus(); }, 800);
      });
    });
  }
  const COMPACT_CMD = "/compact";
  function compactAgent(name, btn) {
    if (btn) btn.disabled = true;
    apiPost("/api/type" + swq(), { agent: name, text: COMPACT_CMD }).then((res) => {
      if (btn) btn.disabled = false;
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast(res.j.ok ? "compacting " + name : "sent /compact to " + name + " (not confirmed)");
      if (name === state.agent && state.tab === "terminal") setTimeout(loadPane, 400);
    });
  }
  function compactAll() {
    const agents = ((state.status && state.status.agents) || []).filter((a) => a.running);
    if (!agents.length) { toast("no running agents to compact"); return; }
    if (!confirm("Send /compact to ALL " + agents.length + " running agent" + (agents.length > 1 ? "s" : "") + "?")) return;
    const btn = $("compactAllBtn"); if (btn) btn.disabled = true;
    Promise.all(agents.map((a) => apiPost("/api/type" + swq(), { agent: a.name, text: COMPACT_CMD }).catch(() => null)))
      .then((results) => { const ok = results.filter((r) => r && r.ok).length; toast("sent /compact to " + ok + "/" + agents.length + " agents"); })
      .finally(() => { if (btn) btn.disabled = false; });
  }
  function loadPane() {
    apiGet("/api/pane" + swq({ agent: state.agent })).then((d) => {
      const p = $("pane"); if (!p) return;
      const atBottom = p.scrollHeight - p.scrollTop - p.clientHeight < 40;
      p.textContent = d.pane || "— (empty / session down) —";
      if (atBottom) p.scrollTop = p.scrollHeight;
    }).catch(() => {});
  }
  function sendType() {
    const text = $("typeText").value;
    if (!text.trim()) return;
    apiPost("/api/type" + swq(), { agent: state.agent, text }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      $("typeText").value = "";
      toast(res.j.ok ? "typed into " + state.agent : "sent (not confirmed in pane)");
      setTimeout(loadPane, 400);
    });
  }

  // ---- settings (per-swarm essentials + advanced + global Telegram) -------

  const ADV_SCHEMA = [
    ["session_prefix", "tmux session prefix", "text"],
    ["supervise", "Liveness supervisor", "bool"],
    ["supervise_interval_ms", "Supervise interval (ms)", "num"],
    ["ready_timeout_ms", "Ready timeout (ms)", "num"],
    ["busy_timeout_ms", "Busy timeout (ms)", "num"],
    ["resume", "Resume sessions on up", "bool"],
    ["pane_idle_ms", "Pane idle (ms)", "num"],
    ["pane_poll_ms", "Pane poll (ms)", "num"],
    ["pane_scrollback", "Pane scrollback lines", "num"],
    ["enter_delay_ms", "Enter delay (ms)", "num"],
    ["send_delay_ms", "Send delay (ms)", "num"],
    ["tmux_mouse", "tmux mouse mode", "bool"],
  ];
  const SWARM_DEFAULTS = {
    supervise: true, supervise_interval_ms: 15000, ready_timeout_ms: 60000,
    busy_timeout_ms: 900000, resume: false, pane_idle_ms: 2500, pane_poll_ms: 700,
    pane_scrollback: 400, enter_delay_ms: 250, send_delay_ms: 150, tmux_mouse: true,
    session_prefix: "", name: "", root: "",
  };

  function fieldInput(key, label, type, sw) {
    const val = key in sw ? sw[key] : SWARM_DEFAULTS[key];
    if (type === "bool")
      return `<div class="fld"><label>${esc(label)}</label>
        <label class="row"><input type="checkbox" data-swarm="${key}" ${val ? "checked" : ""}/> <span class="muted">${key}</span></label></div>`;
    return `<div class="fld"><label>${esc(label)}</label>
      <input class="field" data-swarm="${key}" type="${type === "num" ? "number" : "text"}" value="${esc(val == null ? "" : val)}"/></div>`;
  }

  function renderSettings() {
    Promise.all([
      apiGet("/api/config" + swq()),
      apiGet("/api/settings").catch(() => null),
    ]).then(([cfg, settings]) => {
      state.config = cfg;
      state.settings = settings || state.settings;
      const sw = cfg.swarm || {};
      const essentials = `
        <div class="formgrid">
          ${fieldInput("name", "Swarm name", "text", sw)}
          ${fieldInput("root", "Workspace root", "text", sw)}
        </div>
        <div class="fld" style="margin-top:.8rem">
          <label>You (operator) availability</label>
          <label class="row"><input type="checkbox" id="setAvail" ${cfg.user_available ? "checked" : ""}/> <span class="muted">available to receive mail from agents</span></label>
        </div>`;
      const adv = ADV_SCHEMA.map(([k, l, t]) => fieldInput(k, l, t, sw)).join("");
      const agents = (cfg.agents || []).map((a) => `
        <div class="agentrow">
          ${avatar(String(a.name), "sm")}
          <div class="info2">
            <b>${esc(a.name)}</b> <span class="muted">${esc(a.type || "claude")}</span>
            <div class="muted" style="font-size:.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">talks to: ${esc(fmtCanTalk(a.can_talk_to))}</div>
          </div>
          <button class="btn ghost sm" data-edit="${esc(a.name)}">Edit</button>${infoIcon("Edit this agent; its name is locked since renaming would orphan its mailbox folders.")}
          <button class="btn danger sm" data-del="${esc(a.name)}">Delete</button>${infoIcon("Stop this agent's session and remove it from the config. Irreversible.")}
        </div>`).join("");
      $("view").innerHTML = `
        ${subnav("settings")}
        <div class="settings">
          <div class="card panel">
            <h3>Swarm essentials</h3>
            <p class="muted" style="margin-top:.1rem">Saved straight to <code>${esc(cfg.path || "agentainer.yaml")}</code>.</p>
            ${essentials}
            <div class="rowend"><button class="btn" id="saveSwarm">Save</button>${infoIcon("Write the essentials above to agentainer.yaml.")}</div>
          </div>
          <details class="adv">
            <summary>Configure advanced settings</summary>
            <div class="adv-body">
              <p class="muted">Defaults work out of the box. Change these only if you know why. Timeout/supervise changes apply on the next start.</p>
              <div class="formgrid">${adv}</div>
              <div class="rowend"><button class="btn" id="saveAdv">Save advanced</button></div>
            </div>
          </details>
          <div class="card panel">
            <div class="sectiontitle" style="margin:0 0 .6rem"><h3>Agents</h3><button class="btn sm" id="addAgent">+ Add agent</button>${infoIcon("Define a new agent; it is created and its session launched on save.")}</div>
            ${agents || '<p class="muted">No agents yet.</p>'}
          </div>
          ${globalTelegramCard(state.settings)}
        </div>`;
      wireSubnav();
      $("saveSwarm").onclick = () => saveSwarm(["name", "root"], true);
      $("saveAdv").onclick = () => saveSwarm(ADV_SCHEMA.map((r) => r[0]), false);
      $("addAgent").onclick = () => openAgentForm(null);
      for (const b of document.querySelectorAll("[data-edit]")) b.onclick = () => openAgentForm(b.dataset.edit);
      for (const b of document.querySelectorAll("[data-del]")) b.onclick = () => deleteAgent(b.dataset.del);
      wireGlobalTelegram();
    }).catch((e) => banner(e.message));
  }

  function saveSwarm(keys, includeAvail) {
    const swarm = {};
    for (const node of document.querySelectorAll("[data-swarm]")) {
      const key = node.dataset.swarm;
      if (keys && !keys.includes(key)) continue;
      if (node.type === "checkbox") swarm[key] = node.checked;
      else if (node.type === "number") { if (node.value !== "") swarm[key] = Number(node.value); }
      else if (node.value !== "") swarm[key] = node.value;
    }
    const availNode = $("setAvail");
    const chain = includeAvail && availNode
      ? apiPost("/api/availability" + swq(), { available: availNode.checked })
      : Promise.resolve({ ok: true, j: {} });
    chain.then(() => apiPost("/api/config" + swq(), { swarm })).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast("settings saved");
      // A rename changes the swarm key; follow it so the switcher/status stay valid.
      if (swarm.name && swarm.name !== state.swarm) { state.swarm = swarm.name; }
      renderSwitcher();
    });
  }

  function deleteAgent(name) {
    if (!confirm(`Delete agent "${name}"? This stops its session and removes it from the config.`)) return;
    apiPost("/api/agent/remove" + swq(), { name }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast("removed " + name);
      renderSettings();
    });
  }
  function fmtCanTalk(v) { return Array.isArray(v) ? v.join(", ") : (v || "—"); }

  // ---- global Telegram (the ONE shared bot; /api/settings) ---------------

  function renderTelegramNudge() {
    if (!state.settings) return;
    let dismissed = false; try { dismissed = !!localStorage.getItem(TG_NUDGE_KEY); } catch (_) {}
    // Nudge lives at the top of the dashboard body; only when connected + disabled.
    const existing = $("tgNudge"); if (existing) existing.remove();
    if (dismissed || state.settings.telegram_enabled) return;
    const view = $("view"); if (view.hidden) return;
    const nudge = el(`
      <div class="nudge" id="tgNudge">
        <span aria-hidden="true">📲</span>
        <span>Enable Telegram to control your swarms from anywhere — mirror mail and reply from your phone.</span>
        <span class="nx">
          <button class="btn sm" id="tgNudgeGo">Set up</button>
          <button class="close" id="tgNudgeX" aria-label="Dismiss">✕</button>
        </span>
      </div>`);
    view.insertBefore(nudge, view.firstChild);
    $("tgNudgeGo").onclick = () => {
      try { localStorage.setItem(TG_NUDGE_KEY, "1"); } catch (_) {}
      nudge.remove();
      if (!state.swarm && state.swarms[0]) state.swarm = state.swarms[0].name;
      if (state.swarm) go("settings"); else toast("create a swarm first, then open Settings");
    };
    $("tgNudgeX").onclick = () => { try { localStorage.setItem(TG_NUDGE_KEY, "1"); } catch (_) {} nudge.remove(); };
  }

  function globalTelegramCard(s) {
    const tg = (s && s.telegram) || {};
    const enabled = !!(s && s.telegram_enabled);
    const hasToken = !!tg.has_token;
    const allScope = tg.mirror === "*" || (Array.isArray(tg.mirror) && tg.mirror.includes("*"));
    return `
      <div class="card panel">
        <div class="sectiontitle" style="margin:0 0 .4rem"><h3>Telegram <span class="muted" style="font-weight:500">· shared by all swarms</span></h3>
          <span class="pill ${enabled ? "ok" : "mute"}">${enabled ? "on" : "off"}</span></div>
        <p class="muted" style="margin:.1rem 0 .6rem">One bot for every swarm on this machine. Mirror mail to a Telegram chat and reply from your phone. Create a bot with <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer">@BotFather</a>, then get your numeric chat id from <a href="https://t.me/userinfobot" target="_blank" rel="noopener noreferrer">@userinfobot</a>.</p>
        <div class="formgrid">
          <div class="fld"><label>Enabled</label><label class="row"><input type="checkbox" id="tg_enabled" ${enabled ? "checked" : ""}/> <span class="muted">bridge on ${infoIcon("Turn the shared Telegram bridge on or off for every swarm.")}</span></label></div>
          <div class="fld"><label>Bot token ${infoIcon("The secret bot token from BotFather; leave blank to keep the stored token, or enter a new one to replace it.")}</label><input class="field" id="tg_token" type="password" placeholder="${hasToken ? "•••• stored — blank keeps it" : "123456:ABC-DEF…"}"/></div>
          <div class="fld"><label>Chat ID ${infoIcon("Your numeric Telegram chat id from userinfobot.")}</label><input class="field" id="tg_chat" value="${esc(tg.chat_id || "")}" placeholder="e.g. 123456789"/></div>
          <div class="fld"><label>Mirror your mail</label><label class="row"><input type="checkbox" id="tg_muser" ${tg.mirror_user ? "checked" : ""}/> <span class="muted">mail to you ${infoIcon("Forward messages addressed to your user mailbox to Telegram.")}</span></label></div>
          <div class="fld"><label>Mirror system</label><label class="row"><input type="checkbox" id="tg_msys" ${tg.mirror_system ? "checked" : ""}/> <span class="muted">pings/bounces ${infoIcon("Forward system pings and ACL bounces to Telegram too.")}</span></label></div>
          <div class="fld"><label>Scope</label><label class="row"><input type="checkbox" id="tg_all" ${allScope ? "checked" : ""}/> <span class="muted">mirror all agents ${infoIcon("Mirror mail from every agent in every swarm.")}</span></label></div>
        </div>
        <div class="rowend">
          <button class="btn ghost" id="tg_test">Send test</button>${infoIcon("Save current settings then post a test message to verify the token and chat id work. Needs an active swarm.")}
          <button class="btn" id="tg_save">Save Telegram</button>${infoIcon("Persist the shared bot token, chat id, and mirror settings.")}
        </div>
      </div>`;
  }

  function wireGlobalTelegram() {
    if (!$("tg_save")) return;
    $("tg_save").onclick = saveGlobalTelegram;
    $("tg_test").onclick = testGlobalTelegram;
  }
  function collectGlobalTelegram() {
    const tg = {
      enabled: $("tg_enabled").checked,
      chat_id: $("tg_chat").value.trim(),
      mirror: $("tg_all").checked ? "*" : (Array.isArray(state.settings && state.settings.telegram && state.settings.telegram.mirror) ? state.settings.telegram.mirror : []),
      mirror_user: $("tg_muser").checked,
      mirror_system: $("tg_msys").checked,
    };
    const tok = $("tg_token").value.trim();
    if (tok) tg.bot_token = tok;
    return tg;
  }
  function saveGlobalTelegram() {
    apiPost("/api/settings", { telegram: collectGlobalTelegram() }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      state.settings = res.j;
      toast("Telegram settings saved");
      renderSettings();
    });
  }
  function testGlobalTelegram() {
    // Persist first, then trigger the per-swarm test endpoint (the shared bot is
    // merged into every swarm's config, so any active swarm can send the test).
    apiPost("/api/settings", { telegram: collectGlobalTelegram() }).then(() => {
      if (!state.swarm) { toast("open a swarm first to send a test"); return; }
      apiPost("/api/telegram/test" + swq(), {}).then((res) => {
        toast(res.ok ? "test message sent" : "error: " + (res.j.error || "failed"));
      });
    });
  }

  // ---- add / edit agent modal -------------------------------------------

  const AGENT_TYPES = ["claude", "codex", "gemini", "hermes"];
  function openAgentForm(name) {
    const editing = !!name;
    const a = editing ? (state.config.agents || []).find((x) => String(x.name) === name) || {} : {};
    const typeOpts = AGENT_TYPES.map((t) => `<option value="${t}" ${((a.type || "claude") === t) ? "selected" : ""}>${t}</option>`).join("");
    const captureOpts = ["auto", "hook", "pane", "none"].map((c) => `<option value="${c}" ${((a.capture || "auto") === c) ? "selected" : ""}>${c}</option>`).join("");
    const modal = el(`
      <div class="modal-back"><div class="card modal">
        <h3>${editing ? "Edit " + esc(name) : "Add agent"}</h3>
        <div class="formgrid">
          <div class="fld"><label>Name ${infoIcon("Unique mailbox name; cannot be user or system. Locked when editing.")}</label><input class="field" id="f_name" value="${esc(a.name || "")}" ${editing ? "disabled" : ""} placeholder="developer"/></div>
          <div class="fld"><label>Type ${infoIcon("Which CLI this agent runs. It MUST match the Command below or turn-completion never fires and the agent deadlocks.")}</label><select class="field" id="f_type">${typeOpts}</select></div>
          <div class="fld"><label>Capture ${infoIcon("How turn-completion is detected: auto, hook, pane, or none. Leave on auto unless you know why to change it.")}</label><select class="field" id="f_capture">${captureOpts}</select></div>
        </div>
        <div class="fld" style="margin-top:.6rem"><label>Command (may embed secrets — stays local) ${infoIcon("Full shell command launching the CLI. Must launch the CLI named in Type or the agent deadlocks.")}</label><input class="field" id="f_command" value="${esc(a.command || "")}" placeholder="claude --dangerously-skip-permissions"/></div>
        <div class="fld" style="margin-top:.6rem"><label>Can talk to (comma list, or *) ${infoIcon("Comma-separated agents this one may message, or * for all. Add user to let it reach you.")}</label><input class="field" id="f_talk" value="${esc(fmtCanTalk(a.can_talk_to))}" placeholder="orchestrator, user"/></div>
        <div class="fld" style="margin-top:.6rem"><label>Workdir (optional) ${infoIcon("Directory the agent runs in; blank uses the default. Pre-trusted on first launch.")}</label><input class="field" id="f_workdir" value="${esc(a.workdir || "")}" placeholder="leave blank for default"/></div>
        <div class="fld" style="margin-top:.6rem"><label>Role / standing instructions ${infoIcon("Standing persona and job baked into the first prompt.")}</label><textarea class="field" id="f_role" rows="4" placeholder="You are the developer…">${esc(a.role || "")}</textarea></div>
        <div class="fld" style="margin-top:.6rem">
          <label>Scheduled pings <span class="muted" style="font-weight:500">— cron message per schedule, server-local time.</span></label>
          <div id="f_pings"></div>
          <button class="btn ghost sm" id="f_addping" type="button">+ Add schedule</button>${infoIcon("Add a cron-scheduled ping: a message sent on a schedule.")}
        </div>
        <div class="rowend">
          <button class="btn ghost" id="f_cancel">Cancel</button>
          <button class="btn" id="f_save">${editing ? "Save" : "Add agent"}</button>${infoIcon("Validate and create or update the agent, then start its session. A type/command mismatch is rejected.")}
        </div>
      </div></div>`);
    $("modalRoot").appendChild(modal);
    (a.pings || []).forEach(addPingRow);
    modal.querySelector("#f_addping").onclick = () => addPingRow({});
    const close = () => modal.remove();
    modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
    modal.querySelector("#f_cancel").onclick = close;
    modal.querySelector("#f_save").onclick = () => saveAgentForm(editing, name, close);
  }

  const BUSY_OPTS = ["skip", "queue"];
  function addPingRow(rule) {
    const box = $("f_pings"); if (!box) return;
    const busy = BUSY_OPTS.map((o) => `<option value="${o}" ${((rule.when_busy || "skip") === o) ? "selected" : ""}>${o}</option>`).join("");
    const row = el(`
      <div class="pingrow">
        <input class="field ping-msg" value="${esc(rule.message || "")}" placeholder="Ping message…"/>${infoIcon("What the agent is told when this schedule fires.")}
        <input class="field ping-cron" value="${esc(rule.cron || "")}" placeholder="*/30 9-18 * * 1-5"/>${infoIcon("5-field cron: minute hour day-of-month month day-of-week (server-local time).")}
        <select class="field ping-busy">${busy}</select>${infoIcon("If the agent is mid-turn: skip (drop it) or queue (wait for the turn to end).")}
        <button class="btn ghost sm ping-del" type="button">✕</button>${infoIcon("Remove this schedule")}
      </div>`);
    row.querySelector(".ping-del").onclick = () => row.remove();
    box.appendChild(row);
  }
  function collectPings() {
    const out = [];
    for (const row of document.querySelectorAll("#f_pings .pingrow")) {
      const message = row.querySelector(".ping-msg").value.trim();
      const cron = row.querySelector(".ping-cron").value.trim();
      const when_busy = row.querySelector(".ping-busy").value;
      if (!message && !cron) continue;
      out.push({ message, cron, when_busy });
    }
    return out;
  }
  function parseTalk(raw) {
    raw = (raw || "").trim();
    if (raw === "*") return "*";
    return raw.split(",").map((s) => s.trim()).filter(Boolean);
  }
  function saveAgentForm(editing, name, close) {
    const g = (id) => document.getElementById(id).value;
    const payload = {
      type: g("f_type"),
      command: g("f_command").trim(),
      can_talk_to: parseTalk(g("f_talk")),
      role: g("f_role"),
      capture: g("f_capture"),
      pings: collectPings(),
    };
    const workdir = g("f_workdir").trim();
    if (workdir) payload.workdir = workdir;
    let req;
    if (editing) {
      req = apiPost("/api/agent/edit" + swq(), { name, fields: payload });
    } else {
      const n = g("f_name").trim();
      if (!n) { toast("name is required"); return; }
      if (!payload.command) { toast("command is required"); return; }
      req = apiPost("/api/agent/add" + swq(), Object.assign({ name: n }, payload));
    }
    req.then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast(editing ? "saved " + name : "added " + res.j.name);
      close(); renderSettings();
    });
  }

  // ---- availability (header toggle) --------------------------------------

  function toggleAvailability() {
    if (!state.swarm) return;
    const val = $("availToggle").checked;
    apiPost("/api/availability" + swq(), { available: val }).then((res) => {
      if (!res.ok) { $("availToggle").checked = !val; toast("error: " + (res.j.error || "failed")); return; }
      syncAvailability(val);
      toast(val ? "you're available for mail" : "you're away");
    });
  }

  // ---- wire up -----------------------------------------------------------

  $("connect").addEventListener("click", connect);
  $("token").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
  function goHome() { if (!$("switcher").hidden) { state.swarm = null; go("dashboard"); } }
  $("brand").addEventListener("click", goHome);
  $("brand").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goHome(); } });
  $("availToggle").addEventListener("change", toggleAvailability);

  // Delegated: Start/Stop/recovery/compact pills are re-created by polls.
  document.addEventListener("click", (e) => {
    if (!e.target.closest) return;
    const up = e.target.closest("[data-up]");
    if (up) { e.stopPropagation(); startAgent(up.dataset.up, up); return; }
    const down = e.target.closest("[data-down]");
    if (down) { e.stopPropagation(); stopAgent(down.dataset.down, down); return; }
    const escBtn = e.target.closest("[data-esc]");
    if (escBtn) { e.stopPropagation(); sendKey("Escape", escBtn.dataset.esc); return; }
    const restart = e.target.closest("[data-restart]");
    if (restart) { e.stopPropagation(); restartAgent(restart.dataset.restart); return; }
    const compact = e.target.closest("[data-compact]");
    if (compact) { e.stopPropagation(); compactAgent(compact.dataset.compact, compact); }
  });

  initTips();
  tryStoredToken();

  // ---- delegated ⓘ tooltip engine ----------------------------------------
  function initTips() {
    let pop = null, curr = null, pinned = false;
    const node = () => { if (!pop) { pop = document.createElement("div"); pop.className = "tip-pop"; document.body.appendChild(pop); } return pop; };
    function place(elm) {
      const p = node(), r = elm.getBoundingClientRect();
      p.style.left = "-9999px"; p.style.top = "0px";
      p.classList.add("show");
      const pw = p.offsetWidth, ph = p.offsetHeight, gap = 8;
      let left = r.left + r.width / 2 - pw / 2;
      let top = r.bottom + gap;
      if (top + ph > innerHeight - gap) top = r.top - ph - gap;
      left = Math.max(gap, Math.min(left, innerWidth - pw - gap));
      top = Math.max(gap, top);
      p.style.left = left + "px"; p.style.top = top + "px";
    }
    function show(elm, pin) {
      const t = elm.getAttribute("data-tip");
      if (!t) return;
      const p = node();
      p.textContent = t;
      if (pin) { const hint = document.createElement("span"); hint.className = "tip-hint"; hint.textContent = "Tap outside or press Esc to close"; p.appendChild(hint); }
      p.classList.toggle("pinned", !!pin);
      curr = elm; place(elm);
      if (elm.classList.contains("info")) elm.classList.add("open");
    }
    function hide() {
      if (pop) pop.classList.remove("show", "pinned");
      if (curr && curr.classList) curr.classList.remove("open");
      curr = null; pinned = false;
    }
    const tipTarget = (t) => (t && t.closest) ? t.closest("[data-tip]") : null;
    document.addEventListener("mouseover", (e) => { if (pinned) return; const elm = tipTarget(e.target); if (elm) show(elm, false); });
    document.addEventListener("mouseout", (e) => { if (pinned) return; if (tipTarget(e.target)) hide(); });
    document.addEventListener("click", (e) => {
      const elm = tipTarget(e.target);
      if (elm) { e.stopPropagation(); e.preventDefault(); if (pinned && curr === elm) { hide(); } else { pinned = true; show(elm, true); } }
      else if (pinned) { hide(); }
    }, true);
    document.addEventListener("keydown", (e) => {
      const elm = document.activeElement;
      if ((e.key === "Enter" || e.key === " ") && elm && elm.classList && elm.classList.contains("info")) {
        e.preventDefault();
        if (pinned && curr === elm) { hide(); } else { pinned = true; show(elm, true); }
      } else if (e.key === "Escape" && pinned) { hide(); }
    });
    addEventListener("scroll", () => { if (curr && pop && pop.classList.contains("show")) place(curr); }, true);
    addEventListener("resize", hide);
  }
})();
