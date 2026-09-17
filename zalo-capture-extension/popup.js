const DEFAULT_API_BASE = "http://127.0.0.1:8000";

const els = {
  text: document.getElementById("text"),
  readThread: document.getElementById("readThread"),
  tenant: document.getElementById("tenant"),
  folder: document.getElementById("folder"),
  save: document.getElementById("save"),
  status: document.getElementById("status"),
  apiBase: document.getElementById("apiBase"),
  ownerKey: document.getElementById("ownerKey"),
  saveConfig: document.getElementById("saveConfig"),
  images: document.getElementById("images"),
  threadStatus: document.getElementById("threadStatus"),
  coverageStatus: document.getElementById("coverageStatus"),
};

function setStatus(msg, kind) {
  els.status.textContent = msg;
  els.status.className = kind || "";
}

async function getConfig() {
  const stored = await chrome.storage.local.get(["apiBase", "ownerKey"]);
  return {
    apiBase: stored.apiBase || DEFAULT_API_BASE,
    ownerKey: stored.ownerKey || "",
  };
}

// Chạy trong context của trang Zalo Web (không dùng biến/hàm ngoài — executeScript inject
// nguyên hàm này sang trang, không mang theo closure). Zalo chỉ hiện tên người gửi ở tin đầu
// của 1 cụm liên tiếp (class "show-sender") — các tin sau trong cụm không có
// .message-sender-name-content nên phải "nhớ" người gửi gần nhất khi duyệt xuống.
//
// mode="selection" (mặc định, nút "Lưu đoạn đã bôi đen"): chỉ lấy .chat-item nằm trong vùng
// bôi đen — cách duy nhất trước đây.
// mode="thread" (nút "Đọc các tin đang hiển thị", thêm 2026-08-05): lấy TOÀN BỘ .chat-item hiện
// có trong DOM, không cần bôi đen — nhưng vẫn chỉ chạy đúng lúc bạn tự bấm nút trong popup, y
// hệt nguyên tắc "an toàn hơn content script thường trực" đã ghi trước đây; KHÔNG tự chạy nền,
// KHÔNG tự cuộn để tải thêm lịch sử — chỉ đọc những gì Zalo Web đã render sẵn tại thời điểm bấm
// (Zalo ảo hoá DOM chat, nên đây là "các tin đang hiển thị/đã tải", không phải toàn bộ lịch sử).
function extractZaloContentInPage(mode) {
  // Hàm lồng bên trong (không phải top-level của popup.js) — executeScript chỉ inject đúng
  // 1 function truyền qua `func`, không mang theo scope ngoài, nên helper phải nằm lồng ở đây
  // mới gọi được khi chạy trong context trang Zalo.
  function formatTimestamp(ms) {
    const d = new Date(ms);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  // Phần số sau "@" trong data-qid (vd "...@1785816379709_0_g...") chính là epoch millisecond
  // lúc gửi tin — đã đối chiếu khớp chính xác với giờ hiển thị trên UI Zalo (11:06:19 vs
  // "11:06" hiển thị, sai số chỉ ở phần giây không hiện trên UI). Đáng tin hơn hẳn so với cố
  // lấy 1 date-divider trong DOM (chưa có mẫu để đối chiếu).
  function messageTimestamp(item) {
    const qidEl = item.querySelector("[data-qid]");
    const qid = qidEl ? qidEl.getAttribute("data-qid") || "" : "";
    const match = qid.match(/@(\d+)_/);
    if (!match) return "";
    const ms = Number(match[1]);
    return ms ? formatTimestamp(ms) : "";
  }

  const EMPTY_RESULT = { text: "", images: [], groupId: "", groupName: "" };

  let items;
  if (mode === "thread") {
    items = Array.from(document.querySelectorAll(".chat-item"));
    if (items.length === 0) return EMPTY_RESULT;
  } else {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return EMPTY_RESULT;
    const range = sel.getRangeAt(0);

    items = Array.from(document.querySelectorAll(".chat-item")).filter((el) =>
      range.intersectsNode(el)
    );
    if (items.length === 0) {
      // Không nhận ra cấu trúc .chat-item (trang khác, hoặc bôi đen ngoài vùng chat) — vẫn trả
      // text thô thay vì bỏ trắng, người dùng tự sửa tay trong popup.
      return { ...EMPTY_RESULT, text: sel.toString() };
    }
  }

  // Ảnh trong tin nhắn — CHƯA có mẫu HTML thật của 1 tin nhắn ẢNH để đối chiếu (chỉ có mẫu
  // text + avatar), nên đây là suy đoán hợp lý: lấy mọi <img> trong .chat-item đã chọn, trừ
  // ảnh đại diện (.zavatar-container). Cần chỉnh lại nếu sai sau khi test trên ảnh thật.
  const images = [];
  for (const item of items) {
    for (const img of item.querySelectorAll("img")) {
      if (img.closest(".zavatar-container")) continue;
      if (img.src && !images.includes(img.src)) images.push(img.src);
    }
  }

  // Tên nhóm nằm ở thanh tiêu đề, chỉ 1 lần trên toàn trang (không nằm trong .chat-item).
  const titleEl = document.querySelector(".threadChat__title .header-title");
  const groupName = titleEl ? titleEl.textContent.trim() : "";

  // data-qid mỗi tin có dạng "<senderId>@<msgId>_<x>_g<groupId>" — đoạn cuối bắt đầu bằng "g"
  // nhiều khả năng là group ID (chưa xác nhận được 100% vì không có mẫu tên nhóm để đối
  // chiếu — nếu sai/không khớp thực tế, dòng "Nhóm ID:" sẽ trống hoặc sai, không ảnh hưởng
  // phần nội dung tin nhắn).
  let groupId = "";
  for (const item of items) {
    const qidEl = item.querySelector("[data-qid]");
    const qid = qidEl ? qidEl.getAttribute("data-qid") || "" : "";
    const match = qid.match(/g(\d+)$/);
    if (match) {
      groupId = match[1];
      break;
    }
  }

  let lastSender = "";
  let sawGroupStyleSender = false;
  const lines = [];
  for (const item of items) {
    // Tin của chính mình (class "me" trên .chat-item) KHÔNG có .message-sender-name-content
    // — nếu không check riêng, code sẽ nhầm gán tên người gửi trước đó (tin nhận gần nhất)
    // cho tin của mình. "Bạn" không ảnh hưởng lastSender — lastSender chỉ áp dụng cho chuỗi
    // tin NHẬN liên tiếp, tin "me" xen giữa không làm mất context người đang nhắn.
    const isMe = item.classList.contains("me");
    let sender;
    if (isMe) {
      sender = "Bạn";
    } else {
      const nameEl = item.querySelector(".message-sender-name-content .truncate");
      if (nameEl) {
        lastSender = nameEl.textContent.trim();
        sawGroupStyleSender = true;
      } else if (!lastSender) {
        // Chat 1-1: Zalo không hiện .message-sender-name-content trên từng tin (chỉ 2 người,
        // không cần phân biệt) — dùng tên tiêu đề hội thoại (groupName, cùng selector
        // .threadChat__title .header-title) làm fallback, vì trong chat 1-1 đó chính là tên
        // người kia, không phải tên nhóm.
        lastSender = groupName || "?";
      }
      sender = lastSender || "?";
    }

    let text = "";
    const textEl = item.querySelector('[data-component="message-text-content"]');
    if (textEl) {
      text = textEl.innerText.trim();
      const linkTitle = item.querySelector(".link-message__link-title");
      if (linkTitle) {
        const linkSource = item.querySelector(".link-message__link-source");
        text += `\n[${linkTitle.textContent.trim()}${linkSource ? " - " + linkSource.textContent.trim() : ""}]`;
      }
      // Cấu trúc quote-fragment không nhất quán giữa các mẫu: có lúc tên nằm trong span con
      // .quote-name, có lúc là text trực tiếp của .message-quote-fragment__title — thử nested
      // trước, fallback textContent của chính element bao ngoài.
      const quoteTitleEl = item.querySelector(".message-quote-fragment__title");
      const quoteNameText = quoteTitleEl
        ? (quoteTitleEl.querySelector(".quote-name") || quoteTitleEl).textContent.trim()
        : "";
      const quoteDesc = item.querySelector(".message-quote-fragment__description");
      if (quoteDesc) {
        text = `(trả lời ${quoteNameText || "..."}: "${quoteDesc.textContent.trim()}")\n${text}`;
      }
    } else {
      const actionEl = item.querySelector(".message-action");
      text = (actionEl || item).innerText.trim();
    }
    if (text) {
      const ts = messageTimestamp(item);
      lines.push(ts ? `[${ts}] ${sender}: ${text}` : `${sender}: ${text}`);
    }
  }

  const headerParts = [];
  // sawGroupStyleSender=false suốt cả đoạn chọn nghĩa là không tin nào có tên gắn riêng —
  // dấu hiệu đây là chat 1-1 (groupName lúc này là tên người kia, không phải tên nhóm).
  if (groupName) headerParts.push(`${sawGroupStyleSender ? "Nhóm" : "Người"}: ${groupName}`);
  if (groupId) headerParts.push(`ID: ${groupId}`);
  const header = headerParts.length ? `[${headerParts.join(" | ")}]\n\n` : "";
  return { text: header + lines.join("\n\n"), images, groupId, groupName };
}

const EMPTY_READ_RESULT = { text: "", images: [], groupId: "", groupName: "" };

async function readSelectionFromActiveTab(mode) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) return EMPTY_READ_RESULT;
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractZaloContentInPage,
      args: [mode],
    });
    return result || EMPTY_READ_RESULT;
  } catch {
    // Trang không cho phép inject script (vd chrome:// hoặc chưa cấp quyền) — bỏ qua lặng lẽ,
    // người dùng vẫn có thể tự gõ/paste vào textarea.
    return EMPTY_READ_RESULT;
  }
}

