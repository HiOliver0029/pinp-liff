/* ── 初始化與全域狀態 ───────────────────────────────────────── */

let LIFF_ID = "";
const REACT_SECONDS = 15 * 60;
const SESSION_STORAGE_KEY = "epoch_session_token";

let liffProfile = null;
let timerInterval = null;
let timerRemaining = REACT_SECONDS;

let authSessionToken = "";
let authProvider = "";
let currentDisplayName = "";
let currentPatientName = "";
let remainingShots = 0;
let googleClientId = "";
let demoAllowGuestUpload = false;
let demoSkipTokenCheck = false;

/* ── 共用 UI Helpers ───────────────────────────────────────── */

function setQuotaMessage(message, tone = "info") {
    const el = document.getElementById("quotaMsg");
    if (!el) return;
    el.textContent = message || "";
    if (tone === "error") el.style.color = "#c62828";
    else if (tone === "success") el.style.color = "#1a7f3c";
    else el.style.color = "#666";
}

function updateAuthUI() {
    const statusEl = document.getElementById("authStatusText");
    const patientEl = document.getElementById("authPatientName");
    const shotsEl = document.getElementById("authShots");
    const badgeEl = document.getElementById("quotaBadge");

    const lineBtn = document.getElementById("lineLoginBtn");
    const captureBtn = document.getElementById("captureBtn");
    const galleryBtn = document.getElementById("galleryBtn");

    const isLoggedIn = Boolean(authSessionToken);
    const hasShots = remainingShots > 0;
    const canShootWithoutLogin = demoAllowGuestUpload;
    const canShootWithoutToken = demoSkipTokenCheck;

    if (statusEl) {
        if (isLoggedIn) {
            const providerText = authProvider === "google" ? "Google" : "LINE";
            const name = currentDisplayName || "使用者";
            statusEl.innerHTML = `<span class="auth-ok">✅ 已登入（${providerText}）</span>｜${name}`;
        } else if (canShootWithoutLogin) {
            statusEl.innerHTML = `<span class="auth-ok">🧪 Demo 模式</span>｜未登入也可直接使用拍照分析。`;
        } else {
            statusEl.innerHTML = `<span class="auth-warn">🔐 尚未登入</span>，請先使用 LINE 或 Google 登入。`;
        }
    }

    if (patientEl) patientEl.textContent = currentPatientName || "—";

    if (shotsEl) {
        if (canShootWithoutToken) {
            shotsEl.textContent = "不限";
            shotsEl.className = "auth-ok";
        } else {
            shotsEl.textContent = String(remainingShots);
            shotsEl.className = remainingShots > 0 ? "auth-ok" : "auth-warn";
        }
    }

    if (badgeEl) {
        badgeEl.textContent = canShootWithoutToken ? "Demo 免 Token" : `剩餘 ${remainingShots} 次`;
    }

    if (lineBtn) {
        lineBtn.style.display = "block";
        lineBtn.disabled = !LIFF_ID;
        if (!LIFF_ID) {
            lineBtn.textContent = "LINE 登入未設定（缺少 CAMERA_LIFF_ID）";
        } else {
            lineBtn.textContent = "使用 LINE 登入";
        }
    }

    const canShoot = (isLoggedIn || canShootWithoutLogin) && (canShootWithoutToken || hasShots);
    if (captureBtn) captureBtn.disabled = !canShoot;
    if (galleryBtn) galleryBtn.disabled = !canShoot;

    if (!isLoggedIn && !canShootWithoutLogin) {
        setQuotaMessage("請先登入並完成帳號綁定。", "info");
    } else if (!canShootWithoutToken && !hasShots) {
        setQuotaMessage("拍攝額度為 0，請先輸入 Token 或掃描 QR Code 兌換。", "info");
    } else if (canShootWithoutToken) {
        setQuotaMessage("Demo 模式已啟用：可直接拍攝，無需 Token。", "success");
    }
}

function saveSession(token, provider) {
    authSessionToken = token || "";
    authProvider = provider || "";
    if (authSessionToken) {
        localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({
            token: authSessionToken,
            provider: authProvider,
        }));
    }
}

function clearSession() {
    authSessionToken = "";
    authProvider = "";
    currentDisplayName = "";
    currentPatientName = "";
    remainingShots = 0;
    localStorage.removeItem(SESSION_STORAGE_KEY);
    updateAuthUI();
}

/* ── API 呼叫 ────────────────────────────────────────────── */

async function fetchPublicConfig() {
    try {
        const res = await fetch("/api/config/public");
        if (!res.ok) return;
        const data = await res.json();
        googleClientId = data.google_client_id || "";
        LIFF_ID = data.camera_liff_id || "";
        demoAllowGuestUpload = Boolean(data.demo_allow_guest_upload);
        demoSkipTokenCheck = Boolean(data.demo_skip_token_check);
    } catch (e) {
        console.warn("讀取公開設定失敗", e);
    }
}

