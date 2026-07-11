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
  };
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

  // ---- API ---------------------------------------------------------------

  function withToken(path) { return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN); }
  function apiGet(path) {
    return fetch(withToken(path), { headers: { Accept: "application/json" } }).then((r) => {
      if (r.status === 401) throw new Error("unauthorized");
      return r.json();
    });
  }
  function apiPost(path, body) {
    return fetch(withToken(path), {
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
      banner("");
      syncAvailability(data.user_available);
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
    if (view === "agents") renderAgents();
    else if (view === "mail") renderMail();
    else if (view === "activity") renderActivity();
    else if (view === "settings") renderSettings();
  }

  // ---- agents overview ---------------------------------------------------

  function statusPills(a) {
    const run = a.running ? '<span class="pill ok"><span class="dotpulse"></span>running</span>'
                          : '<span class="pill no">stopped</span>';
    const st = a.busy ? '<span class="pill busy">busy</span>' : '<span class="pill mute">idle</span>';
    const un = a.unread ? `<span class="pill busy">${a.unread} unread</span>` : "";
    const q = a.queue_depth ? `<span class="pill mute">${a.queue_depth} queued</span>` : "";
    return run + st + un + q;
  }

  function renderAgents() {
    const agents = (state.status && state.status.agents) || [];
    const cards = agents.map((a) => `
      <div class="card agentcard" data-agent="${esc(a.name)}">
        <div class="top">
          ${avatar(a.name)}
          <div style="min-width:0">
            <div class="name">${esc(a.name)}</div>
            <div class="muted" style="font-size:.8rem">${esc(a.type)}</div>
          </div>
        </div>
        <div class="role" data-role="${esc(a.name)}">${esc(a.role_preview || "")}</div>
        <div class="meta">${statusPills(a)}</div>
        <div class="muted" style="font-size:.78rem">talks to: ${esc((a.can_talk_to || []).join(", ") || "—")}</div>
      </div>`).join("");
    $("view").innerHTML = `
      <div class="sectiontitle">
        <h2>Agents <span class="muted" style="font-weight:500">(${agents.length})</span></h2>
        <button class="btn ghost sm" id="refreshBtn">Refresh</button>
      </div>
      ${topologyCard(agents)}
      <div class="grid">${cards || '<p class="empty">No agents configured. Add one in Settings.</p>'}</div>`;
    $("refreshBtn").onclick = pollStatus;
    for (const c of document.querySelectorAll(".agentcard"))
      c.onclick = () => openAgent(c.dataset.agent);
    // Lazily enrich each card's role text (kept out of /api/status to stay light).
    agents.forEach((a) => apiGet("/api/agent?agent=" + encodeURIComponent(a.name))
      .then((d) => { const n = document.querySelector(`[data-role="${cssq(a.name)}"]`); if (n) n.textContent = (d.agent && d.agent.role) || "(no role set)"; })
      .catch(() => {}));
    timers.status = setInterval(pollStatus, 5000);
  }

  function cssq(s) { return String(s).replace(/"/g, '\\"'); }

  function pollStatus() {
    apiGet("/api/status").then((data) => {
      state.status = data;
      syncAvailability(data.user_available);
      $("swarmMeta").textContent = (data.name || "swarm") + " · " + ((data.agents || []).length) + " agents";
      if (state.view === "agents") {
        // Update only the pill rows in place so we don't stomp scroll / role text.
        (data.agents || []).forEach((a) => {
          const card = document.querySelector(`.agentcard[data-agent="${cssq(a.name)}"] .meta`);
          if (card) card.innerHTML = statusPills(a);
        });
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
        edges += `<line x1="${s.x + (dx / len) * R}" y1="${s.y + (dy / len) * R}" x2="${t.x - (dx / len) * R}" y2="${t.y - (dy / len) * R}" class="edge" marker-end="url(#arrow)"/>`;
      }
    }));
    const circles = nodes.map((n) => {
      const p = pos[n];
      const fill = n === "user" ? "hsl(215 62% 48%)" : `hsl(${hueFor(n)} 62% 48%)`;
      return `<g><circle cx="${p.x}" cy="${p.y}" r="19" fill="${fill}"/>
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

  function renderActivity() {
    $("view").innerHTML = `
      <div class="sectiontitle">
        <h2>Activity</h2>
        <button class="btn ghost sm" id="refreshBtn">Refresh</button>
      </div>
      <div class="card" style="padding:.3rem .2rem"><div id="timeline" class="timeline"><p class="empty">Loading…</p></div></div>`;
    $("refreshBtn").onclick = loadTimeline;
    loadTimeline();
    timers.activity = setInterval(loadTimeline, 5000);
  }

  function loadTimeline() {
    apiGet("/api/logs?n=250").then((d) => {
      const logs = (d.logs || []).slice().reverse(); // newest first
      const box = $("timeline"); if (!box) return;
      box.innerHTML = logs.map((r) => {
        const kind = r.kind || "?";
        const cls = KIND_CLASS[kind] || "mute";
        const route = [r.from_, r.to].filter(Boolean).join(" → ");
        const extra = [route, r.id, r.reason].filter(Boolean).map(esc).join(" · ");
        return `<div class="event">
          <span class="t">${esc(fmtTime(r.ts))}</span>
          <span class="pill ${cls}">${esc(kind)}</span>
          <b>${esc(r.agent || "")}</b>
          <span class="muted">${extra}</span></div>`;
      }).join("") || '<p class="empty">No events yet.</p>';
    }).catch((e) => banner(e.message));
  }

  // ---- mail app ----------------------------------------------------------

  function openAgent(name) {
    state.agent = name;
    state.peer = "user";
    state.tab = "mail";
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
        <button class="btn ghost sm" id="backBtn">← Agents</button>
        <div class="who">${avatar(agent, "sm")}<h2>${esc(agent)}</h2><span id="mailStatus"></span></div>
        <span style="flex:1"></span>
        <div class="tabs">
          <button class="tab ${state.tab === "mail" ? "active" : ""}" data-tab="mail">Mail</button>
          <button class="tab ${state.tab === "terminal" ? "active" : ""}" data-tab="terminal">Terminal</button>
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
          <span class="muted" style="font-size:.78rem">capture-pane · refreshes 2s</span>
        </div>
        <pre class="pane" id="pane">— loading —</pre>
        <div class="typerow">
          <input class="field" id="typeText" placeholder="Type straight into ${esc(state.agent)}'s session, press Enter…" />
          <button class="btn" id="typeSend">Send</button>
        </div>
        <p class="muted" style="font-size:.8rem;margin:.5rem 0 0">Types directly into the tmux pane (bypasses mail). An empty pane means the agent's session isn't running.</p>
      </div>`;
    const inp = $("typeText");
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); sendType(); } });
    $("typeSend").onclick = sendType;
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
      const time = c.last_time ? `<span class="t">${esc(fmtTime(c.last_time))}</span>` : "";
      const badge = c.unread ? `<span class="badge">${c.unread}</span>` : "";
      return `
        <div class="contact ${c.name === state.peer ? "active" : ""}" data-peer="${esc(c.name)}">
          ${avatar(c.name, "sm")}
          <div class="info">
            <div class="cn"><b>${esc(label)}</b>${time}</div>
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
    const atBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 60;
    if (!state.thread.length) {
      scroll.innerHTML = `<p class="empty">No messages between <b>${esc(state.agent)}</b> and <b>${esc(state.peer === "user" ? "you" : state.peer)}</b> yet.</p>`;
    } else {
      scroll.innerHTML = state.thread.map((m) => {
        const cls = m.from === "system" ? "system" : m.direction === "out" ? "out" : "in";
        const head = cls === "system" ? "system" : `${esc(m.from)} → ${esc(m.to)} · ${esc(fmtTime(m.time))}`;
        const status = cls === "system" ? "" : statusTag(m.status);
        return `<div class="msg ${cls}"><div class="m-head">${head}</div><div class="m-body">${md(m.body.trim())}</div>${status}</div>`;
      }).join("");
    }
    if (atBottom) scroll.scrollTop = scroll.scrollHeight;
    renderCompose();
  }

  // Delivery status of one message, from where it currently sits in the mailroom.
  function statusTag(s) {
    const map = {
      queued: ["◷", "waiting"], delivered: ["✓", "delivered"],
      read: ["✓✓", "read"], archived: ["⤓", "archived"],
    };
    const e = map[s];
    return e ? `<div class="m-status s-${s}">${e[0]} ${e[1]}</div>` : "";
  }

  function renderCompose() {
    const area = $("composeArea"); if (!area) return;
    if (state.peer === "user") {
      area.innerHTML = `
        <div class="compose">
          <textarea class="field" id="reply" rows="1" placeholder="Message ${esc(state.agent)} as the user…"></textarea>
          <button class="btn" id="sendReply">Send</button>
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

  function loadPane() {
    apiGet("/api/pane?agent=" + encodeURIComponent(state.agent)).then((d) => {
      const p = $("pane"); if (p) p.textContent = d.pane || "— (empty / session down) —";
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
          <button class="btn ghost sm" data-edit="${esc(a.name)}">Edit</button>
          <button class="btn danger sm" data-del="${esc(a.name)}">Delete</button>
        </div>`).join("");
      $("view").innerHTML = `
        <div class="settings">
          <div class="card panel">
            <h3>Swarm settings</h3>
            <p class="muted" style="margin-top:.1rem">Saved straight to <code>${esc(cfg.path || "agentainer.yaml")}</code>.</p>
            <div class="formgrid">${fields}</div>
            <div class="rowend"><button class="btn" id="saveSwarm">Save settings</button></div>
          </div>
          ${telegramCard(tg, cfg.agents || [])}
          <div class="card panel">
            <div class="sectiontitle"><h3>Agents</h3><button class="btn sm" id="addAgent">+ Add agent</button></div>
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
      <label class="row" style="gap:.3rem"><input type="checkbox" class="tg-agent" value="${esc(a.name)}" ${sel.includes(a.name) ? "checked" : ""} ${allScope ? "disabled" : ""}/> ${esc(a.name)}</label>`).join("");
    return `
      <div class="card panel">
        <div class="sectiontitle"><h3>Telegram bridge</h3>
          <span class="pill ${tg.enabled ? "ok" : "mute"}">${tg.enabled ? "on" : "off"}</span></div>
        <p class="muted" style="margin:.1rem 0 .6rem">Mirror the swarm's mail to a Telegram chat and reply from your phone. Uses the Bot API over HTTPS — zero dependencies. Create a bot with <b>@BotFather</b>, then get your numeric chat id from <b>@userinfobot</b>.</p>
        <div class="formgrid">
          <div class="fld"><label>Enabled</label><label class="row"><input type="checkbox" id="tg_enabled" ${tg.enabled ? "checked" : ""}/> <span class="muted">mirror on</span></label></div>
          <div class="fld"><label>Bot token</label><input class="field" id="tg_token" type="password" placeholder="${tg.has_token ? "•••• stored — blank keeps it" : "123456:ABC-DEF…"}"/></div>
          <div class="fld"><label>Chat ID</label><input class="field" id="tg_chat" value="${esc(tg.chat_id || "")}" placeholder="e.g. 123456789"/></div>
          <div class="fld"><label>Mirror your mail</label><label class="row"><input type="checkbox" id="tg_muser" ${tg.mirror_user ? "checked" : ""}/> <span class="muted">mail to you</span></label></div>
          <div class="fld"><label>Mirror system</label><label class="row"><input type="checkbox" id="tg_msys" ${tg.mirror_system ? "checked" : ""}/> <span class="muted">pings/bounces</span></label></div>
        </div>
        <div class="fld" style="margin-top:.6rem"><label>Which agents to mirror</label>
          <div class="row">
            <label class="row" style="gap:.3rem"><input type="radio" name="tgscope" value="all" ${allScope ? "checked" : ""}/> all agents</label>
            <label class="row" style="gap:.3rem"><input type="radio" name="tgscope" value="sel" ${allScope ? "" : "checked"}/> selected</label>
          </div>
          <div class="row" id="tg_agents" style="margin-top:.4rem;opacity:${allScope ? ".5" : "1"}">${checks || '<span class="muted">no agents</span>'}</div>
        </div>
        <div class="rowend">
          <button class="btn ghost" id="tg_test">Send test</button>
          <button class="btn ghost" id="tg_poll">${tg.polling ? "Stop replies" : "Receive replies"}</button>
          <button class="btn" id="tg_save">Save Telegram</button>
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
    apiPost("/api/telegram", collectTelegram()).then((res) => {
      if (!res.ok) { toast("error: " + (res.j.error || "failed")); return; }
      toast("Telegram settings saved");
      renderSettings();
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
          <div class="fld"><label>Name</label><input class="field" id="f_name" value="${esc(a.name || "")}" ${editing ? "disabled" : ""} placeholder="developer"/></div>
          <div class="fld"><label>Type</label><select class="field" id="f_type">${typeOpts}</select></div>
          <div class="fld"><label>Capture</label><select class="field" id="f_capture">${captureOpts}</select></div>
          <div class="fld"><label>Ping every (s, 0=off)</label><input class="field" id="f_ping" type="number" value="${esc(a.periodically_ping_seconds || 0)}"/></div>
        </div>
        <div class="fld" style="margin-top:.6rem"><label>Command (may embed secrets — stays local)</label><input class="field" id="f_command" value="${esc(a.command || "")}" placeholder="claude --dangerously-skip-permissions"/></div>
        <div class="fld" style="margin-top:.6rem"><label>Can talk to (comma list, or *)</label><input class="field" id="f_talk" value="${esc(fmtCanTalk(a.can_talk_to))}" placeholder="orchestrator, user"/></div>
        <div class="fld" style="margin-top:.6rem"><label>Workdir (optional)</label><input class="field" id="f_workdir" value="${esc(a.workdir || "")}" placeholder="leave blank for default"/></div>
        <div class="fld" style="margin-top:.6rem"><label>Role / standing instructions</label><textarea class="field" id="f_role" rows="4" placeholder="You are the developer…">${esc(a.role || "")}</textarea></div>
        <div class="rowend">
          <button class="btn ghost" id="f_cancel">Cancel</button>
          <button class="btn" id="f_save">${editing ? "Save" : "Add agent"}</button>
        </div>
      </div></div>`);
    $("modalRoot").appendChild(modal);
    const close = () => modal.remove();
    modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
    modal.querySelector("#f_cancel").onclick = close;
    modal.querySelector("#f_save").onclick = () => saveAgentForm(editing, name, close);
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
      periodically_ping_seconds: Number(g("f_ping")) || 0,
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
  $("availToggle").addEventListener("change", toggleAvailability);
  for (const b of document.querySelectorAll(".navbtn"))
    b.addEventListener("click", () => go(b.dataset.view));
})();
