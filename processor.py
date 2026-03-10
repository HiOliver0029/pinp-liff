import cv2
import numpy as np

# C 線最低有效強度門檻（顯色強度 < 此值視為無效檢測）
C_LINE_MIN_INTENSITY = 15.0

# 線性回歸係數（需根據實際長庚標準校準數據調整，目前為模擬值）
# PINP (ng/mL) = T_intensity * REGRESSION_SLOPE + REGRESSION_INTERCEPT
REGRESSION_SLOPE = 0.7
REGRESSION_INTERCEPT = 5.0


def _extract_intensity(gray: np.ndarray, h: int, w: int) -> tuple[float, float]:
    """
    從灰階影像中提取 T 線與 C 線的顯色強度 (intensity = 255 - mean_gray)。
    T 線位於試紙上方 1/4 ~ 1/3，C 線位於 1/3 ~ 1/2。
    """
    t_region = gray[h // 4: h // 3, w // 4: 3 * w // 4]
    c_region = gray[h // 3: h // 2, w // 4: 3 * w // 4]
    t_intensity = float(255 - np.mean(t_region))
    c_intensity = float(255 - np.mean(c_region))
    return t_intensity, c_intensity


def _normalize_with_white_balance(img: np.ndarray) -> np.ndarray:
    """
    簡易白平衡校正：以影像四個角落的均值作為白色參考，
    縮放各通道以消除環境光色偏，提升不同手機/光源下的一致性。
    """
    h, w = img.shape[:2]
    margin = max(10, min(h, w) // 10)
    corners = [
        img[:margin, :margin],
        img[:margin, -margin:],
        img[-margin:, :margin],
        img[-margin:, -margin:],
    ]
    ref = np.mean(np.vstack([c.reshape(-1, 3) for c in corners]), axis=0)
    scale = 200.0 / (ref + 1e-6)  # 避免除以零
    corrected = np.clip(img.astype(np.float32) * scale, 0, 255).astype(np.uint8)
    return corrected


def analyze_pinp_strip(message_id: str, image_bytes: bytes = None) -> dict:
    """
    AI 影像判讀核心
    1. 簡易白平衡校正，降低環境光干擾
    2. 提取 T 線顯色強度（灰階反轉值）
    3. C 線有效性驗證（強度不足 → 無效檢測）
    4. T/C 比值換算 PINP 濃度 (ng/mL)
    與醫院大型機器 (ECLIA) 目標相關性 R^2 ≥ 0.99
    Returns:
        dict with keys: concentration, gray_value, c_valid
    """
    if image_bytes is None:
        # 開發測試用模擬數值
        return {"concentration": 65.5, "gray_value": 128.0, "c_valid": True}

    try:
        np_arr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if img is None:
            return {"concentration": 0.0, "gray_value": 0.0, "c_valid": False}

        # 白平衡校正
        img = _normalize_with_white_balance(img)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        t_intensity, c_intensity = _extract_intensity(gray, h, w)

        # C 線有效性驗證：C 線太淡代表血清量不足或操作錯誤
        if c_intensity < C_LINE_MIN_INTENSITY:
            return {
                "concentration": 0.0,
                "gray_value": round(t_intensity, 2),
                "c_valid": False,
            }

        # 以 T/C 比值消除批次間試紙個體差異，再套入線性模型換算濃度
        tc_ratio = t_intensity / c_intensity
        concentration = max(0.0, tc_ratio * 80.0)

        return {
            "concentration": round(concentration, 2),
            "gray_value": round(t_intensity, 2),
            "c_valid": True,
        }

    except Exception as e:
        print(f"影像分析錯誤: {e}")
        return {"concentration": 0.0, "gray_value": 0.0, "c_valid": False}


def determine_status(concentration: float) -> str:
    """
    根據 PINP 濃度判斷骨骼狀態顏色
    綠色：生長中 (> 50 ng/mL)
    黃色：穩定 (25–50 ng/mL)
    紅色：需警示 (< 25 ng/mL)
    """
    if concentration > 50:
        return "green"
    elif concentration >= 25:
        return "yellow"
    else:
        return "red"