// Chạy trong context trang Zalo, giống extractZaloContentInPage() — cũng phải tự chứa hoàn
// toàn (không tham chiếu biến/hàm ngoài) vì executeScript inject nguyên hàm này, không mang
// theo scope. Khác extractZaloContentInPage: KHÔNG cần vùng bôi đen, chỉ đọc đoạn chat đang
// mở (an toàn hơn content script thường trực — chỉ chạy đúng lúc bạn tự mở popup, xem
// RULES.md/CLAUDE.md phần cân nhắc điều khoản Zalo trước khi thêm lại cơ chế tự động).
function readThreadIdentityInPage() {
  const titleEl = document.querySelector(".threadChat__title .header-title");
  const groupName = titleEl ? titleEl.textContent.trim() : "";
  if (!groupName) return null;

  let groupId = "";
  let sawGroupStyleSender = false;
  for (const item of document.querySelectorAll(".chat-item")) {
    if (item.querySelector(".message-sender-name-content")) sawGroupStyleSender = true;
    if (!groupId) {
      const qidEl = item.querySelector("[data-qid]");
      const qid = qidEl ? qidEl.getAttribute("data-qid") || "" : "";
      const match = qid.match(/g(\d+)$/);
      if (match) groupId = match[1];
    }
  }
  return { groupName, groupId, isGroup: sawGroupStyleSender || !!groupId };
}

