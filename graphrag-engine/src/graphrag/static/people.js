const els = {
  ownerKey: document.getElementById("ownerKey"),
  saveKey: document.getElementById("saveKey"),
  status: document.getElementById("status"),
  registryBody: document.getElementById("registryBody"),
  newCanonical: document.getElementById("newCanonical"),
  newAliases: document.getElementById("newAliases"),
  addPerson: document.getElementById("addPerson"),
  channelColumns: document.getElementById("channelColumns"),
  linksSvg: document.getElementById("linksSvg"),
  linkGroups: document.getElementById("linkGroups"),
  mergeCanonical: document.getElementById("mergeCanonical"),
  mergeSelected: document.getElementById("mergeSelected"),
  ignoreSelected: document.getElementById("ignoreSelected"),
  ignoredList: document.getElementById("ignoredList"),
};
const isEmbedded = new URLSearchParams(window.location.search).get("embed") === "1";

function setStatus(msg, kind) {
  els.status.textContent = msg;
  els.status.className = kind || "";
}

function getOwnerKey() {
  return localStorage.getItem("graphrag_owner_key") || "";
}

function notifyParentHeight() {
  if (!isEmbedded || window.parent === window) return;
  const root = document.documentElement;
  const body = document.body;
  const height = Math.max(
    body ? body.scrollHeight : 0,
    body ? body.offsetHeight : 0,
    root ? root.scrollHeight : 0,
    root ? root.offsetHeight : 0
  );
  window.parent.postMessage({ type: "people-embed-height", height }, "*");
}

async function apiFetch(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { ...(options.headers || {}), "X-API-Key": getOwnerKey() },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return res.status === 204 ? null : res.json();
}

function renderRegistry(registry) {
  els.registryBody.innerHTML = registry.length
    ? registry
        .map(
          (p) => `
      <tr>
        <td>${p.canonical}</td>
        <td>${(p.aliases || []).join(", ")}</td>
        <td><div style="display:flex; justify-content:flex-end; padding-right:6px;"><button class="danger" data-del="${encodeURIComponent(p.canonical)}">Xoá</button></div></td>
      </tr>`
        )
        .join("")
    : `<tr><td colspan="3"><div class="empty-card">Chưa có bản ghi nào trong registry.</div></td></tr>`;
  els.registryBody.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await apiFetch(`/people/${btn.dataset.del}`, { method: "DELETE" });
        setStatus("Đã xoá.", "ok");
        await refresh();
      } catch (e) {
        setStatus(`Lỗi: ${e.message}`, "error");
      }
    });
  });
  notifyParentHeight();
}

function renderChannelColumns(byChannel) {
  const channels = Object.keys(byChannel);
  els.channelColumns.innerHTML = channels
    .map((channel) => {
      const items = byChannel[channel];
      const rendered = items.length
        ? items
            .map(({ name: n, groups }) => {
              const groupsHint = groups && groups.length
                ? `<div class="hint">Xuất hiện ở: ${groups.join(", ")}</div>`
                : "";
              return `
              <label>
                <input type="checkbox" data-channel="${channel}" value="${encodeURIComponent(n)}">
                <span class="drag-handle" data-channel="${channel}" data-name="${encodeURIComponent(n)}" title="Kéo sang 1 tên khác để nối cùng 1 người"></span>
                ${n}
                ${groupsHint}
              </label>`;
            })
            .join("")
        : "<div class='empty-card'>Không còn tên nào chưa map trong kênh này.</div>";
      return `<div class="channel-col"><h3>${channel}</h3>${rendered}</div>`;
    })
    .join("");

  els.channelColumns.querySelectorAll(".drag-handle").forEach((handle) => {
    handle.addEventListener("mousedown", onDragStart);
  });

  // Tên chưa map đổi (vd sau khi lưu/ẩn 1 nhóm) — link trỏ tới tên không còn tồn tại nữa thì
  // tự rớt khi vẽ lại (renderLinksAndGroups bỏ qua link thiếu handle), không cần dọn tay.
  renderLinksAndGroups();
  notifyParentHeight();
}

// --- Kéo-thả nối 2 tên cùng 1 người (bổ sung cách tick checkbox ở trên, tiện khi chỉ nối
// từng cặp 1-1 xuyên cột mà không cần gõ lại tên chính ngay — xem cả nhóm rồi mới đặt tên). ---

let links = []; // [{a: {channel, name}, b: {channel, name}}]
let dragState = null; // {channel, name, el} trong lúc đang kéo

function nodeKey(node) {
  return `${node.channel}:::${node.name}`;
}

function svgPoint(clientX, clientY) {
  const rect = els.linksSvg.getBoundingClientRect();
  return { x: clientX - rect.left, y: clientY - rect.top };
}

function handleCenter(el) {
  const r = el.getBoundingClientRect();
  return svgPoint(r.left + r.width / 2, r.top + r.height / 2);
}

function findHandle(channel, name) {
  return els.channelColumns.querySelector(
    `.drag-handle[data-channel="${channel}"][data-name="${encodeURIComponent(name)}"]`
  );
}

