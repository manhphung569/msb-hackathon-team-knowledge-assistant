const els = {
  ownerKey: document.getElementById("ownerKey"),
  saveKey: document.getElementById("saveKey"),
  refreshBtn: document.getElementById("refreshBtn"),
  status: document.getElementById("status"),
  heroStatus: document.getElementById("heroStatus"),
  heroGeneratedAt: document.getElementById("heroGeneratedAt"),
  summaryGrid: document.getElementById("summaryGrid"),
  coverageGrid: document.getElementById("coverageGrid"),
  historyList: document.getElementById("historyList"),
  detailModeChip: document.getElementById("detailModeChip"),
  detailGrid: document.getElementById("detailGrid"),
  detailTables: document.getElementById("detailTables"),
};

function getOwnerKey() {
  return localStorage.getItem("graphrag_owner_key") || "";
}

function setStatus(message, kind) {
  els.status.textContent = message;
  els.status.className = kind || "";
}

function formatDate(value) {
  if (!value) return "n/a";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("vi-VN");
}

function formatSeconds(value) {
  if (typeof value !== "number") return "n/a";
  return `${value.toFixed(2)}s`;
}

function getMetricTone(key, value) {
  if (["docs_needing_normalization", "docs_needing_build", "orphaned_normalized_files"].includes(key)) {
    return value > 0 ? "danger" : "ok";
  }
  return "neutral";
}

function chipForCount(count) {
  if (count > 0) return "danger";
  return "ok";
}

