const els = {
  ownerKey: document.getElementById("ownerKey"),
  saveKey: document.getElementById("saveKey"),
  status: document.getElementById("status"),
  reportList: document.getElementById("reportList"),
  detailTitle: document.getElementById("detailTitle"),
  detailBadge: document.getElementById("detailBadge"),
  detailDescription: document.getElementById("detailDescription"),
  detailSummary: document.getElementById("detailSummary"),
  detailSnapshot: document.getElementById("detailSnapshot"),
  issueList: document.getElementById("issueList"),
};

let currentReportId = "";

function ownerKey() {
  return localStorage.getItem("graphrag_owner_key") || "";
}

function setStatus(message, kind = "") {
  els.status.textContent = message;
  els.status.className = `status ${kind}`.trim();
}

async function apiFetch(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      ...(options.headers || {}),
      "X-API-Key": ownerKey(),
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return res.json();
  }
  return res;
}

function badgeClass(status) {
  if (status === "ok") return "badge ok";
  if (status === "failed") return "badge failed";
  return "badge never-built";
}

function renderIssues(issues) {
  if (!issues || !issues.length) {
    els.issueList.innerHTML = `<div class="issue">Không có lỗi quality gate.</div>`;
    return;
  }
  els.issueList.innerHTML = issues
    .map(
      (issue) => `
        <div class="issue ${issue.level === "error" ? "error" : "warning"}">
          <strong>${issue.code}</strong><br>${issue.message}
        </div>`
    )
    .join("");
}

function renderDetail(detail) {
  const def = detail.definition;
  const manifest = detail.build_manifest || {};
  const snapshot = detail.snapshot || {};
  els.detailTitle.textContent = def.title;
  els.detailBadge.textContent = manifest.status || "never-built";
  els.detailBadge.className = badgeClass(manifest.status || "never-built");
  els.detailDescription.textContent = def.description || "Không có mô tả.";
  els.detailSummary.textContent = JSON.stringify(
    {
      report_id: def.id,
      kind: def.kind,
      source_markdown_glob: def.source_markdown_glob,
      template_path: def.template_path,
      output_path: def.output_path,
      generated_at: manifest.generated_at || null,
      report_date: manifest.report_date || null,
      artifact_exists: detail.artifact_exists,
      summary: manifest.summary || null,
    },
    null,
    2
  );
  els.detailSnapshot.textContent = JSON.stringify(
    {
      report_date: snapshot.report_date || null,
      template_date: snapshot.template_date || null,
      metrics: snapshot.metrics || null,
      goals: snapshot.goals || [],
      lane_tables: snapshot.lane_tables || {},
    },
    null,
    2
  );
  renderIssues(manifest.issues || []);
}

function cardHtml(report) {
  return `
    <article class="report-card ${report.id === currentReportId ? "active" : ""}" data-report-id="${report.id}">
      <div class="report-head">
        <div>
          <h3>${report.title}</h3>
          <div class="muted">${report.kind}</div>
        </div>
        <span class="${badgeClass(report.status)}">${report.status}</span>
      </div>
      <div class="meta">
        <div><strong>Ngày báo cáo:</strong> ${report.report_date || "chưa có"}</div>
        <div><strong>Lần build gần nhất:</strong> ${report.last_generated_at || "chưa build"}</div>
        <div>${report.description || ""}</div>
      </div>
      <div class="actions">
        <button data-open="${report.id}" class="secondary">Xem chi tiết</button>
        <button data-build="${report.id}">Build</button>
        ${report.artifact_exists ? `<a class="button-link secondary" href="/reports/${report.id}/artifact" target="_blank">Tải file</a>` : ""}
      </div>
    </article>`;
}

async function loadReportList() {
  const reports = await apiFetch("/reports");
  els.reportList.innerHTML = reports.map(cardHtml).join("");
  els.reportList.querySelectorAll("[data-open]").forEach((btn) => {
    btn.addEventListener("click", () => openReport(btn.dataset.open));
  });
  els.reportList.querySelectorAll("[data-build]").forEach((btn) => {
    btn.addEventListener("click", () => buildReport(btn.dataset.build));
  });
}

async function openReport(reportId) {
  currentReportId = reportId;
  const detail = await apiFetch(`/reports/${reportId}`);
  renderDetail(detail);
  await loadReportList();
}

async function buildReport(reportId) {
  try {
    setStatus(`Đang build ${reportId}...`);
    await apiFetch(`/reports/${reportId}/build`, { method: "POST" });
    setStatus(`Đã chạy build cho ${reportId}.`, "ok");
    await openReport(reportId);
  } catch (error) {
    setStatus(`Lỗi build: ${error.message}`, "error");
  }
}

async function boot() {
  try {
    await loadReportList();
    const firstCard = els.reportList.querySelector("[data-report-id]");
    if (firstCard) {
      await openReport(firstCard.dataset.reportId);
    }
  } catch (error) {
    setStatus(`Không tải được vùng báo cáo: ${error.message}`, "error");
  }
}

els.saveKey.addEventListener("click", async () => {
  localStorage.setItem("graphrag_owner_key", els.ownerKey.value.trim());
  setStatus("Đã lưu OWNER_API_KEY.", "ok");
  await boot();
});

els.ownerKey.value = ownerKey();
boot();