async function applyAuthPayload(data) {
    saveSession(data.session_token, data.provider);
    currentDisplayName = data.display_name || "";
    currentPatientName = data.patient_name || "";
    remainingShots = Number(data.remaining_shots || 0);
    updateAuthUI();
}

async function loginWithLine() {
    if (!liffProfile || !liffProfile.userId) {
        throw new Error("目前非 LINE 內建環境，無法取得 LINE userId");
    }

    const res = await fetch("/api/auth/line", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            line_user_id: liffProfile.userId,
            display_name: liffProfile.displayName || "",
        }),
    });

    if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "LINE 登入失敗");
    }

    const data = await res.json();
    await applyAuthPayload(data);
    setQuotaMessage("LINE 帳號登入成功，可開始兌換拍攝額度。", "success");
}

async function loginWithGoogleIdToken(idToken) {
    const res = await fetch("/api/auth/google", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            id_token: idToken,
            line_user_id: liffProfile?.userId || null,
        }),
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Google 登入失敗");
    }

    const data = await res.json();
    await applyAuthPayload(data);
    setQuotaMessage("Google 帳號登入成功，可開始兌換拍攝額度。", "success");
}

async function refreshQuotaStatus() {
    if (!authSessionToken) return;

    const res = await fetch(`/api/quota/status?session_token=${encodeURIComponent(authSessionToken)}`);
    if (!res.ok) {
        if (res.status === 401) clearSession();
        return;
    }

    const data = await res.json();
    currentDisplayName = data.display_name || currentDisplayName;
    currentPatientName = data.patient_name || currentPatientName;
    remainingShots = Number(data.remaining_shots || 0);
    updateAuthUI();
}

async function restoreSessionIfAny() {
    const raw = localStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return;

    try {
        const saved = JSON.parse(raw);
        authSessionToken = saved.token || "";
        authProvider = saved.provider || "";
        if (!authSessionToken) return;
        await refreshQuotaStatus();
    } catch {
        clearSession();
    }
}

async function redeemToken(tokenCode) {
    if (demoSkipTokenCheck) {
        setQuotaMessage("Demo 模式下可直接拍攝，暫時不需要兌換 Token。", "success");
        return;
    }

    if (!authSessionToken) {
        setQuotaMessage("請先登入，再進行 token 兌換。", "error");
        return;
    }

    const code = (tokenCode || "").trim();
    if (!code) {
        setQuotaMessage("請輸入 token 後再兌換。", "error");
        return;
    }

    const res = await fetch("/api/quota/redeem", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            session_token: authSessionToken,
            token_code: code,
        }),
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        setQuotaMessage(data.detail || "Token 兌換失敗", "error");
        return;
    }

    remainingShots = Number(data.remaining_shots || remainingShots);
    updateAuthUI();
    setQuotaMessage(`兌換成功！+${data.granted_shots} 次，剩餘 ${remainingShots} 次。`, "success");
}

/* ── Google / LINE 登入初始化 ───────────────────────────── */

async function manualLineLogin() {
    try {
        if (typeof liff === "undefined") {
            throw new Error("目前環境不支援 LINE LIFF");
        }

        if (!liff.isLoggedIn()) {
            liff.login();
            return;
        }

        if (!liffProfile) {
            liffProfile = await liff.getProfile();
        }

        await loginWithLine();
    } catch (e) {
        console.error(e);
        alert("LINE 登入失敗，請改用 Google 登入或稍後重試。");
    }
}

function initGoogleButton() {
    const wrap = document.getElementById("googleLoginWrap");
    if (!wrap) return;

    if (!googleClientId || !window.google || !google.accounts || !google.accounts.id) {
        wrap.innerHTML = `<div style="font-size:.82rem;color:#888;">Google 登入尚未設定（缺少 GOOGLE_CLIENT_ID）。</div>`;
        return;
    }

    google.accounts.id.initialize({
        client_id: googleClientId,
        callback: async (response) => {
            try {
                await loginWithGoogleIdToken(response.credential);
            } catch (e) {
                console.error(e);
                setQuotaMessage("Google 登入失敗，請稍後重試。", "error");
            }
        },
    });

    wrap.innerHTML = "";
    google.accounts.id.renderButton(wrap, {
        type: "standard",
        theme: "outline",
        size: "large",
        text: "signin_with",
        shape: "pill",
        width: 300,
    });
}

function initGoogleButtonWithRetry(retry = 0) {
    if (window.google && google.accounts && google.accounts.id) {
        initGoogleButton();
        return;
    }

    if (retry >= 20) {
        initGoogleButton();
        return;
    }

    setTimeout(() => initGoogleButtonWithRetry(retry + 1), 200);
}