function onDragStart(ev) {
  ev.preventDefault();
  const handle = ev.currentTarget;
  dragState = {
    channel: handle.dataset.channel,
    name: decodeURIComponent(handle.dataset.name),
    el: handle,
  };
  document.addEventListener("mousemove", onDragMove);
  document.addEventListener("mouseup", onDragEnd);
  drawTempLine(ev.clientX, ev.clientY);
}

function drawTempLine(clientX, clientY) {
  if (!dragState) return;
  const start = handleCenter(dragState.el);
  const end = svgPoint(clientX, clientY);
  let temp = els.linksSvg.querySelector("line.temp");
  if (!temp) {
    temp = document.createElementNS("http://www.w3.org/2000/svg", "line");
    temp.setAttribute("class", "temp");
    els.linksSvg.appendChild(temp);
  }
  temp.setAttribute("x1", start.x);
  temp.setAttribute("y1", start.y);
  temp.setAttribute("x2", end.x);
  temp.setAttribute("y2", end.y);
}

function onDragMove(ev) {
  drawTempLine(ev.clientX, ev.clientY);
}

function onDragEnd(ev) {
  document.removeEventListener("mousemove", onDragMove);
  document.removeEventListener("mouseup", onDragEnd);
  const temp = els.linksSvg.querySelector("line.temp");
  if (temp) temp.remove();
  if (!dragState) return;

  const target = document
    .elementsFromPoint(ev.clientX, ev.clientY)
    .find((el) => el.classList && el.classList.contains("drag-handle"));

  if (target && target !== dragState.el) {
    const a = { channel: dragState.channel, name: dragState.name };
    const b = { channel: target.dataset.channel, name: decodeURIComponent(target.dataset.name) };
    const exists = links.some(
      (l) =>
        (nodeKey(l.a) === nodeKey(a) && nodeKey(l.b) === nodeKey(b)) ||
        (nodeKey(l.a) === nodeKey(b) && nodeKey(l.b) === nodeKey(a))
    );
    if (!exists) {
      links.push({ a, b });
    }
    renderLinksAndGroups();
  }
  dragState = null;
}

function renderLinksAndGroups() {
  els.linksSvg.querySelectorAll("line:not(.temp)").forEach((l) => l.remove());
  links.forEach((link) => {
    const elA = findHandle(link.a.channel, link.a.name);
    const elB = findHandle(link.b.channel, link.b.name);
    if (!elA || !elB) return; // tên đã bị lưu/ẩn ở nhóm khác — bỏ vẽ, không lỗi
    const p1 = handleCenter(elA);
    const p2 = handleCenter(elB);
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", p1.x);
    line.setAttribute("y1", p1.y);
    line.setAttribute("x2", p2.x);
    line.setAttribute("y2", p2.y);
    els.linksSvg.appendChild(line);
  });
  renderLinkGroups();
}

function renderLinkGroups() {
  // Union-find gộp các cặp đã nối thành từng nhóm (vd A-B và B-C nối chung thành 1 nhóm
  // {A, B, C}) — 1 người có thể có nhiều hơn 2 tên/định danh xuyên kênh.
  const parent = {};
  const find = (x) => {
    if (!(x in parent)) parent[x] = x;
    while (parent[x] !== x) x = parent[x];
    return x;
  };
  const union = (x, y) => {
    const rx = find(x);
    const ry = find(y);
    if (rx !== ry) parent[rx] = ry;
  };
  const nodeByKey = {};
  links.forEach((l) => {
    nodeByKey[nodeKey(l.a)] = l.a;
    nodeByKey[nodeKey(l.b)] = l.b;
    union(nodeKey(l.a), nodeKey(l.b));
  });
  const groupsByRoot = {};
  Object.keys(nodeByKey).forEach((k) => {
    const root = find(k);
    (groupsByRoot[root] = groupsByRoot[root] || []).push(nodeByKey[k]);
  });
  const groupList = Object.values(groupsByRoot);

  els.linkGroups.innerHTML = groupList.length
    ? ""
    : "<div class='empty-card'>Chưa có nhóm nào. Kéo từ một chấm sang chấm khác để nối cùng một người.</div>";
  groupList.forEach((nodes, i) => {
    const div = document.createElement("div");
    div.className = "group-card";
    div.innerHTML = `
      <div class="names">${nodes
        .map((n) => `${n.name} <span class="hint">(${n.channel})</span>`)
        .join(" &nbsp;↔&nbsp; ")}</div>
      <div class="row">
        <input type="text" id="linkGroupCanonical-${i}" placeholder="Tên chính cho nhóm này" value="${nodes[0].name}">
        <button data-save-linkgroup="${i}">Lưu nhóm này</button>
        <button data-clear-linkgroup="${i}" class="secondary">Bỏ nối</button>
      </div>`;
    els.linkGroups.appendChild(div);
  });

  els.linkGroups.querySelectorAll("[data-save-linkgroup]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const i = Number(btn.dataset.saveLinkgroup);
      const nodes = groupList[i];
      const canonical = document.getElementById(`linkGroupCanonical-${i}`).value.trim();
      if (!canonical) {
        setStatus("Cần tên chính cho nhóm.", "error");
        return;
      }
      try {
        await apiFetch("/people", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ canonical, aliases: nodes.map((n) => n.name) }),
        });
        setStatus(`Đã lưu "${canonical}" (${nodes.length} tên/định danh).`, "ok");
        const keys = new Set(nodes.map(nodeKey));
        links = links.filter((l) => !keys.has(nodeKey(l.a)) || !keys.has(nodeKey(l.b)));
        await refresh();
      } catch (e) {
        setStatus(`Lỗi: ${e.message}`, "error");
      }
    });
  });

  els.linkGroups.querySelectorAll("[data-clear-linkgroup]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = Number(btn.dataset.clearLinkgroup);
      const nodes = groupList[i];
      const keys = new Set(nodes.map(nodeKey));
      links = links.filter((l) => !keys.has(nodeKey(l.a)) || !keys.has(nodeKey(l.b)));
      renderLinksAndGroups();
    });
  });
  notifyParentHeight();
}

