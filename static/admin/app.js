const ADMIN_KEY_STORAGE = "epoch_admin_key";

const el = {
  adminKeyInput: document.getElementById("adminKeyInput"),
  saveAdminKeyBtn: document.getElementById("saveAdminKeyBtn"),
  refreshAllBtn: document.getElementById("refreshAllBtn"),
  globalStatus: document.getElementById("globalStatus"),

  overviewCards: document.getElementById("overviewCards"),

  genCount: document.getElementById("genCount"),
  genPrefix: document.getElementById("genPrefix"),
  genShots: document.getElementById("genShots"),
  generateBtn: document.getElementById("generateBtn"),
  generatedCodes: document.getElementById("generatedCodes"),

  tokenStatusFilter: document.getElementById("tokenStatusFilter"),
  reloadTokensBtn: document.getElementById("reloadTokensBtn"),
  tokensTableBody: document.getElementById("tokensTableBody"),

  userKeyword: document.getElementById("userKeyword"),
  reloadUsersBtn: document.getElementById("reloadUsersBtn"),
  usersTableBody: document.getElementById("usersTableBody"),
};

function getAdminKey() {
  return (el.adminKeyInput.value || "").trim();
}

function setStatus(message, type = "") {
  el.globalStatus.textContent = message || "";
  el.globalStatus.className = "status";
  if (type === "error") el.globalStatus.classList.add("error");
  if (type === "ok") el.globalStatus.classList.add("ok");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatDate(value) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-TW", { hour12: false });
}

function maskLineId(v) {
  if (!v) return "-";
  if (v.length <= 8) return v;
  return `${v.slice(0, 4)}...${v.slice(-4)}`;
}

async function getJson(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") {
      url.searchParams.set(k, String(v));
    }
  });

  const res = await fetch(url.toString());
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Request failed (${res.status})`);
  }
  return data;
}

async function postJson(path, payload = {}) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Request failed (${res.status})`);
  }
  return data;
}

function renderOverview(data) {
  const cards = [
    ["總 Token", data.total_tokens],
    ["已兌換 Token", data.redeemed_tokens],
    ["未兌換 Token", data.unredeemed_tokens],
    ["總使用者", data.total_users],
    ["仍有額度使用者", data.users_with_quota],
    ["全體剩餘拍攝次數", data.total_remaining_shots],
  ];

  el.overviewCards.innerHTML = cards
    .map(([k, v]) => `<div class="card"><div class="k">${escapeHtml(k)}</div><div class="v">${escapeHtml(v)}</div></div>`)
    .join("");
}

function renderTokens(items) {
  if (!items.length) {
    el.tokensTableBody.innerHTML = `<tr><td colspan="6" class="muted">查無資料</td></tr>`;
    return;
  }

  el.tokensTableBody.innerHTML = items
    .map((item) => {
      const redeemed = item.redeemed
        ? `<span class="pill ok">已兌換</span>`
        : `<span class="pill no">未兌換</span>`;
      const redeemer = item.redeemed
        ? `${escapeHtml(item.redeemed_by_patient_name || "")}${item.redeemed_by_display_name ? ` / ${escapeHtml(item.redeemed_by_display_name)}` : ""}`
        : "-";
      return `
        <tr>
          <td>${escapeHtml(item.code)}</td>
          <td>${escapeHtml(item.shots_granted)}</td>
          <td>${redeemed}</td>
          <td>${redeemer || "-"}</td>
          <td>${escapeHtml(maskLineId(item.redeemed_by_line_user_id))}</td>
          <td>${escapeHtml(formatDate(item.redeemed_at))}</td>
        </tr>
      `;
    })
    .join("");
}

function renderUsers(items) {
  if (!items.length) {
    el.usersTableBody.innerHTML = `<tr><td colspan="7" class="muted">查無資料</td></tr>`;
    return;
  }

  el.usersTableBody.innerHTML = items
    .map((item) => {
      const providers = (item.providers || []).length ? item.providers.join(", ") : "-";
      return `
        <tr>
          <td>${escapeHtml(item.user_id)}</td>
          <td>${escapeHtml(item.display_name || "-")}</td>
          <td>${escapeHtml(item.patient_name || "-")}</td>
          <td>${escapeHtml(item.remaining_shots)}</td>
          <td>${escapeHtml(providers)}</td>
          <td>${escapeHtml(maskLineId(item.line_user_id))}</td>
          <td>${escapeHtml(formatDate(item.created_at))}</td>
        </tr>
      `;
    })
    .join("");
}

async function loadOverview() {
  const data = await getJson("/api/admin/overview", {
    admin_key: getAdminKey(),
  });
  renderOverview(data);
}

async function loadTokens() {
  const data = await getJson("/api/admin/tokens", {
    admin_key: getAdminKey(),
    status: el.tokenStatusFilter.value,
    limit: 200,
    offset: 0,
  });
  renderTokens(data.items || []);
}

async function loadUsers() {
  const data = await getJson("/api/admin/users", {
    admin_key: getAdminKey(),
    keyword: el.userKeyword.value.trim(),
    limit: 200,
    offset: 0,
  });
  renderUsers(data.items || []);
}

async function loadAll() {
  const key = getAdminKey();
  if (!key) {
    setStatus("請先輸入管理金鑰後再查詢。", "error");
    return;
  }

  setStatus("讀取中...");
  try {
    await Promise.all([loadOverview(), loadTokens(), loadUsers()]);
    setStatus("資料更新完成。", "ok");
  } catch (e) {
    setStatus(`讀取失敗：${e.message}`, "error");
  }
}

async function generateTokens() {
  const adminKey = getAdminKey();
  if (!adminKey) {
    setStatus("請先輸入管理金鑰。", "error");
    return;
  }

  try {
    setStatus("Token 產生中...");
    const data = await postJson("/api/admin/quota/generate", {
      admin_key: adminKey,
      count: Number(el.genCount.value || 1),
      prefix: (el.genPrefix.value || "PINP").trim() || "PINP",
      shots_granted: Number(el.genShots.value || 10),
    });

    const lines = [
      `# count=${data.count}, shots_per_token=${data.shots_per_token}`,
      ...(data.codes || []),
    ];
    el.generatedCodes.value = lines.join("\n");
    setStatus(`已產生 ${data.count} 組 token。`, "ok");

    await loadOverview();
    await loadTokens();
  } catch (e) {
    setStatus(`產生失敗：${e.message}`, "error");
  }
}

function saveAdminKey() {
  const key = getAdminKey();
  if (!key) {
    setStatus("請輸入有效管理金鑰。", "error");
    return;
  }
  localStorage.setItem(ADMIN_KEY_STORAGE, key);
  setStatus("管理金鑰已儲存於本機瀏覽器。", "ok");
}

function bootstrap() {
  const saved = localStorage.getItem(ADMIN_KEY_STORAGE);
  if (saved) {
    el.adminKeyInput.value = saved;
    setStatus("已載入本機儲存的管理金鑰。", "ok");
  }

  el.saveAdminKeyBtn.addEventListener("click", saveAdminKey);
  el.refreshAllBtn.addEventListener("click", loadAll);
  el.reloadTokensBtn.addEventListener("click", loadTokens);
  el.reloadUsersBtn.addEventListener("click", loadUsers);
  el.generateBtn.addEventListener("click", generateTokens);
  el.tokenStatusFilter.addEventListener("change", loadTokens);
  el.userKeyword.addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadUsers();
  });

  if (saved) {
    loadAll();
  }
}

document.addEventListener("DOMContentLoaded", bootstrap);
