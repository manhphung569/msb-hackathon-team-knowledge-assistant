const els = {
  ownerKey: document.getElementById("ownerKey"),
  saveKey: document.getElementById("saveKey"),
  refreshBtn: document.getElementById("refreshBtn"),
  status: document.getElementById("status"),
  summaryGrid: document.getElementById("summaryGrid"),
  runsList: document.getElementById("runsList"),
  runsCountChip: document.getElementById("runsCountChip"),
  alertsBody: document.getElementById("alertsBody"),
  alertsCountChip: document.getElementById("alertsCountChip"),
  filterProject: document.getElementById("filterProject"),
  filterAgent: document.getElementById("filterAgent"),
  filterSeverity: document.getElementById("filterSeverity"),
};

let lastReport = null;

function getOwnerKey() {
  return localStorage.getItem("graphrag_owner_key") || "";
}

function setStatus(message, kind) {
  els.status.textContent = message;
  els.status.className = kind || "";
}

function formatDate(value) {
  if (!value) return "n/a";
  const date = new Date(value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("vi-VN");
}

function severityChipClass(sev) {
  const s = (sev || "").toUpperCase();
  if (s === "CAO") return "danger";
  if (s === "TRUNG BINH" || s === "TRUNG-BINH" || s === "MEDIUM") return "warn";
  return "neutral";
}

async function jsonFetch(path) {
  const res = await fetch(path, { headers: { "X-API-Key": getOwnerKey() } });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

function renderSummary(summary) {
  const bySeverity = summary.alerts_by_severity || {};
  const caoCount = bySeverity["CAO"] || 0;
  const cards = [
    {
      title: "Tong ALERT",
      value: summary.total_alerts ?? 0,
      note: "Gom tu moi ALERTS.md dang co",
      tone: "neutral",
    },
    {
      title: "ALERT muc CAO",
      value: caoCount,
      note: "Can xu ly / escalate ngay",
      tone: caoCount > 0 ? "danger" : "ok",
    },
    {
      title: "So lan orchestrator chay",
      value: summary.total_runs ?? 0,
      note: "Tinh tu khi bat dau ghi run-log",
      tone: "neutral",
    },
    {
      title: "Lan chay gan nhat",
      value: formatDate(summary.last_run_time),
      note: "Thoi diem orchestrator chay lan cuoi",
      tone: "neutral",
      small: true,
    },
  ];
  els.summaryGrid.innerHTML = cards
    .map(
      (c) => `
        <article class="panel metric-card ${c.tone}">
          <p class="tiny-label">${c.title}</p>
          <strong class="metric-value" style="${c.small ? "font-size:20px" : ""}">${c.value}</strong>
          <p class="metric-note">${c.note}</p>
        </article>`
    )
    .join("");
}

function agentChip(agent) {
  const status = (agent.status || "ok").toLowerCase();
  const tone = status === "error" ? "danger" : status === "skipped" ? "neutral" : "ok";
  return `<span class="chip ${tone}">${agent.name}${agent.status ? ` (${agent.status})` : ""}</span>`;
}

function renderRuns(runs) {
  els.runsCountChip.textContent = `${runs.length} lan`;
  if (!runs.length) {
    els.runsList.innerHTML = `<li><div class="empty-state">Chua co lan orchestrator nao duoc ghi run-log. Se xuat hien sau lan chay tiep theo.</div></li>`;
    return;
  }
  els.runsList.innerHTML = runs
    .map((run) => {
      const agents = (run.agents || []).map(agentChip).join(" ");
      const projects = run.projects && run.projects.length ? run.projects.join(", ") : run.scope;
      const alertsNew = run.alerts_new || [];
      const alertsList = alertsNew.length
        ? `<ul>${alertsNew
            .map((a) => `<li><strong>[${a.severity || "?"}] ${a.agent || "?"}</strong> (${a.project || ""}): ${a.message || ""}</li>`)
            .join("")}</ul>`
        : "";
      return `
        <li class="run-item">
          <div class="run-item-head">
            <strong>${formatDate(run.time)} — ${run.trigger || "manual"}</strong>
            <span class="chip ${alertsNew.length ? "danger" : "ok"}">${alertsNew.length} alert moi</span>
          </div>
          <div class="muted">Pham vi: ${projects}</div>
          <div class="agent-chips">${agents}</div>
          ${run.summary ? `<div class="muted" style="margin-top:8px">${run.summary}</div>` : ""}
          ${alertsList}
        </li>`;
    })
    .join("");
}

function populateFilterOptions(alerts) {
  const projects = [...new Set(alerts.map((a) => a.project).filter(Boolean))].sort();
  const agents = [...new Set(alerts.map((a) => a.agent).filter(Boolean))].sort();
  const severities = [...new Set(alerts.map((a) => a.severity).filter(Boolean))].sort();

  const fill = (select, values, placeholder) => {
    const current = select.value;
    select.innerHTML =
      `<option value="">${placeholder}</option>` + values.map((v) => `<option value="${v}">${v}</option>`).join("");
    if (values.includes(current)) select.value = current;
  };

  fill(els.filterProject, projects, "Tat ca du an");
  fill(els.filterAgent, agents, "Tat ca agent");
  fill(els.filterSeverity, severities, "Tat ca muc do");
}

function renderAlerts(alerts) {
  const projectFilter = els.filterProject.value;
  const agentFilter = els.filterAgent.value;
  const severityFilter = els.filterSeverity.value;

  const filtered = alerts.filter(
    (a) =>
      (!projectFilter || a.project === projectFilter) &&
      (!agentFilter || a.agent === agentFilter) &&
      (!severityFilter || a.severity === severityFilter)
  );

  els.alertsCountChip.textContent = `${filtered.length} / ${alerts.length} alert`;

  if (!filtered.length) {
    els.alertsBody.innerHTML = `<tr><td colspan="5"><div class="empty-state">Khong co alert nao khop bo loc.</div></td></tr>`;
    return;
  }

  els.alertsBody.innerHTML = filtered
    .map(
      (a) => `
        <tr>
          <td>${formatDate(a.time)}</td>
          <td><span class="chip ${severityChipClass(a.severity)}">${a.severity}</span></td>
          <td>${a.agent}</td>
          <td>${a.project}<br><span class="muted"><code>${a.ticket}</code></span></td>
          <td>${a.message}</td>
        </tr>`
    )
    .join("");
}

async function loadAgentOps() {
  if (!getOwnerKey()) {
    setStatus("Can OWNER_API_KEY de xem trang nay — dan key roi bam Luu key.", "error");
    els.summaryGrid.innerHTML = "";
    els.runsList.innerHTML = "";
    els.alertsBody.innerHTML = "";
    return;
  }
  setStatus("Dang tai du lieu agent ops...", "");
  try {
    const report = await jsonFetch("/agent-ops");
    lastReport = report;
    renderSummary(report.summary || {});
    renderRuns(report.runs || []);
    populateFilterOptions(report.alerts || []);
    renderAlerts(report.alerts || []);
    setStatus("Da tai xong.", "ok");
  } catch (error) {
    setStatus(`Khong tai duoc /agent-ops: ${error.message}`, "error");
  }
}

els.saveKey.addEventListener("click", () => {
  localStorage.setItem("graphrag_owner_key", els.ownerKey.value.trim());
  setStatus("Da luu key. Dang tai lai...", "ok");
  loadAgentOps();
});

els.refreshBtn.addEventListener("click", loadAgentOps);
[els.filterProject, els.filterAgent, els.filterSeverity].forEach((sel) =>
  sel.addEventListener("change", () => {
    if (lastReport) renderAlerts(lastReport.alerts || []);
  })
);

els.ownerKey.value = getOwnerKey();
loadAgentOps();