async function initLiffProfile() {
    try {
        if (!LIFF_ID) {
            console.warn("未設定 CAMERA_LIFF_ID，略過 LIFF 初始化。");
            return;
        }

        await liff.init({ liffId: LIFF_ID });
        if (liff.isLoggedIn()) {
            liffProfile = await liff.getProfile();
        }
    } catch (e) {
        console.warn("LIFF 初始化失敗（外部瀏覽器模式）:", e);
    }
}

/* ── Token / QR 兌換 ────────────────────────────────────── */

function redeemTokenFromInput() {
    const input = document.getElementById("tokenInput");
    if (!input) return;
    const tokenCode = input.value.trim();
    redeemToken(tokenCode).then(() => {
        input.value = "";
    });
}

function triggerQrScan() {
    document.getElementById("qrInput").click();
}

function extractTokenFromQrRaw(raw) {
    const text = (raw || "").trim();
    if (!text) return "";

    try {
        const url = new URL(text);
        const token = url.searchParams.get("token") || url.searchParams.get("code");
        if (token) return token;
    } catch {
        // 非 URL，直接當成 token
    }

    return text;
}

async function decodeQrFromFile(file) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => {
            const canvas = document.createElement("canvas");
            canvas.width = img.width;
            canvas.height = img.height;
            const ctx = canvas.getContext("2d", { willReadFrequently: true });
            ctx.drawImage(img, 0, 0);
            const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);

            if (typeof jsQR === "undefined") {
                reject(new Error("jsQR 未載入"));
                return;
            }

            const result = jsQR(imageData.data, canvas.width, canvas.height);
            if (!result || !result.data) {
                reject(new Error("找不到 QR Code"));
                return;
            }
            resolve(result.data);
        };
        img.onerror = () => reject(new Error("無法讀取 QR 圖片"));
        img.src = URL.createObjectURL(file);
    });
}

async function handleQrSelected(e) {
    const file = e.target.files[0];
    if (!file) return;

    try {
        const raw = await decodeQrFromFile(file);
        const tokenCode = extractTokenFromQrRaw(raw);
        if (!tokenCode) {
            setQuotaMessage("QR 內容不含可兌換 token。", "error");
            return;
        }
        await redeemToken(tokenCode);
    } catch (err) {
        console.error(err);
        setQuotaMessage("QR 掃描失敗，請改用手動輸入 token。", "error");
    } finally {
        e.target.value = "";
    }
}

/* ── 步驟切換 ────────────────────────────────────────────── */

function goStep(n) {
    [1, 2, 3, 4].forEach(i => {
        document.getElementById(`step${i}`).classList.toggle("active", i === n);
        const si = document.getElementById(`si${i}`);
        si.classList.remove("active", "done");
        if (i < n) si.classList.add("done");
        if (i === n) si.classList.add("active");
    });
    if (n !== 2) stopTimer();
}

/* ── 倒數計時器 ──────────────────────────────────────────── */

function startTimer() {
    document.getElementById("startTimerBtn").disabled = true;
    timerRemaining = REACT_SECONDS;
    updateTimerUI();
    timerInterval = setInterval(() => {
        timerRemaining -= 1;
        updateTimerUI();
        if (timerRemaining <= 0) {
            stopTimer();
            goStep(3);
        }
    }, 1000);
}

function stopTimer() {
    if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
    }
}