async function readThreadIdentityFromActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) return null;
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: readThreadIdentityInPage,
    });
    return result || null;
  } catch {
    return null;
  }
}

// Tenant (org/workspace) đang chọn trong dropdown, dạng "msb/magnet" — kể từ khi tách
// tenants/ (2026-08-22), `graphrag serve` chỉ bind đúng 1 tenant qua --tenant-root, và server
// giờ chấp nhận tham số `tenant` trên /folders, /capture, /capture-status, /capture-image để
// vượt qua tenant mặc định đó (xem api.py _resolve_tenant_settings). Bỏ trống = server tự dùng
// tenant nó đang bind (tương thích ngược).
function selectedTenant() {
  return els.tenant.value || "";
}

// Cây org -> workspace (kèm instance lồng nhau) từ GET /tenants (không cần OWNER_API_KEY) rút
// gọn về danh sách phẳng các tenant_id thật sự có dữ liệu (status "active") — node "parent"
// (chỉ chứa instances/, chính nó không phải 1 tenant hợp lệ để truyền vào --tenant-root) bị bỏ
// qua, chỉ đệ quy xuống children của nó.
function flattenTenants(data) {
  const result = [];
  function walk(node) {
    if (!node) return;
    if (node.status === "active") result.push({ tenant_id: node.tenant_id, doc_count: node.doc_count || 0 });
    for (const child of node.children || []) walk(child);
  }
  for (const org of data.orgs || []) {
    for (const ws of org.workspaces || []) walk(ws);
  }
  walk(data.common);
  return result;
}