async function jsonFetch(path, ownerOnly = false) {
  const headers = {};
  if (ownerOnly) {
    headers["X-API-Key"] = getOwnerKey();
  }
  const res = await fetch(path, { headers });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

function renderSummary(summary) {
  const labels = {
    artifact_files: ["Artifact files", "So file goc dang duoc quan ly"],
    normalized_files: ["Normalized files", "So file markdown da normalize"],
    indexed_docs: ["Indexed docs", "So document dang nam trong vector index"],
    docs_needing_normalization: ["Need normalization", "Artifact da doi nhung markdown chua theo kip"],
    docs_needing_build: ["Need build", "Markdown da doi nhung index chua theo kip"],
    orphaned_normalized_files: ["Orphaned normalized", "Markdown khong con artifact goc tuong ung"],
  };
  const order = [
    "artifact_files",
    "normalized_files",
    "indexed_docs",
    "docs_needing_normalization",
    "docs_needing_build",
    "orphaned_normalized_files",
  ];
  els.summaryGrid.innerHTML = order
    .map((key) => {
      const value = summary[key] ?? 0;
      const tone = getMetricTone(key, value);
      const [title, note] = labels[key];
      return `
        <article class="panel metric-card ${tone}">
          <p class="tiny-label">${title}</p>
          <strong class="metric-value">${value}</strong>
          <p class="metric-note">${note}</p>
        </article>`;
    })
    .join("");
}

function renderCoverage(coverage) {
  const order = [
    "project",
    "source_channel",
    "date",
    "reliability",
    "ticket_ids",
    "people_mentions",
    "document_type",
  ];
  els.coverageGrid.innerHTML = order
    .map((key) => {
      const item = coverage[key];
      if (!item) return "";
      return `
        <div class="coverage-item">
          <p class="tiny-label">${key.replaceAll("_", " ")}</p>
          <strong>${item.pct}%</strong>
          <div class="section-note">${item.count}/${item.total} document co field nay</div>
          <div class="progress"><span style="width:${item.pct}%"></span></div>
        </div>`;
    })
    .join("");
}

function renderBuildHistory(entries) {
  if (!entries || entries.length === 0) {
    els.historyList.innerHTML = `<li><span class="empty-state">Chua co build history.</span></li>`;
    return;
  }
  els.historyList.innerHTML = entries
    .map((entry) => {
      const changed = entry.changed_docs ?? 0;
      const chunks = entry.n_chunks ?? 0;
      const success = entry.success ? "thanh cong" : "that bai";
      return `
        <li>
          <div>
            <strong>${formatDate(entry.time)}</strong>
            <div class="muted">${success}, changed docs: ${changed}, chunks: ${chunks}</div>
            <div class="muted">provider: ${entry.provider || "n/a"} | model: ${entry.local_model || "n/a"}</div>
          </div>
          <div><strong>${formatSeconds(entry.duration_seconds)}</strong></div>
        </li>`;
    })
    .join("");
}

function renderDetailCards(summary, detail) {
  const cards = [
    {
      key: "missing_normalized",
      title: "Missing normalized",
      note: "Artifact co nhung chua co markdown mirror",
      count: detail?.stale_files?.missing_normalized?.length ?? summary.docs_needing_normalization ?? 0,
    },
    {
      key: "stale_normalized",
      title: "Stale normalized",
      note: "Artifact moi hon markdown",
      count: detail?.stale_files?.stale_normalized?.length ?? 0,
    },
    {
      key: "stale_build",
      title: "Stale build",
      note: "Markdown da doi nhung cache build chua cap nhat",
      count: detail?.stale_files?.stale_build?.length ?? summary.docs_needing_build ?? 0,
    },
    {
      key: "missing_indexed",
      title: "Missing indexed",
      note: "Doc co trong normalized nhung chua co trong vector index",
      count: detail?.stale_files?.missing_indexed?.length ?? 0,
    },
    {
      key: "orphaned_normalized",
      title: "Orphaned normalized",
      note: "Markdown khong con artifact goc",
      count: detail?.stale_files?.orphaned_normalized?.length ?? summary.orphaned_normalized_files ?? 0,
    },
  ];
  els.detailGrid.innerHTML = cards
    .map(
      (card) => `
        <div class="detail-card">
          <p class="tiny-label">${card.title}</p>
          <strong class="detail-total">${card.count}</strong>
          <div class="section-note">${card.note}</div>
        </div>`
    )
    .join("");
}

function summarizeRow(category, item) {
  if (category === "missing_normalized") {
    return `<code>${item.artifact_path}</code><br><span class="muted">expected: ${item.expected_normalized_path}</span>`;
  }
  if (category === "stale_normalized") {
    return `<code>${item.artifact_path}</code><br><span class="muted">normalized: ${item.normalized_path}</span>`;
  }
  if (category === "stale_build" || category === "missing_indexed") {
    return `<code>${item.normalized_path}</code><br><span class="muted">doc_id: ${item.doc_id}</span>`;
  }
  if (category === "orphaned_normalized") {
    return `<code>${item.normalized_path}</code><br><span class="muted">missing artifact: ${item.missing_artifact_path}</span>`;
  }
  return `<code>${JSON.stringify(item)}</code>`;
}

function renderDetailTables(detail) {
  if (!detail) {
    els.detailTables.innerHTML = `<div class="empty-state">Dang o che do summary-only. Luu OWNER_API_KEY hop le de tai file path chi tiet.</div>`;
    return;
  }
  const sections = [
    ["missing_normalized", "Missing normalized"],
    ["stale_normalized", "Stale normalized"],
    ["stale_build", "Stale build"],
    ["missing_indexed", "Missing indexed"],
    ["orphaned_normalized", "Orphaned normalized"],
  ];
  els.detailTables.innerHTML = sections
    .map(([key, title]) => {
      const items = detail.stale_files?.[key] || [];
      const tone = chipForCount(items.length);
      const body = items.length
        ? items
            .map(
              (item, index) => `
                <tr>
                  <td>${index + 1}</td>
                  <td>${summarizeRow(key, item)}</td>
                </tr>`
            )
            .join("")
        : `<tr><td colspan="2"><div class="empty-state">Khong co muc nao trong nhom nay.</div></td></tr>`;
      return `
        <section class="table-card">
          <div class="section-head">
            <div>
              <h2>${title}</h2>
              <p class="section-note">Chi tiet de xu ly ngay khi pipeline bat dau lech.</p>
            </div>
            <span class="chip ${tone}">${items.length} items</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>#</th><th>Item</th></tr>
              </thead>
              <tbody>${body}</tbody>
            </table>
          </div>
        </section>`;
    })
    .join("");
}

function applySummary(summaryData) {
  els.heroStatus.textContent = summaryData.status || "unknown";
  els.heroGeneratedAt.textContent = formatDate(summaryData.generated_at);
  renderSummary(summaryData.summary || {});
}

function applyDetail(detailData, hasDetail) {
  const coverage = detailData?.metadata_coverage || {};
  const history = detailData?.build_history || [];
  renderCoverage(coverage);
  renderBuildHistory(history);
  renderDetailCards(detailData?.summary || {}, hasDetail ? detailData : null);
  renderDetailTables(hasDetail ? detailData : null);
  els.detailModeChip.textContent = hasDetail ? "owner detail" : "summary only";
  els.detailModeChip.className = `chip ${hasDetail ? "ok" : "warn"}`;
}

async function loadHealth() {
  setStatus("Dang tai health summary...", "");
  let summaryData;
  try {
    summaryData = await jsonFetch("/health");
  } catch (error) {
    setStatus(`Khong tai duoc /health: ${error.message}`, "error");
    return;
  }

  applySummary(summaryData);

  if (!getOwnerKey()) {
    applyDetail(summaryData, false);
    setStatus("Da tai summary. Neu can file path chi tiet, them OWNER_API_KEY.", "ok");
    return;
  }

  try {
    const detailData = await jsonFetch("/health/details", true);
    applyDetail(detailData, true);
    setStatus("Da tai health detail bang OWNER_API_KEY.", "ok");
  } catch (error) {
    applyDetail(summaryData, false);
    setStatus(`Summary da tai, nhung detail khong mo duoc: ${error.message}`, "error");
  }
}

els.saveKey.addEventListener("click", () => {
  localStorage.setItem("graphrag_owner_key", els.ownerKey.value.trim());
  setStatus("Da luu key trong localStorage. Dang tai lai detail...", "ok");
  loadHealth();
});

els.refreshBtn.addEventListener("click", loadHealth);

els.ownerKey.value = getOwnerKey();
loadHealth();
