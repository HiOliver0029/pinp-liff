/* ── LIFF 初始化 & 全域狀態 ────────────────────────────────── */

const LIFF_ID = "YOUR_LIFF_ID"; // ← 請替換為實際 LIFF ID
const REACT_SECONDS = 15 * 60;  // 試紙反應時間 (秒)

let liffProfile = null;
let timerInterval = null;
let timerRemaining = REACT_SECONDS;

async function main() {
    try {
        await liff.init({ liffId: LIFF_ID });
        if (!liff.isLoggedIn()) {
            liff.login();
            return;
        }
        liffProfile = await liff.getProfile();
    } catch (e) {
        console.warn("LIFF 初始化失敗（外部瀏覽器模式）:", e);
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

    // SVG 圓形進度（238.76 = 2π × 38）
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
            canvas.width = 80; canvas.height = 80;
            const ctx = canvas.getContext("2d");
            ctx.drawImage(img, 0, 0, 80, 80);
            const data = ctx.getImageData(0, 0, 80, 80).data;
            let sum = 0;
            for (let i = 0; i < data.length; i += 4) {
                // ITU-R BT.601 亮度公式
                sum += 0.299 * data[i] + 0.587 * data[i+1] + 0.114 * data[i+2];
            }
            const avg = sum / (80 * 80);
            resolve(avg);
            URL.revokeObjectURL(img.src);
        };
        img.onerror = () => resolve(128); // 無法偵測時給中間值
        img.src = URL.createObjectURL(file);
    });
}

/* ── 相機觸發與上傳 ──────────────────────────────────────── */

function triggerCamera() {
    document.getElementById("cameraInput").click();
}

document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("cameraInput").addEventListener("change", handleFileSelected);
});

async function handleFileSelected(e) {
    const file = e.target.files[0];
    if (!file) return;

    // 亮度檢查
    const brightness = await checkBrightness(file);
    const hint = document.getElementById("lightHint");
    if (brightness < 60) {
        hint.classList.add("show");
        // 仍允許繼續，但給予提示
    } else {
        hint.classList.remove("show");
    }

    // 顯示載入遮罩
    const overlay = document.getElementById("loadingOverlay");
    overlay.classList.add("show");

    try {
        const formData = new FormData();
        formData.append("file", file);
        if (liffProfile) {
            formData.append("line_user_id", liffProfile.userId);
            formData.append("device_info", navigator.userAgent.substring(0, 200));
        }

        const res = await fetch("/api/upload", { method: "POST", body: formData });

        if (res.status === 422) {
            const err = await res.json();
            overlay.classList.remove("show");
            alert(`⚠️ ${err.message || "C 線無效，請重新操作後再拍照。"}`);
            return;
        }
        if (!res.ok) {
            throw new Error(`伺服器錯誤 (${res.status})`);
        }

        const data = await res.json();
        overlay.classList.remove("show");
        showResult(data);
        goStep(4);

    } catch (err) {
        overlay.classList.remove("show");
        console.error(err);
        alert("上傳失敗，請確認網路連線後重試。");
    }

    // 清空，允許再次選同一檔案
    e.target.value = "";
}

/* ── 顯示結果 ────────────────────────────────────────────── */

const STATUS_STYLE = {
    green:  { bg: "#28a745", label: "骨骼生長良好 ✅", barColor: "#28a745" },
    yellow: { bg: "#ffc107", label: "骨骼狀態穩定 🟡", barColor: "#ffc107" },
    red:    { bg: "#dc3545", label: "⚠️ 需警示，建議諮詢醫師", barColor: "#dc3545" },
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
    requestAnimationFrame(() => { fill.style.width = pct + "%"; });
}

/* ── 關閉 LIFF ───────────────────────────────────────────── */

function closeLiff() {
    if (liff.isInClient && liff.isInClient()) {
        liff.closeWindow();
    } else {
        window.close();
    }
}

main();