async function loadTenants(config) {
  els.tenant.innerHTML = "<option>Đang tải…</option>";
  try {
    const res = await fetch(`${config.apiBase}/tenants`);
    if (!res.ok) {
      els.tenant.innerHTML = "<option value=''>— lỗi server —</option>";
      return;
    }
    const flat = flattenTenants(await res.json());
    if (flat.length === 0) {
      els.tenant.innerHTML = "<option value=''>— không có tenant nào —</option>";
      return;
    }
    const stored = await chrome.storage.local.get(["tenant"]);
    const hasStored = stored.tenant && flat.some((t) => t.tenant_id === stored.tenant);
    els.tenant.innerHTML = flat
      .map((t) => `<option value="${t.tenant_id}">${t.tenant_id} (${t.doc_count})</option>`)
      .join("");
    els.tenant.value = hasStored ? stored.tenant : flat[0].tenant_id;
  } catch {
    els.tenant.innerHTML = "<option value=''>— không kết nối được server —</option>";
  }
}

async function checkThreadStatus(config) {
  els.threadStatus.className = "";
  const identity = await readThreadIdentityFromActiveTab();
  if (!identity) return; // Không phải trang Zalo/không nhận diện được đoạn chat — im lặng.
  if (!config.ownerKey) {
    els.threadStatus.textContent = "Chưa cấu hình OWNER_API_KEY — không biết đoạn này đã capture chưa.";
    els.threadStatus.className = "unknown";
    return;
  }
  try {
    const params = new URLSearchParams();
    if (identity.groupId) params.set("group_id", identity.groupId);
    if (identity.groupName) params.set("group_name", identity.groupName);
    if (selectedTenant()) params.set("tenant", selectedTenant());
    const res = await fetch(`${config.apiBase}/capture-status?${params}`, {
      headers: { "X-API-Key": config.ownerKey },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      els.threadStatus.textContent = `Không tra được trạng thái: ${res.status} ${body.detail || ""}`;
      els.threadStatus.className = "unknown";
      return;
    }
    const data = await res.json();
    if (data.captured) {
      els.threadStatus.textContent = `✓ Đoạn này đã capture (${data.note_count} lần, gần nhất ${data.last_captured_at}).`;
      els.threadStatus.className = "captured";
    } else {
      els.threadStatus.textContent = "● Đoạn này chưa capture lần nào.";
      els.threadStatus.className = "not_captured";
    }
  } catch {
    els.threadStatus.textContent = "Không kết nối được server — kiểm tra graphrag serve đã chạy chưa.";
    els.threadStatus.className = "unknown";
  }
}

// "" là 1 lựa chọn HỢP LỆ trong dropdown folder (lưu vào gốc tenant, xem list_capture_folders) —
// không thể dùng `!els.folder.value` để biết dropdown đã tải xong thật hay chưa (2 trường hợp
// đều ra chuỗi rỗng). Cờ riêng này mới là nguồn xác định đáng tin cho nút Lưu.
let foldersLoaded = false;

async function loadFolders(config) {
  foldersLoaded = false;
  els.folder.innerHTML = "<option>Đang tải…</option>";
  if (!config.ownerKey) {
    els.folder.innerHTML = "<option value=''>— cần nhập OWNER_API_KEY ở Cấu hình server —</option>";
    return;
  }
  try {
    const params = new URLSearchParams();
    if (selectedTenant()) params.set("tenant", selectedTenant());
    const res = await fetch(`${config.apiBase}/folders?${params}`, {
      headers: { "X-API-Key": config.ownerKey },
    });
    if (res.status === 401) {
      els.folder.innerHTML = "<option value=''>— sai OWNER_API_KEY —</option>";
      return;
    }
    if (!res.ok) {
      els.folder.innerHTML = "<option value=''>— lỗi server —</option>";
      return;
    }
    const folders = await res.json();
    // "" = lưu thẳng vào gốc tenant, không gắn ticket cụ thể (xem capture.py list_capture_folders)
    // — gắn nhãn rõ ràng thay vì để option trống vô hình, dễ nhầm là dropdown chưa tải xong.
    els.folder.innerHTML = folders
      .map((f) => `<option value="${f}">${f || "— Chưa phân loại / không thuộc backlog nào —"}</option>`)
      .join("");
    foldersLoaded = true;
  } catch {
    els.folder.innerHTML = "<option value=''>— không kết nối được server, kiểm tra graphrag serve đã chạy chưa —</option>";
  }
}

async function urlToBase64(url) {
  const res = await fetch(url);
  const blob = await res.blob();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(reader.result.split(",")[1]); // bỏ "data:...;base64,"
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function renderImages(urls, config) {
  els.images.innerHTML = "";
  urls.forEach((url, i) => {
    const div = document.createElement("div");
    div.className = "image-item";
    div.innerHTML = `
      <img src="${url}" alt="">
      <button data-ocr="${i}">OCR vào tri thức</button>
      <span class="img-status" id="img-status-${i}"></span>`;
    els.images.appendChild(div);
  });
  els.images.querySelectorAll("[data-ocr]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const i = Number(btn.dataset.ocr);
      const statusEl = document.getElementById(`img-status-${i}`);
      const folder = els.folder.value;
      if (!foldersLoaded) {
        statusEl.textContent = "Chưa chọn dự án (danh sách dự án chưa tải xong).";
        return;
      }
      btn.disabled = true;
      statusEl.textContent = "Đang OCR…";
      try {
        const image_base64 = await urlToBase64(urls[i]);
        const res = await fetch(`${config.apiBase}/capture-image`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-API-Key": config.ownerKey },
          body: JSON.stringify({ folder, image_base64, tenant: selectedTenant() }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          statusEl.textContent = `Lỗi: ${data.detail || res.status}`;
          return;
        }
        statusEl.textContent = `Đã lưu vào ${data.saved_to}.`;
      } catch {
        statusEl.textContent = "Không lấy/gửi được ảnh — kiểm tra kết nối.";
      } finally {
        btn.disabled = false;
      }
    });
  });
}

