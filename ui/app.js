"use strict";
// Agentainer UI -- vanilla JS, no framework, no build step, no CDN.
// The token is sent on every request via ?token= (simplest, works everywhere).

(function () {
  const $ = (id) => document.getElementById(id);

  let TOKEN = "";
  let BASE = ""; // e.g. "" (same origin) -- we just append ?token=
  let pollTimer = null;
  let logTimer = null;

  function api(path) {
    // path already includes a leading "/" and any query string; we append token.
    const sep = path.includes("?") ? "&" : "?";
    const url = path + sep + "token=" + encodeURIComponent(TOKEN);
    return fetch(url, { headers: { Accept: "application/json" } }).then((r) => {
      if (r.status === 401) throw new Error("unauthorized");
      return r.json();
    });
  }

  function pill(text, cls) {
    return '<span class="pill ' + cls + '">' + text + "</span>";
  }

  function renderStatus(data) {
    $("meta").textContent =
      "swarm: " + (data.name || "?") + "  ·  root: " + (data.root || "?");
    const sup = data.supervisor_alive;
    $("sup").textContent =
      "supervisor: " +
      (sup === null ? "n/a (module absent)" : sup ? "alive" : "not running");

    const tbody = $("agents").querySelector("tbody");
    tbody.innerHTML = "";
    const to = $("to");
    const logAgent = $("logAgent");
    to.innerHTML = "";
    logAgent.innerHTML = '<option value="">(all)</option>';

    (data.agents || []).forEach((a) => {
      const tr = document.createElement("tr");
      const state = a.busy ? pill("busy", "busy") : pill("idle", "ok");
      const running = a.running ? pill("yes", "ok") : pill("no", "no");
      tr.innerHTML =
        "<td>" + esc(a.name) + "</td>" +
        "<td>" + esc(a.type) + "</td>" +
        "<td>" + running + "</td>" +
        "<td>" + state + "</td>" +
        "<td>" + a.queue_depth + "</td>" +
        "<td>" + a.unread + "</td>" +
        "<td>" + esc((a.can_talk_to || []).join(", ") || "—") + "</td>";
      tbody.appendChild(tr);

      const o1 = document.createElement("option");
      o1.value = a.name;
      o1.textContent = a.name;
      to.appendChild(o1);

      const o2 = document.createElement("option");
      o2.value = a.name;
      o2.textContent = a.name;
      logAgent.appendChild(o2);
    });
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
    );
  }

  function refresh() {
    api("/api/status")
      .then(renderStatus)
      .catch((e) => banner(e.message));
  }

  function banner(msg) {
    $("banner").textContent = msg || "";
  }

  function send() {
    const to = $("to").value;
    const text = $("text").value;
    if (!to || !text.trim()) {
      $("sendStatus").textContent = "need a recipient and a message";
      return;
    }
    const sep = "/api/send".includes("?") ? "&" : "?";
    fetch("/api/send" + sep + "token=" + encodeURIComponent(TOKEN), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to: to, text: text }),
    })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j: j })))
      .then((res) => {
        if (!res.ok) {
          $("sendStatus").textContent = "error: " + (res.j.error || "failed");
          return;
        }
        $("sendStatus").textContent = "sent to " + to;
        $("text").value = "";
      })
      .catch((e) => ($("sendStatus").textContent = "error: " + e.message));
  }

  function loadLogs() {
    const agent = $("logAgent").value;
    const n = $("logN").value || 50;
    let path = "/api/logs?n=" + encodeURIComponent(n);
    if (agent) path += "&agent=" + encodeURIComponent(agent);
    api(path)
      .then((data) => {
        const lines = (data.logs || []).map((r) =>
          typeof r === "object" ? JSON.stringify(r) : String(r)
        );
        $("logs").textContent =
          lines.length ? lines.join("\n") : "-- no log lines --";
      })
      .catch((e) => ($("logs").textContent = "error: " + e.message));
  }

  function connect() {
    TOKEN = $("token").value.trim();
    if (!TOKEN) {
      banner("enter a token first");
      return;
    }
    api("/api/status")
      .then((data) => {
        $("login").hidden = true;
        $("app").hidden = false;
        banner("");
        renderStatus(data);
        startPolling();
      })
      .catch((e) => banner("connect failed: " + e.message));
  }

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(refresh, 4000);
  }

  function startLogPolling() {
    if (logTimer) clearInterval(logTimer);
    if ($("autoLogs").checked) logTimer = setInterval(loadLogs, 5000);
  }

  // wire up
  $("connect").addEventListener("click", connect);
  $("refresh").addEventListener("click", refresh);
  $("send").addEventListener("click", send);
  $("loadLogs").addEventListener("click", loadLogs);
  $("autoLogs").addEventListener("change", startLogPolling);
})();