window.addEventListener("resize", () => renderLinksAndGroups());

function renderIgnored(names) {
  els.ignoredList.innerHTML = names.length
    ? names
        .map(
          (n) =>
            `<li>${n} — <a href="#" data-unignore="${encodeURIComponent(n)}">bỏ ẩn</a></li>`
        )
        .join("")
    : "<li><div class='empty-card'>Không có mục nào đang bị ẩn.</div></li>";
  els.ignoredList.querySelectorAll("[data-unignore]").forEach((a) => {
    a.addEventListener("click", async (ev) => {
      ev.preventDefault();
      try {
        await apiFetch(`/people/ignore/${a.dataset.unignore}`, { method: "DELETE" });
        setStatus("Đã bỏ ẩn.", "ok");
        await refresh();
      } catch (e) {
        setStatus(`Lỗi: ${e.message}`, "error");
      }
    });
  });
  notifyParentHeight();
}

async function refresh() {
  try {
    const data = await apiFetch("/people");
    renderRegistry(data.registry);
    renderChannelColumns(data.unmapped_by_channel);
    renderIgnored(data.ignored);
  } catch (e) {
    setStatus(`Lỗi tải dữ liệu: ${e.message} — kiểm tra OWNER_API_KEY đã đúng chưa.`, "error");
  }
}

if (isEmbedded) {
  document.body.classList.add("embed");
  window.addEventListener("load", notifyParentHeight);
  window.addEventListener("resize", notifyParentHeight);
}

if (els.saveKey) {
  els.saveKey.addEventListener("click", () => {
    localStorage.setItem("graphrag_owner_key", els.ownerKey.value.trim());
    setStatus("Đã lưu key.", "ok");
    refresh();
  });
}

els.addPerson.addEventListener("click", async () => {
  const canonical = els.newCanonical.value.trim();
  const aliases = els.newAliases.value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (!canonical) {
    setStatus("Cần nhập tên chính.", "error");
    return;
  }
  try {
    await apiFetch("/people", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ canonical, aliases }),
    });
    setStatus(`Đã lưu "${canonical}".`, "ok");
    els.newCanonical.value = "";
    els.newAliases.value = "";
    await refresh();
  } catch (e) {
    setStatus(`Lỗi: ${e.message}`, "error");
  }
});

els.mergeSelected.addEventListener("click", async () => {
  const checked = Array.from(
    els.channelColumns.querySelectorAll("input[type=checkbox]:checked")
  ).map((cb) => decodeURIComponent(cb.value));
  const canonical = els.mergeCanonical.value.trim();
  if (!canonical || checked.length === 0) {
    setStatus("Cần tên chính và ít nhất 1 tên được chọn (ở 1 hay nhiều kênh).", "error");
    if (!canonical) {
      els.mergeCanonical.classList.add("input-error");
      els.mergeCanonical.focus();
    }
    return;
  }
  els.mergeCanonical.classList.remove("input-error");
  try {
    await apiFetch("/people", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ canonical, aliases: checked }),
    });
    setStatus(`Đã gộp "${canonical}" (${checked.length} tên/định danh).`, "ok");
    els.mergeCanonical.value = "";
    await refresh();
  } catch (e) {
    setStatus(`Lỗi: ${e.message}`, "error");
  }
});

els.ignoreSelected.addEventListener("click", async () => {
  const checked = Array.from(
    els.channelColumns.querySelectorAll("input[type=checkbox]:checked")
  ).map((cb) => decodeURIComponent(cb.value));
  if (checked.length === 0) {
    setStatus("Chưa chọn mục nào để đánh dấu.", "error");
    return;
  }
  try {
    await apiFetch("/people/ignore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names: checked }),
    });
    setStatus(`Đã ẩn ${checked.length} mục.`, "ok");
    await refresh();
  } catch (e) {
    setStatus(`Lỗi: ${e.message}`, "error");
  }
});

els.mergeCanonical.addEventListener("input", () => {
  els.mergeCanonical.classList.remove("input-error");
});

if (els.ownerKey) {
  els.ownerKey.value = getOwnerKey();
}
refresh();
setTimeout(notifyParentHeight, 0);