async function init() {
  const config = await getConfig();
  els.apiBase.value = config.apiBase;
  els.ownerKey.value = config.ownerKey;

  const { text, images } = await readSelectionFromActiveTab("selection");
  els.text.value = text;
  renderImages(images, config);
  await loadTenants(config); // phải xong trước loadFolders — folders phụ thuộc tenant đang chọn
  await loadFolders(config);
  checkThreadStatus(config); // không await — không chặn phần load folder/text ở trên
}

els.tenant.addEventListener("change", async () => {
  await chrome.storage.local.set({ tenant: els.tenant.value });
  const config = await getConfig();
  await loadFolders(config);
  checkThreadStatus(config);
});

function setCoverageStatus(msg, kind) {
  els.coverageStatus.textContent = msg;
  els.coverageStatus.className = kind || "";
}

// Tra /capture-status — không có tác dụng phụ lên UI (khác checkThreadStatus, dùng cho banner
// riêng #threadStatus) — hỏi lại thẳng server mỗi lần đọc để biết CHẮC CHẮN đoạn chat này đã
// từng capture chưa (không dựa banner đầu trang có thể đã cũ nếu vừa đổi tenant/đổi đoạn chat).
async function fetchCaptureStatusQuiet(config, groupId, groupName) {
  if ((!groupId && !groupName) || !config.ownerKey) return null;
  try {
    const params = new URLSearchParams();
    if (groupId) params.set("group_id", groupId);
    if (groupName) params.set("group_name", groupName);
    if (selectedTenant()) params.set("tenant", selectedTenant());
    const res = await fetch(`${config.apiBase}/capture-status?${params}`, {
      headers: { "X-API-Key": config.ownerKey },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// Xem trước (server KHÔNG ghi gì) tin nào trong `text` đã từng capture — so khớp đúng TỪNG TIN
// NHẮN THẬT theo hash nội dung (dùng chung logic lọc trùng với /capture thật, xem
// capture.preview_capture) — đáng tin hơn hẳn tự đoán qua khoảng thời gian ở bản trước, và
// không cần cache phía client: server luôn là nguồn sự thật, tính lại mỗi lần hỏi.
async function fetchCapturePreview(config, text) {
  if (!config.ownerKey) return null;
  try {
    const res = await fetch(`${config.apiBase}/capture-preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": config.ownerKey },
      body: JSON.stringify({ text, tenant: selectedTenant() }),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

els.readThread.addEventListener("click", async () => {
  els.readThread.disabled = true;
  setStatus("Đang đọc các tin đang hiển thị…");
  setCoverageStatus("", ""); // xoá cảnh báo độ phủ của lượt đọc trước, tránh hiện nhầm nếu lượt này lỗi/đổi đoạn chat
  try {
    const { text, images, groupId, groupName } = await readSelectionFromActiveTab("thread");
    if (!text) {
      setStatus("Không đọc được tin nào — mở đúng 1 đoạn chat Zalo Web rồi thử lại.", "error");
      return;
    }
    els.text.value = text;
    const config = await getConfig();
    renderImages(images, config);

    // Hiện ngay đầu popup (cạnh banner "đã capture") thay vì cuối trang — dễ bị che khuất bởi
    // phần ảnh/OCR hoặc cần cuộn mới thấy nếu chỉ hiện ở dòng trạng thái cuối cạnh nút Lưu.
    // Chỉ cảnh báo khi đoạn chat này ĐÃ từng capture trước đó (nếu chưa từng, không có gì để so
    // — mọi nội dung đọc được đều là mới, im lặng). Tin CŨ NHẤT trong lượt đọc này (paragraph
    // đầu tiên sau header — .chat-item duyệt theo đúng thứ tự DOM cũ->mới) là tín hiệu chính:
    // nếu nó đã từng được lưu, coi như đã đọc lùi chạm tới đúng nơi lần capture trước dừng lại.
    const status = await fetchCaptureStatusQuiet(config, groupId, groupName);
    if (status && status.captured) {
      const preview = await fetchCapturePreview(config, text);
      if (preview && preview.total > 0) {
        setCoverageStatus(
          preview.oldest_already_seen
            ? "✓ Tin CŨ NHẤT đang đọc đã từng được lưu trước đó — đã chạm vùng đã lưu, an tâm lưu tiếp."
            : "⚠ Tin CŨ NHẤT đang đọc CHƯA từng được lưu — có thể còn khoảng tin nhắn cũ hơn chưa lưu ở giữa. Cuộn lên xem thêm tin cũ hơn rồi đọc lại.",
          preview.oldest_already_seen ? "ok" : "warn"
        );
      }
    }
    // status null hoặc captured=false -> chưa từng capture đoạn này, không có gì để cảnh báo, im lặng.

    setStatus("Đã điền các tin đang hiển thị — xem/sửa/xoá tên người khác trước khi lưu.", "ok");
  } finally {
    els.readThread.disabled = false;
  }
});

els.saveConfig.addEventListener("click", async () => {
  const apiBase = els.apiBase.value.trim() || DEFAULT_API_BASE;
  const ownerKey = els.ownerKey.value.trim();
  await chrome.storage.local.set({ apiBase, ownerKey });
  setStatus("Đã lưu cấu hình.", "ok");
  const config = { apiBase, ownerKey };
  await loadTenants(config); // apiBase có thể vừa đổi sang server khác — tenant list cần load lại trước
  await loadFolders(config);
});

els.save.addEventListener("click", async () => {
  const config = await getConfig();
  const text = els.text.value.trim();
  const folder = els.folder.value;

  if (!text) {
    setStatus("Chưa có nội dung — bôi đen trên trang rồi mở lại popup, hoặc tự gõ vào ô trên.", "error");
    return;
  }
  if (!foldersLoaded) {
    setStatus("Chưa chọn dự án để lưu vào (danh sách dự án chưa tải xong).", "error");
    return;
  }

  els.save.disabled = true;
  setStatus("Đang lưu…");
  try {
    const res = await fetch(`${config.apiBase}/capture`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": config.ownerKey },
      body: JSON.stringify({ folder, text, tenant: selectedTenant() }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setStatus(`Lỗi: ${data.detail || res.status}`, "error");
      return;
    }
    const dupNote = data.skipped_duplicate ? ` (bỏ ${data.skipped_duplicate} tin trùng lần capture trước)` : "";
    setStatus(`Đã lưu vào ${data.saved_to} — index có ${data.n_chunks_indexed} chunk mới.${dupNote}`, "ok");
    els.text.value = "";
  } catch {
    setStatus("Không kết nối được server — kiểm tra graphrag serve đã chạy chưa.", "error");
  } finally {
    els.save.disabled = false;
  }
});

init();
