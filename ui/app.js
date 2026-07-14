"use strict";
// Agentainer UI -- vanilla JS, no framework, no build step, no CDN.
// A mobile-friendly mail app over the file-based mailroom: browse agents, read
// each agent's correspondence as threads, reply as the user, watch the tmux
// pane / type straight into it, and edit agentainer.yaml (settings + agents).
// The token rides on every request via ?token= (simplest, works everywhere).

(function () {
  const $ = (id) => document.getElementById(id);
  const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };

  let TOKEN = "";
  const state = {
    view: "agents",       // agents | mail | settings
    status: null,         // last /api/status
    agent: null,          // open agent (mail view)
    peer: null,           // open contact
    contacts: [],
    thread: [],
    tab: "mail",          // mail | terminal (agent view)
    config: null,         // last /api/config
    telegram: null,       // last /api/telegram
    wrap: (() => { try { return !!localStorage.getItem("paneWrap"); } catch (_) { return false; } })(),
    rate: (() => { try { return !!localStorage.getItem("showRate"); } catch (_) { return false; } })(),
    notify: (() => { try { return !!localStorage.getItem("notifyOptIn"); } catch (_) { return false; } })(),
    rates: {},            // last /api/rate {name: msgs_per_min}
    lastAttention: 0,     // for the 0 -> >0 notification transition
    activityN: 100,       // activity page size (grows via "Show older")
    threadShown: 50,      // mail-thread page size (grows via "Show earlier")
  };
  const ACTIVITY_PAGE = 100; // rows added per "Show older events" click
  const THREAD_PAGE = 50;    // messages revealed per "Show earlier" click
  const timers = {};

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
    for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
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
  // Seconds -> short human duration ("42s", "3m", "1h4m").
  function fmtDur(s) {
    s = Math.max(0, Math.floor(s || 0));
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m";
    return Math.floor(s / 3600) + "h" + Math.floor((s % 3600) / 60) + "m";
  }
  // Minimal, dependency-free, XSS-safe markdown -> HTML. Everything is escaped
  // first (so agent/user text can never inject markup); we then emit only our
  // own tags, and only allow http(s)/mailto links. Good enough for mail bodies.
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
    clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 2200);
  }

  // ---- info tooltips (ⓘ affordance + hover/click popover) -----------------
  // A visible ⓘ icon marks that an explanation exists. Hovering shows it;
  // clicking/tapping PINS it open (mobile-friendly, since :hover is unreliable
  // on touch). The engine is delegated (initTips, wired once at boot) so it
  // covers markup rendered at any time. Text rides on data-tip (already escaped
  // by infoIcon via esc()); the popover uses textContent so it is XSS-safe.
  function infoIcon(text) {
    return `<span class="info" tabindex="0" role="button" aria-label="More info: ${esc(text)}" data-tip="${esc(text)}"></span>`;
  }

  // ---- API ---------------------------------------------------------------

  function withToken(path) { return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN); }

  // ---- connection honesty (#1) -------------------------------------------
  // A monitoring UI must never show frozen data as if it were live. Every
  // request goes through `rawFetch`, which timestamps the last SERVER response
  // (any HTTP status counts -- the box answered) and flags a network REJECTION.
  // The indicator then goes stale from BOTH signals: an explicit down-mark and
  // a 1s ticker (so a silently-hung server, which never rejects, still ages out).
  const conn = { lastOk: 0, down: false, ticker: null };
  function markConn(ok) {
    if (ok) { conn.lastOk = Date.now(); conn.down = false; }
    else { conn.down = true; }
    renderConn();
  }
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

  // ---- shell / navigation ------------------------------------------------

  function connect() {
    TOKEN = $("token").value.trim();
    if (!TOKEN) { banner("enter a token first"); return; }
    apiGet("/api/status").then((data) => {
      state.status = data;
      $("login").hidden = true;
      $("view").hidden = false;
      $("nav").hidden = false;
      $("availWrap").hidden = false;
      $("connind").hidden = false;
      // A GLOBAL heartbeat (kept out of `timers`, so clearTimers/navigation never
      // stops it) re-renders the indicator every second so it ages out on its own.
      if (!conn.ticker) conn.ticker = setInterval(renderConn, 1000);
      renderConn();
      banner("");
      syncAvailability(data.user_available);
      state.lastAttention = data.attention || 0;
      go("agents");
    }).catch((e) => banner("connect failed: " + e.message));
  }

  function syncAvailability(available) {
    $("availToggle").checked = !!available;
    $("availLbl").textContent = available ? "You: available" : "You: away";
  }

  function go(view) {
    state.view = view;
    clearTimers();
    const nav = view === "settings" ? "settings" : view === "activity" ? "activity" : "agents";
    for (const b of document.querySelectorAll(".navbtn"))
      b.classList.toggle("active", b.dataset.view === nav);
    // Render from the last snapshot immediately, then refetch so entering the
    // view always shows current state -- the mail view refreshes only its own
    // agent, so `state.status` is otherwise stale until the 5s interval fires.
    if (view === "agents") { renderAgents(); pollStatus(); }
    else if (view === "mail") renderMail();
    else if (view === "activity") renderActivity();
    else if (view === "settings") renderSettings();
  }

  // ---- agents overview ---------------------------------------------------

  // Truthful state vocabulary (see /api/status `state`): what the agent is
  // actually doing, not just whether tmux is up.
  const STATE_LABEL = {
    working: "working", waiting: "waiting", attention: "needs you",
    stalled: "stalled", stopped: "stopped",
  };
  // Tooltip text for each agent state, shown on the state pill.
  const STATE_TIP = {
    working: "This agent is actively running a turn right now.",
    waiting: "The agent is up but idle, waiting for its next message.",
    attention: "The agent has finished and is waiting on your reply.",
    stalled: "Busy past its timeout so its turn-completion signal was likely lost; use Esc or Restart to recover.",
    stopped: "No tmux session; the agent is not processing mail. Start it to bring it online.",
  };

  function statusPills(a) {
    // Fall back to the old running/busy signals if `state` is absent.
    const s = a.state || (a.running ? (a.busy ? "working" : "waiting") : "stopped");
    let label = STATE_LABEL[s] || s;
    if (s === "working" && a.working_s) label = "working " + fmtDur(a.working_s);
    const dot = s === "working" ? '<span class="dotpulse"></span>' : "";
    const stp = `<span class="pill st-${s}" data-tip="${esc(STATE_TIP[s] || STATE_LABEL[s] || s)}">${dot}${esc(label)}</span>`;
    const un = a.unread ? `<span class="pill busy" data-tip="Unread messages in this agent's inbox, waiting to be read and processed.">${a.unread} unread</span>` : "";
    const q = a.queue_depth ? `<span class="pill mute" data-tip="Messages held in the orchestrator queue for this agent; the inbox releases them one at a time.">${a.queue_depth} queued</span>` : "";
    // A down agent gets a Start button; a running one gets a Stop button.
    const act = a.running
      ? `<button class="pill downbtn" data-down="${esc(a.name)}">■ Stop</button>${infoIcon("Kill this agent's tmux session and any in-flight turn. No confirmation for a single agent; config is untouched.")}`
      : `<button class="pill upbtn" data-up="${esc(a.name)}">▶ Start</button>${infoIcon("Launch this agent's tmux session and run its CLI from the config; it begins reading mail. agentainer.yaml is untouched.")}`;
    // A running agent gets a Context Compact button: types the /compact slash
    // command straight into its pane and presses Enter, so the CLI compacts its
    // own context window. Only meaningful while the session is up.
    const compact = a.running
      ? `<button class="pill compactbtn" data-compact="${esc(a.name)}">⊟ Context Compact</button>${infoIcon("Type /compact into this agent's live session and press Enter, asking its CLI to compact its context window. Bypasses mail.")}`
      : "";
    // A stalled agent (busy past its timeout -- completion signal lost) gets the
    // fix inline: nudge it with Escape, or restart it, without digging into the
    // Terminal tab. Wired through the delegated document listener (data-esc /
    // data-restart) so poll-recreated buttons keep working.
    const recover = s === "stalled"
      ? `<button class="pill recover" data-esc="${esc(a.name)}">⎋ Esc</button>${infoIcon("Send a literal Escape keypress into this agent's pane to interrupt a stuck turn. The session is not killed.")}`
        + `<button class="pill recover" data-restart="${esc(a.name)}">↻ Restart</button>${infoIcon("Stop then relaunch this agent's tmux session from the config; any in-flight turn is lost, but agentainer.yaml is unchanged.")}`
      : "";
    return stp + act + compact + recover + un + q;
  }

  function renderAgents() {
    // Clear any prior poll timers so a poll-triggered re-render can't stack them.
    if (timers.status) { clearInterval(timers.status); delete timers.status; }
    if (timers.rate) { clearInterval(timers.rate); delete timers.rate; }
    const agents = (state.status && state.status.agents) || [];
    const cards = agents.map((a) => `
      <div class="card agentcard" data-agent="${esc(a.name)}">
        <div class="top">
          ${avatar(a.name)}
          <div style="min-width:0">
            <div class="name">${esc(a.name)} ${infoIcon("Open this agent: its mail thread with you and its live terminal.")}</div>
            <div class="muted" style="font-size:.8rem" data-tip="The CLI this agent runs (claude, codex, gemini, or hermes), which sets how its turn-completion is detected.">${esc(a.type)}</div>
          </div>
          ${state.rate ? `<span class="rateline" data-rateline="${esc(a.name)}"></span>` : ""}
        </div>
        <div class="role" data-role="${esc(a.name)}" data-tip="This agent's configured role and purpose, from agentainer.yaml.">${esc(a.role_preview || "")}</div>
        <div class="meta">${statusPills(a)}</div>
        <div class="muted" style="font-size:.78rem" data-tip="Agents this one may mail, per the can_talk_to ACL. Cooperative, not an OS boundary.">talks to: ${esc((a.can_talk_to || []).join(", ") || "—")}</div>
      </div>`).join("");
    const notifyTgl = ("Notification" in window)
      ? `<label class="tgl"><input type="checkbox" id="notifyTgl" ${state.notify ? "checked" : ""}/> Notify ${infoIcon("Opt in to a desktop notification when agents go from zero to needing your reply. Requires browser notification permission.")}</label>` : "";
    // Bulk controls only make sense once the swarm has agents to act on.
    const bulk = agents.length ? `
          <button class="btn ghost sm bulk-btn" id="startAll">Start all</button>${infoIcon("Start every currently stopped agent at once, each launched from the config.")}
          <button class="btn ghost sm bulk-btn" id="stopAll">Stop all</button>${infoIcon("Stop every running agent at once; each tmux session and any in-flight turn is killed. Requires confirmation.")}
          <button class="btn ghost sm bulk-btn" id="restartAll">Restart all</button>${infoIcon("Restart all agents at once; every running session is killed then relaunched, interrupting live turns. Requires confirmation.")}` : "";
    const body = cards
      ? `<div class="grid">${cards}</div>`
      : `<div id="emptyAgents"><p class="empty">Loading templates…</p></div>`;
    $("view").innerHTML = `
      <div class="sectiontitle">
        <h2>Agents <span class="muted" style="font-weight:500">(${agents.length})</span></h2>
        <div class="agents-tools">
          <label class="tgl"><input type="checkbox" id="rateTgl" ${state.rate ? "checked" : ""}/> Show rate ${infoIcon("Toggle a per-agent messages-per-minute rate (5-minute window) on each card; off by default to keep the view light.")}</label>
          ${notifyTgl}
          ${bulk}
          ${agents.length ? `<button class="btn ghost sm" id="compactAllBtn">Context Compact All</button>${infoIcon("Type /compact into every running agent's session and press Enter, asking each CLI to compact its context window. Bypasses mail; requires confirmation.")}` : ""}
          <button class="btn ghost sm" id="refreshBtn">Refresh</button>${infoIcon("Re-fetch agent status now instead of waiting for the auto-refresh.")}
        </div>
      </div>
      ${topologyCard(agents)}
      ${body}`;
    $("refreshBtn").onclick = pollStatus;
    if ($("startAll")) $("startAll").onclick = () => bulkAction("up");
    if ($("stopAll")) $("stopAll").onclick = () => bulkAction("down");
    if ($("restartAll")) $("restartAll").onclick = () => bulkAction("restart");
    if ($("compactAllBtn")) $("compactAllBtn").onclick = compactAll;
    $("rateTgl").onchange = (e) => toggleRate(e.target.checked);
    if ($("notifyTgl")) $("notifyTgl").onchange = (e) => toggleNotify(e.target.checked);
    if (!cards) loadTemplates();
    if (state.rate) { loadRates(); timers.rate = setInterval(loadRates, 5000); }
    for (const c of document.querySelectorAll(".agentcard"))
      c.onclick = (e) => {
        // The Start/Stop and stalled-recovery pills live inside the card; the
        // delegated document listener handles them. Its stopPropagation fires too
        // late to stop this (closer) handler, so skip opening the mail page for
        // those clicks here.
        if (e.target.closest("[data-up],[data-down],[data-esc],[data-restart],[data-compact]")) return;
        openAgent(c.dataset.agent);
      };
    for (const g of document.querySelectorAll(".gnode")) {
      g.onclick = () => openAgent(g.dataset.agent);
      g.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openAgent(g.dataset.agent); } };
    }
    // Lazily enrich each card's role text (kept out of /api/status to stay light).
    agents.forEach((a) => apiGet("/api/agent?agent=" + encodeURIComponent(a.name))
      .then((d) => { const n = document.querySelector(`[data-role="${cssq(a.name)}"]`); if (n) n.textContent = (d.agent && d.agent.role) || "(no role set)"; })
      .catch(() => {}));
    timers.status = setInterval(pollStatus, 5000);
  }

  function cssq(s) { return String(s).replace(/"/g, '\\"'); }

  // ---- "the swarm needs you" signal (#3) ---------------------------------

  // Fire a browser notification only on the 0 -> >0 edge, and only when opted in.
  function maybeNotify(attention) {
    const prev = state.lastAttention || 0;
    state.lastAttention = attention;
    if (!state.notify || !("Notification" in window)) return;
    if (attention > 0 && prev === 0 && Notification.permission === "granted") {
      try {
        new Notification("Agentainer", {
          body: attention + " message" + (attention > 1 ? "s" : "") + " awaiting your reply",
        });
      } catch (_) {}
    }
  }

  function toggleNotify(on) {
    if (on && "Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().then((p) => {
        if (p !== "granted") {
          state.notify = false;
          try { localStorage.setItem("notifyOptIn", ""); } catch (_) {}
          const t = $("notifyTgl"); if (t) t.checked = false;
          toast("notifications blocked by the browser");
        }
      });
    }
    state.notify = on;
    try { localStorage.setItem("notifyOptIn", on ? "1" : ""); } catch (_) {}
  }

  // ---- optional message rate (#6, off by default) ------------------------

  function toggleRate(on) {
    state.rate = on;
    try { localStorage.setItem("showRate", on ? "1" : ""); } catch (_) {}
    if (state.view === "agents") renderAgents();
  }

  function loadRates() {
    if (!state.rate) return;
    apiGet("/api/rate?window=5").then((d) => {
      state.rates = (d && d.rates) || {};
      for (const el of document.querySelectorAll("[data-rateline]")) {
        const v = state.rates[el.dataset.rateline] || 0;
        el.textContent = v.toFixed(1) + "/min";
      }
    }).catch(() => {});
  }

  // ---- onboarding: start from a template (#4) ----------------------------

  function loadTemplates() {
    const box = $("emptyAgents"); if (!box) return;
    const fallback = '<p class="empty">No agents configured. Add one in Settings.</p>';
    apiGet("/api/templates").then((d) => {
      const tpls = (d && d.templates) || [];
      if (!tpls.length) { box.innerHTML = fallback; return; }
      box.innerHTML = `
        <div class="tpl-intro"><b>Start from a template</b><span class="muted"> — or add your own in Settings.</span></div>
        <div class="tplgrid">${tpls.map((x) => `
          <button class="card tpl" data-tpl="${esc(x.name)}">
            <div class="tpl-title">${esc(x.title || x.name)}</div>
            <div class="muted tpl-sum">${esc(x.summary || "")}</div>
            <div class="tpl-meta muted">${esc(String(x.agents || 0))} agent${x.agents === 1 ? "" : "s"}</div>
          </button>${infoIcon("Add this template's agents to your swarm: writes them into agentainer.yaml and launches each one.")}`).join("")}</div>`;
      for (const b of box.querySelectorAll("[data-tpl]"))
        b.onclick = () => applyTemplate(b.dataset.tpl, b);
    }).catch(() => { box.innerHTML = fallback; });
  }

  function applyTemplate(name, btn) {
    if (btn) btn.disabled = true;
    apiPost("/api/templates/apply", { name }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); if (btn) btn.disabled = false; return; }
      toast("added " + ((res.j.added || []).length) + " agents from " + name);
      pollStatus();
    });
  }

  // Bring one agent up (from a `.upbtn` on a card or the mail header).
  function startAgent(name, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "starting…"; }
    apiPost("/api/up", { agent: name }).then((res) => {
      if (!res.ok) {
        toast("error: " + (res.j.error || "failed"));
        if (btn) { btn.disabled = false; btn.textContent = "▶ Start"; }
        return;
      }
      toast(res.j.started ? "starting " + name : name + " already running");
      // Give the CLI a moment to open its tmux session, then refresh.
      setTimeout(() => { pollStatus(); refreshMailStatus(); }, 800);
    });
  }

  // Take one agent down (kill its tmux session). Config is left untouched.
  function stopAgent(name, btn) {
    if (!confirm("Stop " + name + "? Its tmux session (and any in-flight turn) will be killed.")) return;
    if (btn) { btn.disabled = true; btn.textContent = "stopping…"; }
    apiPost("/api/down", { agent: name }).then((res) => {
      if (!res.ok) {
        toast("error: " + (res.j.error || "failed"));
        if (btn) { btn.disabled = false; btn.textContent = "■ Stop"; }
        return;
      }
      toast(res.j.stopped ? "stopped " + name : name + " already down");
      setTimeout(() => { pollStatus(); refreshMailStatus(); }, 400);
    });
  }

  // Start / stop / restart every agent at once (buttons in the Agents header).
  // Buttons are disabled for the duration so a double-click can't fire twice.
  function bulkAction(kind) {
    if (kind === "down" && !confirm("Stop ALL running agents? Each tmux session (and any in-flight turn) will be killed.")) return;
    if (kind === "restart" && !confirm("Restart ALL agents? Running sessions are killed, then every configured agent is relaunched.")) return;
    const btns = Array.from(document.querySelectorAll(".bulk-btn"));
    btns.forEach((b) => { b.disabled = true; });
    let p;
    if (kind === "up") {
      p = apiPost("/api/up_all", {}).then((res) => bulkToast(res, "started"));
    } else if (kind === "down") {
      p = apiPost("/api/down_all", {}).then((res) => bulkToast(res, "stopped"));
    } else {
      p = apiPost("/api/down_all", {}).then((res) => {
        if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
        return apiPost("/api/up_all", {}).then((r2) => bulkToast(r2, "started", "restarted"));
      });
    }
    p.catch(() => toast("network error"))
      .finally(() => { btns.forEach((b) => { b.disabled = false; }); pollStatus(); });
  }

  function bulkToast(res, key, verb) {
    if (!res || !res.ok) { toast("error: " + ((res && res.j.error) || "failed")); return; }
    toast((verb || key) + " " + ((res.j[key] || []).length));
  }

  function pollStatus() {
    apiGet("/api/status").then((data) => {
      state.status = data;
      banner("");  // recovered: clear any stale "connect failed" / poll error
      syncAvailability(data.user_available);
      maybeNotify(data.attention || 0);
      $("swarmMeta").textContent = (data.name || "swarm") + " · " + ((data.agents || []).length) + " agents";
      if (state.view === "agents") {
        const agents = data.agents || [];
        const shown = Array.from(document.querySelectorAll(".agentcard")).map((c) => c.dataset.agent);
        const same = shown.length === agents.length && agents.every((a, i) => shown[i] === a.name);
        if (!same) {
          // Agent set changed (added / removed / brought up elsewhere): rebuild
          // the whole view so new cards and the topology graph appear.
          renderAgents();
        } else {
          // Same set: patch pill rows in place so we don't stomp scroll / role text.
          agents.forEach((a) => {
            const card = document.querySelector(`.agentcard[data-agent="${cssq(a.name)}"] .meta`);
            if (card) card.innerHTML = statusPills(a);
          });
        }
      }
    }).catch((e) => banner(e.message));
  }

  // ---- topology graph (who-talks-to-whom) --------------------------------

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
    nodes.forEach((n, i) => {
      const ang = -Math.PI / 2 + (i * 2 * Math.PI) / N;
      pos[n] = { x: cx + r * Math.cos(ang), y: cy + r * Math.sin(ang) };
    });
    let edges = "";
    agents.forEach((a) => (a.can_talk_to || []).forEach((p) => {
      if (pos[a.name] && pos[p]) {
        const s = pos[a.name], t = pos[p];
        const dx = t.x - s.x, dy = t.y - s.y, len = Math.hypot(dx, dy) || 1, R = 21;
        // Live flow: an edge INTO a node that has unread mail is "carrying" mail.
        const live = byName[p] && byName[p].unread > 0;
        edges += `<line x1="${s.x + (dx / len) * R}" y1="${s.y + (dy / len) * R}" x2="${t.x - (dx / len) * R}" y2="${t.y - (dy / len) * R}" class="edge${live ? " edge-live" : ""}" marker-end="url(#arrow)"><title>Mail route: the sender may send to the recipient under the can_talk_to ACL; a pulsing arrow means unread mail is waiting.</title></line>`;
      }
    }));
    const circles = nodes.map((n) => {
      const p = pos[n];
      const isUser = n === "user";
      const st = isUser ? null : ((byName[n] && byName[n].state) || "waiting");
      const fill = isUser ? "hsl(215 62% 48%)" : `hsl(${hueFor(n)} 62% 48%)`;
      const clickable = !isUser;
      // A status ring around the node, colored by live state (waiting = none).
      const ring = (st && st !== "waiting")
        ? `<circle cx="${p.x}" cy="${p.y}" r="23" fill="none" class="gring gring-${st}"/>` : "";
      const dim = st === "stopped" ? ' opacity="0.55"' : "";
      return `<g${clickable ? ` class="gnode" data-agent="${esc(n)}" role="button" tabindex="0" aria-label="open ${esc(n)}"` : ""}${dim}>
        <title>${esc("Open " + (n === "user" ? "you" : n))} — colored ring shows live state; a dimmed circle is stopped.</title>
        ${ring}
        <circle cx="${p.x}" cy="${p.y}" r="19" fill="${fill}"/>
        <text x="${p.x}" y="${p.y + 4}" text-anchor="middle" class="gnode-t">${esc(initials(n))}</text>
        <text x="${p.x}" y="${p.y + 36}" text-anchor="middle" class="gnode-l">${esc(n === "user" ? "you" : n)}</text></g>`;
    }).join("");
    return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;min-width:420px;height:auto;color:var(--muted)" role="img" aria-label="agent communication graph">
      <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>
      <g class="edges">${edges}</g>${circles}</svg>`;
  }

  // ---- activity timeline (global event log) ------------------------------

  const KIND_CLASS = {
    route: "ok", delivered: "ok", "user-send": "ok", read: "mute", "read-receipt": "mute",
    bounce: "no", "rate-limited": "no", ping: "busy", "user-held": "busy",
    "user-available": "ok", "user-away": "mute",
  };
  // Tooltip text for each activity event kind, shown on the kind badge.
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
    state.activityN = ACTIVITY_PAGE; // fresh entry -> back to the first page
    $("view").innerHTML = `
      <div class="sectiontitle">
        <h2>Activity</h2>
        <button class="btn ghost sm" id="refreshBtn">Refresh</button>${infoIcon("Reload the latest events from the durable JSONL event log.")}
      </div>
      <div class="card" style="padding:.3rem .2rem"><div id="timeline" class="timeline"><p class="empty">Loading…</p></div></div>
      <div class="pager" id="activityPager"></div>`;
    $("refreshBtn").onclick = loadTimeline;
    loadTimeline();
    timers.activity = setInterval(loadTimeline, 5000);
  }

  function loadTimeline() {
    apiGet("/api/logs?n=" + state.activityN).then((d) => {
      const raw = d.logs || [];
      const logs = raw.slice().reverse(); // newest first
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
      // The API returns the last N records; a full page means older ones may
      // exist, so offer to widen the window. (No total count is exposed.)
      const pager = $("activityPager"); if (!pager) return;
      if (raw.length >= state.activityN) {
        pager.innerHTML = `<button class="btn ghost sm" id="activityMore">Show older events</button>${infoIcon("Load 100 earlier events from the durable JSONL log.")}`;
        $("activityMore").onclick = () => { state.activityN += ACTIVITY_PAGE; loadTimeline(); };
      } else {
        pager.innerHTML = `<span class="muted">${raw.length} event${raw.length === 1 ? "" : "s"} · all shown</span>`;
      }
    }).catch((e) => banner(e.message));
  }

  // ---- mail app ----------------------------------------------------------

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
        <button class="btn ghost sm" id="backBtn">← Agents</button>${infoIcon("Go back to the Agents overview.")}
        <div class="who">${avatar(agent, "sm")}<h2>${esc(agent)}</h2><span id="mailStatus"></span></div>
        <span style="flex:1"></span>
        <div class="tabs">
          <button class="tab ${state.tab === "mail" ? "active" : ""}" data-tab="mail">Mail</button>${infoIcon("Browse contacts and exchange file-based mail (inbox, outbox, read) with this agent.")}
          <button class="tab ${state.tab === "terminal" ? "active" : ""}" data-tab="terminal">Terminal</button>${infoIcon("Live tmux pane plus direct keystrokes into this session, bypassing mail.")}
        </div>
      </div>
      <div id="agentBody"></div>`;
    $("backBtn").onclick = () => go("agents");
    for (const b of document.querySelectorAll(".tab"))
      b.onclick = () => { state.tab = b.dataset.tab; renderMail(); };
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
          <label class="wrapchk"><input type="checkbox" id="wrapPane" ${state.wrap ? "checked" : ""}/> Wrap text ${infoIcon("Wrap long lines in the captured pane snapshot; display only, does not affect the agent.")}</label>
          <span class="muted" style="font-size:.78rem">capture-pane · refreshes 2s</span>
        </div>
        <pre class="pane${state.wrap ? " wrap" : ""}" id="pane">— loading —</pre>
        <div class="keyrow">${KEYS.map((k) => `<button class="keybtn" data-key="${esc(k.key)}" title="${esc(k.title)}">${esc(k.label)}</button>`).join("")}</div>
        <div class="typerow">
          <input class="field" id="typeText" placeholder="Type straight into ${esc(state.agent)}'s session, press Enter…" />${infoIcon("Type text fed directly into the agent's tmux pane as live keystrokes; bypasses mail and acts on the running session immediately.")}
          <button class="btn" id="typeSend">Send</button>${infoIcon("Send the typed text straight into the agent's live pane as keystrokes; bypasses mail.")}
        </div>
        <p class="muted" style="font-size:.8rem;margin:.5rem 0 0">Types directly into the tmux pane (bypasses mail). The keys above send a single control keystroke (e.g. Esc to dismiss a prompt). An empty pane means the agent's session isn't running.</p>
      </div>`;
    const inp = $("typeText");
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); sendType(); } });
    $("typeSend").onclick = sendType;
    for (const b of document.querySelectorAll(".keybtn")) b.onclick = () => sendKey(b.dataset.key);
    $("wrapPane").onchange = (e) => {
      state.wrap = e.target.checked;
      try { localStorage.setItem("paneWrap", state.wrap ? "1" : ""); } catch (_) {}
      $("pane").classList.toggle("wrap", state.wrap);
    };
    loadPane();
    timers.pane = setInterval(() => { loadPane(); refreshMailStatus(); }, 2000);
  }

  function refreshMailStatus() {
    apiGet("/api/agent?agent=" + encodeURIComponent(state.agent)).then((d) => {
      const a = d.agent; if (!a) return;
      const s = $("mailStatus"); if (s) s.innerHTML = statusPills(a);
    }).catch(() => {});
  }

  function loadContacts() {
    apiGet("/api/contacts?agent=" + encodeURIComponent(state.agent)).then((d) => {
      state.contacts = orderedContacts(d.contacts || []);
      renderContacts();
    }).catch((e) => banner(e.message));
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
          <div class="info">
            <div class="cn"><b>${esc(label)}</b>${infoIcon("Open this contact's message thread.")}${time}</div>
            <div class="prev">${esc(c.last_preview || (c.count ? "" : "no messages yet"))}</div>
          </div>
          ${badge}
        </div>`;
    }).join("") || '<p class="empty">No contacts.</p>';
    for (const node of box.querySelectorAll(".contact"))
      node.onclick = () => selectPeer(node.dataset.peer);
  }

  function selectPeer(peer) {
    state.peer = peer;
    state.threadShown = THREAD_PAGE; // start each conversation at its newest page
    document.body.dataset.pane = "thread";
    renderContacts();
    loadThread();
  }

  function loadThread() {
    if (!state.peer) return;
    apiGet(`/api/thread?agent=${encodeURIComponent(state.agent)}&peer=${encodeURIComponent(state.peer)}`)
      .then((d) => { state.thread = d.messages || []; renderThread(); })
      .catch((e) => banner(e.message));
  }

  function renderThread() {
    const scroll = $("threadScroll"); if (!scroll) return;
    // Idempotent: the 4s mail poll calls this even when nothing changed.
    // Reassigning innerHTML rebuilds every node and wipes any text the user
    // has selected, so bail out when the thread is byte-for-byte the same.
    // Page size is part of the signature so "Show earlier" (which only grows the
    // window, not the thread) still forces a re-render past the idempotency guard.
    const sig = JSON.stringify([state.threadShown, state.thread.map((m) => [m.from, m.to, m.time, m.status, m.body])]);
    if (scroll.dataset.sig === sig) { renderCompose(); return; }
    scroll.dataset.sig = sig;
    // Jump to the newest message when a different conversation is opened;
    // otherwise only auto-follow when the reader is already near the bottom.
    const newPeer = scroll.dataset.peer !== state.peer;
    scroll.dataset.peer = state.peer;
    const atBottom = newPeer || scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 60;
    if (!state.thread.length) {
      scroll.innerHTML = `<p class="empty">No messages between <b>${esc(state.agent)}</b> and <b>${esc(state.peer === "user" ? "you" : state.peer)}</b> yet.</p>`;
    } else {
      // Paginate from the newest end: show the last `threadShown`, reveal older
      // ones on demand so a long history doesn't render (or scroll) all at once.
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

  // Long mails are hard to scroll past, so bodies over COLLAPSE_LINES lines are
  // clipped to the first COLLAPSE_LINES with a "Show N more lines" toggle. Both
  // the short and full renders are prebuilt; the button just swaps which shows.
  const COLLAPSE_LINES = 10;
  function mailBody(raw) {
    const src = (raw || "").trim();
    const lines = src.split("\n");
    if (lines.length <= COLLAPSE_LINES)
      return `<div class="m-body">${md(src)}</div>`;
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
    const collapse = box.dataset.collapsed !== "1"; // flip current state
    box.dataset.collapsed = collapse ? "1" : "0";
    box.querySelector(".m-short").hidden = !collapse;
    box.querySelector(".m-full").hidden = collapse;
    const n = btn.dataset.more;
    btn.textContent = collapse ? `Show ${n} more line${n > 1 ? "s" : ""}` : "Show less";
  }

  // Delivery status of one message, from where it currently sits in the mailroom.
  function statusTag(s) {
    const map = {
      queued: ["◷", "waiting"], delivered: ["✓", "delivered"],
      read: ["✓✓", "read"], archived: ["⤓", "archived"],
    };
    // Tooltip text for each message status, shown on the status tag.
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
    // Idempotent: background polls call this every few seconds. Rebuilding the
    // textarea would wipe whatever the user is typing (and drop focus), so only
    // touch the DOM when the mode actually changes.
    if (area.dataset.mode === mode) return;
    area.dataset.mode = mode;
    if (mode === "compose") {
      area.innerHTML = `
        <div class="compose">
          <textarea class="field" id="reply" rows="1" placeholder="Message ${esc(state.agent)} as the user…"></textarea>${infoIcon("Write a message to this agent as the operator (user); Ctrl or Cmd+Enter or Send delivers it as file-based mail to the agent's inbox, not into the tmux pane.")}
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
    apiPost("/api/send", { to: state.agent, text }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      $("reply").value = ""; $("reply").style.height = "auto";
      toast("sent to " + state.agent);
      loadThread(); loadContacts();
    });
  }

  // ---- terminal (live pane + direct type-in) -----------------------------

  // Control keys offered under the pane. `key` is a tmux key name the backend
  // whitelists (lib/tmux.py ALLOWED_KEYS).
  const KEYS = [
    { key: "Escape", label: "Esc", title: "Send Escape into the pane; can dismiss a trust or permission modal or cancel a menu, and may interrupt a live turn." },
    { key: "Enter", label: "Enter", title: "Send Enter/Return; submits whatever is on the agent's current input line." },
    { key: "Tab", label: "Tab", title: "Send Tab; triggers shell or CLI tab-completion, which can alter the current input." },
    { key: "Up", label: "↑", title: "Send the Up arrow; recalls the previous command from history." },
    { key: "Down", label: "↓", title: "Send the Down arrow; steps forward through command history." },
    { key: "Left", label: "←", title: "Send the Left arrow; moves the cursor left without changing text." },
    { key: "Right", label: "→", title: "Send the Right arrow; moves the cursor right without changing text." },
    { key: "C-c", label: "Ctrl-C", title: "Send Ctrl-C; interrupts and cancels the running command or a live turn, which may abort an in-flight tool call." },
    { key: "C-u", label: "Ctrl-U", title: "Send Ctrl-U; clears the entire current input line." },
    { key: "C-l", label: "Ctrl-L", title: "Send Ctrl-L; repaints the terminal and clears the visible screen without disturbing the running process." },
  ];

  // `agent` is optional: the Terminal keypad omits it (targets the open agent),
  // the stalled-recovery ⎋ Esc button passes the card's name explicitly.
  function sendKey(key, agent) {
    const name = agent || state.agent;
    apiPost("/api/key", { agent: name, key }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast(key + " → " + name);
      if (name === state.agent) setTimeout(loadPane, 300);
    });
  }

  // Restart one agent by name (stop, then start) -- the stalled-recovery action.
  function restartAgent(name) {
    toast("restarting " + name + "…");
    apiPost("/api/down", { agent: name }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      apiPost("/api/up", { agent: name }).then((r2) => {
        if (!r2.ok) { toast("error: " + (r2.j.error || "failed")); return; }
        toast("restarted " + name);
        setTimeout(() => { pollStatus(); refreshMailStatus(); }, 800);
      });
    });
  }

  // The slash command that asks a CLI to compact its own context window. Typed
  // straight into the pane (via /api/type, which appends Enter), exactly like a
  // human would. Claude/Codex use /compact; this is the common case.
  const COMPACT_CMD = "/compact";

  // Type /compact into one agent's live session (from a card / mail-header pill).
  function compactAgent(name, btn) {
    if (btn) btn.disabled = true;
    apiPost("/api/type", { agent: name, text: COMPACT_CMD }).then((res) => {
      if (btn) btn.disabled = false;
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast(res.j.ok ? "compacting " + name : "sent /compact to " + name + " (not confirmed)");
      if (name === state.agent && state.tab === "terminal") setTimeout(loadPane, 400);
    });
  }

  // Type /compact into every running agent at once (Agents-header button). Only
  // running agents have a live pane to receive it; stopped ones are skipped.
  function compactAll() {
    const agents = ((state.status && state.status.agents) || []).filter((a) => a.running);
    if (!agents.length) { toast("no running agents to compact"); return; }
    if (!confirm("Send /compact to ALL " + agents.length + " running agent" + (agents.length > 1 ? "s" : "") + "? This types the /compact command into each live session.")) return;
    const btn = $("compactAllBtn"); if (btn) btn.disabled = true;
    Promise.all(agents.map((a) => apiPost("/api/type", { agent: a.name, text: COMPACT_CMD }).catch(() => null)))
      .then((results) => {
        const ok = results.filter((r) => r && r.ok).length;
        toast("sent /compact to " + ok + "/" + agents.length + " agents");
      })
      .finally(() => { if (btn) btn.disabled = false; });
  }

  function loadPane() {
    apiGet("/api/pane?agent=" + encodeURIComponent(state.agent)).then((d) => {
      const p = $("pane"); if (!p) return;
      // Follow the tail: keep pinned to the newest output unless the user has
      // scrolled up to read history (then leave their position alone).
      const atBottom = p.scrollHeight - p.scrollTop - p.clientHeight < 40;
      p.textContent = d.pane || "— (empty / session down) —";
      if (atBottom) p.scrollTop = p.scrollHeight;
    }).catch(() => {});
  }

  function sendType() {
    const text = $("typeText").value;
    if (!text.trim()) return;
    apiPost("/api/type", { agent: state.agent, text }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      $("typeText").value = "";
      toast(res.j.ok ? "typed into " + state.agent : "sent (not confirmed in pane)");
      setTimeout(loadPane, 400);
    });
  }

  // ---- settings ----------------------------------------------------------

  const SWARM_SCHEMA = [
    ["name", "Swarm name", "text"],
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
    session_prefix: "", name: "",
  };

  function renderSettings() {
    Promise.all([apiGet("/api/config"), apiGet("/api/telegram").catch(() => null)]).then(([cfg, tg]) => {
      state.config = cfg;
      state.telegram = tg;
      const sw = cfg.swarm || {};
      const fields = SWARM_SCHEMA.map(([key, label, type]) => {
        const val = key in sw ? sw[key] : SWARM_DEFAULTS[key];
        if (type === "bool")
          return `<div class="fld"><label>${esc(label)}</label>
            <label class="row"><input type="checkbox" data-swarm="${key}" ${val ? "checked" : ""}/> <span class="muted">${key}</span></label></div>`;
        return `<div class="fld"><label>${esc(label)}</label>
          <input class="field" data-swarm="${key}" type="${type === "num" ? "number" : "text"}" value="${esc(val == null ? "" : val)}"/></div>`;
      }).join("");
      const agents = (cfg.agents || []).map((a) => `
        <div class="agentrow">
          ${avatar(String(a.name), "sm")}
          <div class="info">
            <b>${esc(a.name)}</b> <span class="muted">${esc(a.type || "claude")}</span>
            <div class="muted" style="font-size:.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">talks to: ${esc(fmtCanTalk(a.can_talk_to))}</div>
          </div>
          <button class="btn ghost sm" data-edit="${esc(a.name)}">Edit</button>${infoIcon("Edit this agent; its name is locked since renaming would orphan its mailbox folders.")}
          <button class="btn danger sm" data-del="${esc(a.name)}">Delete</button>${infoIcon("Stop this agent's session and remove it from the config. Irreversible.")}
        </div>`).join("");
      $("view").innerHTML = `
        <div class="settings">
          <div class="card panel">
            <h3>Swarm settings</h3>
            <p class="muted" style="margin-top:.1rem">Saved straight to <code>${esc(cfg.path || "agentainer.yaml")}</code>.</p>
            <div class="formgrid">${fields}</div>
            <div class="rowend"><button class="btn" id="saveSwarm">Save settings</button>${infoIcon("Write the swarm fields above to agentainer.yaml; supervise and timeout changes apply on the next up, not to running agents.")}</div>
          </div>
          ${telegramCard(tg, cfg.agents || [])}
          <div class="card panel">
            <div class="sectiontitle"><h3>Agents</h3><button class="btn sm" id="addAgent">+ Add agent</button>${infoIcon("Open the form to define a new agent; it is created and its session launched on save.")}</div>
            ${agents || '<p class="muted">No agents yet.</p>'}
          </div>
        </div>`;
      $("saveSwarm").onclick = saveSwarm;
      $("addAgent").onclick = () => openAgentForm(null);
      for (const b of document.querySelectorAll("[data-edit]")) b.onclick = () => openAgentForm(b.dataset.edit);
      for (const b of document.querySelectorAll("[data-del]")) b.onclick = () => deleteAgent(b.dataset.del);
      wireTelegram();
    }).catch((e) => banner(e.message));
  }

  function telegramCard(tg, agents) {
    if (!tg) return "";
    const allScope = tg.mirror === "*" || (Array.isArray(tg.mirror) && tg.mirror.includes("*"));
    const sel = Array.isArray(tg.mirror) ? tg.mirror : [];
    const checks = agents.map((a) => `
      <label class="row" style="gap:.3rem"><input type="checkbox" class="tg-agent" value="${esc(a.name)}" ${sel.includes(a.name) ? "checked" : ""} ${allScope ? "disabled" : ""}/> ${esc(a.name)} ${infoIcon("Tick to mirror this agent's mail to Telegram; disabled when all-agents is selected.")}</label>`).join("");
    return `
      <div class="card panel">
        <div class="sectiontitle"><h3>Telegram bridge</h3>
          <span class="pill ${tg.enabled ? "ok" : "mute"}">${tg.enabled ? "on" : "off"}</span></div>
        <p class="muted" style="margin:.1rem 0 .6rem">Mirror the swarm's mail to a Telegram chat and reply from your phone. Uses the Bot API over HTTPS — zero dependencies. Create a bot with <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer">@BotFather</a>, then get your numeric chat id from <a href="https://t.me/userinfobot" target="_blank" rel="noopener noreferrer">@userinfobot</a>.</p>
        <div class="formgrid">
          <div class="fld"><label>Enabled</label><label class="row"><input type="checkbox" id="tg_enabled" ${tg.enabled ? "checked" : ""}/> <span class="muted">mirror on ${infoIcon("Turn the Telegram bridge on or off; when off, no mail is mirrored out and incoming replies are ignored.")}</span></label></div>
          <div class="fld"><label>Bot token ${infoIcon("The secret bot token from BotFather; leave blank to keep the stored token, or enter a new one to replace it.")}</label><input class="field" id="tg_token" type="password" placeholder="${tg.has_token ? "•••• stored — blank keeps it" : "123456:ABC-DEF…"}"/></div>
          <div class="fld"><label>Chat ID ${infoIcon("Your numeric Telegram chat id from userinfobot; the chat where mirrored mail is sent and replies are read.")}</label><input class="field" id="tg_chat" value="${esc(tg.chat_id || "")}" placeholder="e.g. 123456789"/></div>
          <div class="fld"><label>Mirror your mail</label><label class="row"><input type="checkbox" id="tg_muser" ${tg.mirror_user ? "checked" : ""}/> <span class="muted">mail to you ${infoIcon("Forward messages addressed to your user mailbox to Telegram so you stay reachable while away.")}</span></label></div>
          <div class="fld"><label>Mirror system</label><label class="row"><input type="checkbox" id="tg_msys" ${tg.mirror_system ? "checked" : ""}/> <span class="muted">pings/bounces ${infoIcon("Forward system pings and ACL bounces to Telegram too, not just regular agent mail.")}</span></label></div>
        </div>
        <div class="fld" style="margin-top:.6rem"><label>Which agents to mirror</label>
          <div class="row">
            <label class="row" style="gap:.3rem"><input type="radio" name="tgscope" value="all" ${allScope ? "checked" : ""}/> all agents ${infoIcon("Mirror mail from every agent; selecting this disables the per-agent checkboxes below.")}</label>
            <label class="row" style="gap:.3rem"><input type="radio" name="tgscope" value="sel" ${allScope ? "" : "checked"}/> selected ${infoIcon("Mirror only the agents you tick below; user mail is always mirrored regardless.")}</label>
          </div>
          <div class="row" id="tg_agents" style="margin-top:.4rem;opacity:${allScope ? ".5" : "1"}">${checks || '<span class="muted">no agents</span>'}</div>
        </div>
        <div class="rowend">
          <button class="btn ghost" id="tg_test">Send test</button>${infoIcon("Save current settings then post a test message to verify the token and chat id work.")}
          <button class="btn ghost" id="tg_poll">${tg.polling ? "Stop replies" : "Receive replies"}</button>${infoIcon("Start long-polling so your Telegram replies route back in as user mail; click again to stop.")}
          <button class="btn" id="tg_save">Save Telegram</button>${infoIcon("Persist the bot token, chat id, and mirror settings to agentainer.yaml. When enabled, this also sends a test message to verify the config and starts watching for replies.")}
        </div>
      </div>`;
  }

  function wireTelegram() {
    if (!$("tg_save")) return;
    for (const r of document.querySelectorAll('input[name="tgscope"]'))
      r.onchange = () => {
        const all = document.querySelector('input[name="tgscope"]:checked').value === "all";
        $("tg_agents").style.opacity = all ? ".5" : "1";
        for (const c of document.querySelectorAll(".tg-agent")) c.disabled = all;
      };
    $("tg_save").onclick = saveTelegram;
    $("tg_test").onclick = testTelegram;
    $("tg_poll").onclick = toggleTelegramPolling;
  }

  function collectTelegram() {
    const all = document.querySelector('input[name="tgscope"]:checked').value === "all";
    const mirror = all ? "*" : Array.from(document.querySelectorAll(".tg-agent:checked")).map((c) => c.value);
    const body = {
      enabled: $("tg_enabled").checked,
      chat_id: $("tg_chat").value.trim(),
      mirror,
      mirror_user: $("tg_muser").checked,
      mirror_system: $("tg_msys").checked,
    };
    const tok = $("tg_token").value.trim();
    if (tok) body.bot_token = tok;
    return body;
  }

  function saveTelegram() {
    const body = collectTelegram();
    apiPost("/api/telegram", body).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      // When disabled there's nothing to verify — just confirm the save.
      if (!body.enabled) { toast("Telegram settings saved"); renderSettings(); return; }
      // Enabled: by default prove the config works end-to-end. Send a test
      // message; on success also auto-start the reply watcher so replies from
      // the phone route back in without a second click.
      toast("Telegram settings saved — sending test message…");
      apiPost("/api/telegram/test", {}).then((tst) => {
        if (!tst.ok) {
          toast("saved, but the test message failed — please check your bot token and chat id: " + (tst.j.error || "failed"));
          renderSettings();
          return;
        }
        apiPost("/api/telegram/poll", { run: true }).then((pl) => {
          if (pl.ok && pl.j.polling) toast("test message sent ✓ — now watching for Telegram replies");
          else toast("test message sent ✓ — but couldn't start watching replies: " + ((pl.j && pl.j.error) || "failed"));
          renderSettings();
        });
      });
    });
  }

  function testTelegram() {
    // Persist first (so the token/chat just typed are used), then send a test.
    apiPost("/api/telegram", collectTelegram()).then(() =>
      apiPost("/api/telegram/test", {}).then((res) => {
        toast(res.ok ? "test message sent" : "error: " + (res.j.error || "failed"));
      }));
  }

  function toggleTelegramPolling() {
    const run = !(state.telegram && state.telegram.polling);
    apiPost("/api/telegram/poll", { run }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast(res.j.polling ? "listening for Telegram replies" : "stopped listening");
      renderSettings();
    });
  }

  function fmtCanTalk(v) { return Array.isArray(v) ? v.join(", ") : (v || "—"); }

  function saveSwarm() {
    const swarm = {};
    for (const node of document.querySelectorAll("[data-swarm]")) {
      const key = node.dataset.swarm;
      if (node.type === "checkbox") swarm[key] = node.checked;
      else if (node.type === "number") { if (node.value !== "") swarm[key] = Number(node.value); }
      else if (node.value !== "") swarm[key] = node.value;
    }
    apiPost("/api/config", { swarm }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast("settings saved");
    });
  }

  function deleteAgent(name) {
    if (!confirm(`Delete agent "${name}"? This stops its session and removes it from the config.`)) return;
    apiPost("/api/agent/remove", { name }).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast("removed " + name);
      renderSettings(); pollStatus();
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
          <div class="fld"><label>Name ${infoIcon("Unique mailbox name; cannot be user or system (reserved). Locked when editing.")}</label><input class="field" id="f_name" value="${esc(a.name || "")}" ${editing ? "disabled" : ""} placeholder="developer"/></div>
          <div class="fld"><label>Type ${infoIcon("Which CLI this agent runs. It MUST match the Command below or turn-completion never fires and the agent deadlocks as busy.")}</label><select class="field" id="f_type">${typeOpts}</select></div>
          <div class="fld"><label>Capture ${infoIcon("How turn-completion is detected: auto (per-type default), hook, pane polling, or none. Leave on auto unless you know why to change it.")}</label><select class="field" id="f_capture">${captureOpts}</select></div>
        </div>
        <div class="fld" style="margin-top:.6rem"><label>Command (may embed secrets — stays local) ${infoIcon("Full shell command launching the CLI. May embed secrets and stays local. Must launch the CLI named in Type or the agent deadlocks.")}</label><input class="field" id="f_command" value="${esc(a.command || "")}" placeholder="claude --dangerously-skip-permissions"/></div>
        <div class="fld" style="margin-top:.6rem"><label>Can talk to (comma list, or *) ${infoIcon("Comma-separated agents this one may message, or * for all. Cooperative ACL, not OS isolation. Add user to let it reach you.")}</label><input class="field" id="f_talk" value="${esc(fmtCanTalk(a.can_talk_to))}" placeholder="orchestrator, user"/></div>
        <div class="fld" style="margin-top:.6rem"><label>Workdir (optional) ${infoIcon("Directory the agent runs in; blank uses the default. Pre-trusted on first launch so a trust modal cannot swallow its first prompt.")}</label><input class="field" id="f_workdir" value="${esc(a.workdir || "")}" placeholder="leave blank for default"/></div>
        <div class="fld" style="margin-top:.6rem"><label>Role / standing instructions ${infoIcon("Standing persona and job baked into the first prompt; who the agent is and what it does. Pings and mail build on this.")}</label><textarea class="field" id="f_role" rows="4" placeholder="You are the developer…">${esc(a.role || "")}</textarea></div>
        <div class="fld" style="margin-top:.6rem">
          <label>Scheduled pings <span class="muted" style="font-weight:500">— cron message per schedule, in server-local time. Overrides "Ping every".</span></label>
          <div id="f_pings"></div>
          <button class="btn ghost sm" id="f_addping" type="button">+ Add schedule</button>${infoIcon("Add a cron-scheduled ping: a message sent on a schedule (e.g. work hours vs weekends).")}
        </div>
        <div class="rowend">
          <button class="btn ghost" id="f_cancel">Cancel</button>${infoIcon("Discard changes and close the form.")}
          <button class="btn" id="f_save">${editing ? "Save" : "Add agent"}</button>${infoIcon("Validate and create or update the agent, then start its session. A type and command mismatch is rejected to avoid a silent deadlock.")}
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

  // One editable cron-ping rule: message + 5-field cron + busy policy. Cron is
  // validated server-side on save (a bad expression comes back as an error
  // toast), so the field is free text with a placeholder hint.
  const BUSY_OPTS = ["skip", "queue"];
  function addPingRow(rule) {
    const box = $("f_pings"); if (!box) return;
    const busy = BUSY_OPTS.map((o) =>
      `<option value="${o}" ${((rule.when_busy || "skip") === o) ? "selected" : ""}>${o}</option>`).join("");
    const row = el(`
      <div class="pingrow">
        <input class="field ping-msg" value="${esc(rule.message || "")}" placeholder="Ping message…"/>${infoIcon("What the agent is told when this schedule fires.")}
        <input class="field ping-cron" value="${esc(rule.cron || "")}" placeholder="*/30 9-18 * * 1-5"/>${infoIcon("5-field cron: minute hour day-of-month month day-of-week (server-local time). Supports */step, ranges, lists, and mon-fri / jan names.")}
        <select class="field ping-busy">${busy}</select>${infoIcon("If the agent is mid-turn when this fires: skip (drop it, keep the mailbox clean) or queue (wait for the turn to end).")}
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
      // Drop fully-blank rows; a half-filled row is kept so the server returns a
      // clear validation error rather than silently discarding it.
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
      req = apiPost("/api/agent/edit", { name, fields: payload });
    } else {
      const n = g("f_name").trim();
      if (!n) { toast("name is required"); return; }
      if (!payload.command) { toast("command is required"); return; }
      req = apiPost("/api/agent/add", Object.assign({ name: n }, payload));
    }
    req.then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast(editing ? "saved " + name : "added " + res.j.name);
      close(); renderSettings(); pollStatus();
    });
  }

  // ---- availability ------------------------------------------------------

  function toggleAvailability() {
    const val = $("availToggle").checked;
    apiPost("/api/availability", { available: val }).then((res) => {
      if (!res.ok) { $("availToggle").checked = !val; toast("error: " + (res.j.error || "failed")); return; }
      syncAvailability(val);
      toast(val ? "you're available for mail" : "you're away");
    });
  }

  // ---- wire up -----------------------------------------------------------

  $("connect").addEventListener("click", connect);
  $("token").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
  // The logo is a "home" button once connected (nav is hidden before login).
  function goHome() { if (!$("nav").hidden) go("agents"); }
  $("brand").addEventListener("click", goHome);
  $("brand").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goHome(); }
  });
  $("availToggle").addEventListener("change", toggleAvailability);
  for (const b of document.querySelectorAll(".navbtn"))
    b.addEventListener("click", () => go(b.dataset.view));
  // Delegated: Start buttons are re-created by status polls, so listen once here
  // instead of re-wiring on every render. (The card's own onclick skips these
  // buttons directly, since this document-level handler bubbles too late to.)
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

  // Delegated ⓘ tooltip engine: hover to peek, click/tap to pin. One popover
  // node is reused. Works for any element carrying data-tip, present or future.
  function initTips() {
    let pop = null, curr = null, pinned = false;
    const node = () => {
      if (!pop) {
        pop = document.createElement("div");
        pop.className = "tip-pop";
        document.body.appendChild(pop);
      }
      return pop;
    };
    function place(el) {
      const p = node(), r = el.getBoundingClientRect();
      p.style.left = "-9999px"; p.style.top = "0px";           // measure off-screen
      p.classList.add("show");
      const pw = p.offsetWidth, ph = p.offsetHeight, gap = 8;
      let left = r.left + r.width / 2 - pw / 2;
      let top = r.bottom + gap;
      if (top + ph > innerHeight - gap) top = r.top - ph - gap; // flip above if no room
      left = Math.max(gap, Math.min(left, innerWidth - pw - gap));
      top = Math.max(gap, top);
      p.style.left = left + "px"; p.style.top = top + "px";
    }
    function show(el, pin) {
      const t = el.getAttribute("data-tip");
      if (!t) return;
      const p = node();
      p.textContent = t;
      if (pin) {
        const hint = document.createElement("span");
        hint.className = "tip-hint";
        hint.textContent = "Tap outside or press Esc to close";
        p.appendChild(hint);
      }
      p.classList.toggle("pinned", !!pin);
      curr = el; place(el);
      if (el.classList.contains("info")) el.classList.add("open");
    }
    function hide() {
      if (pop) pop.classList.remove("show", "pinned");
      if (curr && curr.classList) curr.classList.remove("open");
      curr = null; pinned = false;
    }
    const tipTarget = (t) => (t && t.closest) ? t.closest("[data-tip]") : null;
    document.addEventListener("mouseover", (e) => {
      if (pinned) return;
      const el = tipTarget(e.target);
      if (el) show(el, false);
    });
    document.addEventListener("mouseout", (e) => {
      if (pinned) return;
      if (tipTarget(e.target)) hide();
    });
    document.addEventListener("click", (e) => {
      const el = tipTarget(e.target);
      if (el) {
        e.stopPropagation(); e.preventDefault();
        if (pinned && curr === el) { hide(); }
        else { pinned = true; show(el, true); }
      } else if (pinned) {
        hide();
      }
    }, true); // capture: pin the ⓘ before a parent card/button click handler fires
    document.addEventListener("keydown", (e) => {
      const el = document.activeElement;
      if ((e.key === "Enter" || e.key === " ") && el && el.classList && el.classList.contains("info")) {
        e.preventDefault();
        if (pinned && curr === el) { hide(); } else { pinned = true; show(el, true); }
      } else if (e.key === "Escape" && pinned) { hide(); }
    });
    addEventListener("scroll", () => { if (curr && pop && pop.classList.contains("show")) place(curr); }, true);
    addEventListener("resize", hide);
  }
})();