function updateTimerUI() {
    const m = Math.floor(timerRemaining / 60);
    const s = timerRemaining % 60;
    document.getElementById("timerDisplay").textContent =
        `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;

    const pct = timerRemaining / REACT_SECONDS;
    const offset = 238.76 * (1 - pct);
    document.getElementById("progressCircle").setAttribute("stroke-dashoffset", offset.toFixed(2));
}

/* ── 亮度偵測 ────────────────────────────────────────────── */

async function checkBrightness(file) {
    return new Promise(resolve => {
        const img = new Image();
        img.onload = () => {
            const canvas = document.createElement("canvas");
            canvas.width = 80;
            canvas.height = 80;
            const ctx = canvas.getContext("2d");
            ctx.drawImage(img, 0, 0, 80, 80);
            const data = ctx.getImageData(0, 0, 80, 80).data;
            let sum = 0;
            for (let i = 0; i < data.length; i += 4) {
                sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
            }
            resolve(sum / (80 * 80));
            URL.revokeObjectURL(img.src);
        };
        img.onerror = () => resolve(128);
        img.src = URL.createObjectURL(file);
    });
}

/* ── 相機觸發與上傳 ──────────────────────────────────────── */

function assertCanShoot() {
    if (!authSessionToken && !demoAllowGuestUpload) {
        alert("請先登入 LINE 或 Google 帳號。");
        return false;
    }
    if (!demoSkipTokenCheck && remainingShots <= 0) {
        alert("拍攝額度不足，請先輸入 token 或掃描 QR code 兌換。\n每組 token 可獲得 10 次拍攝機會。");
        return false;
    }
    return true;
}

function triggerCamera() {
    if (!assertCanShoot()) return;
    document.getElementById("cameraInput").click();
}

function triggerGallery() {
    if (!assertCanShoot()) return;
    document.getElementById("galleryInput").click();
}

async function handleFileSelected(e) {
    const file = e.target.files[0];
    if (!file) return;

    if (!assertCanShoot()) {
        e.target.value = "";
        return;
    }

    const brightness = await checkBrightness(file);
    const hint = document.getElementById("lightHint");
    if (brightness < 60) hint.classList.add("show");
    else hint.classList.remove("show");

    const overlay = document.getElementById("loadingOverlay");
    overlay.classList.add("show");

    try {
        const formData = new FormData();
        formData.append("file", file);
        if (authSessionToken) {
            formData.append("session_token", authSessionToken);
        }
        if (liffProfile?.userId) formData.append("line_user_id", liffProfile.userId);
        formData.append("device_info", navigator.userAgent.substring(0, 200));

        const res = await fetch("/api/upload", { method: "POST", body: formData });
        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
            overlay.classList.remove("show");
            if (typeof data.remaining_shots === "number") {
                remainingShots = data.remaining_shots;
                updateAuthUI();
            }
            if (res.status === 401) {
                if (!demoAllowGuestUpload) {
                    clearSession();
                    alert("登入已失效，請重新登入。");
                } else {
                    alert("目前伺服器尚未啟用 Demo 免登入模式，請先登入後再試。\n（確認 .env 的 DEMO_ALLOW_GUEST_UPLOAD=true）");
                }
                return;
            }
            if (res.status === 403) {
                alert(data.message || "拍攝額度不足，請先兌換 token。");
                return;
            }
            if (res.status === 422) {
                alert(`⚠️ ${data.message || "C 線無效，請重新操作後再拍照。"}`);
                return;
            }
            throw new Error(data.detail || `伺服器錯誤 (${res.status})`);
        }

        remainingShots = Number(
            data.remaining_shots !== null && data.remaining_shots !== undefined
                ? data.remaining_shots
                : remainingShots
        );
        updateAuthUI();

        overlay.classList.remove("show");
        showResult(data);
        goStep(4);
    } catch (err) {
        overlay.classList.remove("show");
        console.error(err);
        alert("上傳失敗，請確認網路連線後重試。");
    }

    document.getElementById("cameraInput").value = "";
    document.getElementById("galleryInput").value = "";
}

/* ── 顯示結果 ────────────────────────────────────────────── */

const STATUS_STYLE = {
    green: { bg: "#28a745", label: "骨骼生長良好 ✅", barColor: "#28a745" },
    yellow: { bg: "#ffc107", label: "骨骼狀態穩定 🟡", barColor: "#ffc107" },
    red: { bg: "#dc3545", label: "⚠️ 需警示，建議諮詢醫師", barColor: "#dc3545" },
};

function showResult(data) {
    const style = STATUS_STYLE[data.status_color] || STATUS_STYLE.red;
    document.getElementById("resultHeader").textContent = style.label;
    document.getElementById("resultHeader").style.background = style.bg;
    document.getElementById("concValue").textContent = data.concentration;
    document.getElementById("concValue").style.color = style.bg;
    document.getElementById("resultDesc").textContent = data.description || "";

    const pct = Math.min(100, Math.round(data.concentration));
    const fill = document.getElementById("powerBarFill");
    fill.style.width = "0%";
    fill.style.background = style.barColor;
    requestAnimationFrame(() => {
        fill.style.width = `${pct}%`;
    });
}

/* ── 關閉 LIFF ───────────────────────────────────────────── */

function closeLiff() {
    try {
        if (typeof liff !== "undefined" && liff.isInClient && liff.isInClient()) {
            liff.closeWindow();
            return;
        }
    } catch {
        // ignore
    }
    window.close();
}

/* ── 主流程 ──────────────────────────────────────────────── */

async function main() {
    document.getElementById("cameraInput").addEventListener("change", handleFileSelected);
    document.getElementById("galleryInput").addEventListener("change", handleFileSelected);
    document.getElementById("qrInput").addEventListener("change", handleQrSelected);

    updateAuthUI();
    await fetchPublicConfig();
    await initLiffProfile();
    await restoreSessionIfAny();

    // 在 LINE 內建瀏覽器中，自動以 LINE 帳號綁定登入
    if (!authSessionToken && liffProfile?.userId) {
        try {
            await loginWithLine();
        } catch (e) {
            console.warn("LINE 自動登入失敗", e);
        }
    }

    initGoogleButtonWithRetry();
    updateAuthUI();
}

document.addEventListener("DOMContentLoaded", main);
