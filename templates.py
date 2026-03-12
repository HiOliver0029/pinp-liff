"""
Flex Message 模板工廠
--------------------
用法：
    from templates import build_result_flex
    from linebot.v3.messaging import FlexMessage, FlexContainer

    flex_dict = build_result_flex(
        concentration=65.5,
        status_color="green",
        patient_name="王大明 先生",
        liff_trends_url="https://liff.line.me/xxxx-yyyy",
    )
    message = FlexMessage(
        alt_text="您的骨骼健康報告",
        contents=FlexContainer.from_dict(flex_dict),
    )
"""

from datetime import datetime

# ── 三色狀態設定 ──────────────────────────────────────────────────────────────

_STATUS_CONFIG = {
    "green": {
        "color": "#28a745",
        "label": "生長良好 (綠燈)",
        "description": (
            "目前的骨鬆藥物反應良好，顯示骨骼正在積極生長。"
            "請維持目前的用藥習慣與生活型態。"
        ),
    },
    "yellow": {
        "color": "#e6a817",
        "label": "狀態穩定 (黃燈)",
        "description": (
            "骨骼狀態穩定，建議繼續維持藥物治療並定期檢測追蹤，"
            "如有疑問請諮詢醫師。"
        ),
    },
    "red": {
        "color": "#dc3545",
        "label": "需警示 (紅燈)",
        "description": (
            "骨骼生長指數偏低，建議儘快諮詢骨科或復健科醫師，"
            "評估是否需要調整治療方案。"
        ),
    },
}


def build_result_flex(
    concentration: float,
    status_color: str,
    patient_name: str = "使用者",
    date_str: str | None = None,
    liff_trends_url: str = "https://liff.line.me/YOUR_TRENDS_LIFF_ID",
) -> dict:
    """
    將動態數值組裝成 PINP 骨骼健康報告 Flex Message JSON dict。

    Parameters
    ----------
    concentration   : PINP 濃度 (ng/mL)
    status_color    : "green" | "yellow" | "red"
    patient_name    : 顯示在報告上的受檢者名稱
    date_str        : 日期字串 (預設今日 YYYY-MM-DD)
    liff_trends_url : 趨勢圖 LIFF 完整 URL (https://liff.line.me/xxxx)

    Returns
    -------
    dict — 可直接傳入 FlexContainer.from_dict()
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    status = _STATUS_CONFIG.get(status_color, _STATUS_CONFIG["red"])

    # 進度條比例：以 100 ng/mL 為滿格，最小 1、最大 99 避免 flex=0 渲染異常
    bar_filled = max(1, min(int(concentration), 99))
    bar_empty = 100 - bar_filled

    return {
        "type": "bubble",
        "size": "giga",
        # ── 標題列 ──
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#27ACB2",
            "paddingAll": "20px",
            "contents": [
                {
                    "type": "text",
                    "text": "PINP 骨骼健康報告",
                    "weight": "bold",
                    "size": "xl",
                    "color": "#ffffff",
                }
            ],
        },
        # ── 主體 ──
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                # 受檢者
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "text",
                            "text": "受檢者：",
                            "size": "sm",
                            "color": "#8c8c8c",
                            "flex": 1,
                        },
                        {
                            "type": "text",
                            "text": patient_name,
                            "size": "sm",
                            "color": "#111111",
                            "flex": 2,
                            "align": "end",
                        },
                    ],
                },
                # 檢測日期
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "contents": [
                        {
                            "type": "text",
                            "text": "檢測日期：",
                            "size": "sm",
                            "color": "#8c8c8c",
                            "flex": 1,
                        },
                        {
                            "type": "text",
                            "text": date_str,
                            "size": "sm",
                            "color": "#111111",
                            "flex": 2,
                            "align": "end",
                        },
                    ],
                },
                # 分隔線
                {"type": "separator", "margin": "xl"},
                # PINP 濃度大字
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "xl",
                    "contents": [
                        {
                            "type": "text",
                            "text": "今日 PINP 濃度",
                            "size": "sm",
                            "color": "#8c8c8c",
                            "align": "center",
                        },
                        {
                            "type": "text",
                            "text": f"{concentration:.1f} ng/mL",
                            "size": "xxl",
                            "weight": "bold",
                            "color": "#111111",
                            "align": "center",
                            "margin": "sm",
                        },
                    ],
                },
                # 骨骼動力條
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "contents": [
                        {
                            "type": "text",
                            "text": "骨骼動力條 (藥效反應)",
                            "size": "xs",
                            "color": "#8c8c8c",
                            "margin": "md",
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "margin": "sm",
                            "backgroundColor": "#e0e0e0",
                            "cornerRadius": "20px",
                            "contents": [
                                {
                                    "type": "box",
                                    "layout": "vertical",
                                    "contents": [],
                                    "backgroundColor": status["color"],
                                    "flex": bar_filled,
                                    "height": "12px",
                                    "cornerRadius": "20px",
                                },
                                {
                                    "type": "box",
                                    "layout": "vertical",
                                    "contents": [],
                                    "backgroundColor": "#e0e0e0",
                                    "flex": bar_empty,
                                    "height": "12px",
                                    "cornerRadius": "20px",
                                },
                            ],
                        },
                    ],
                },
                # 狀態說明
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "xl",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"● 狀態：{status['label']}",
                            "color": status["color"],
                            "weight": "bold",
                            "size": "md",
                        },
                        {
                            "type": "text",
                            "text": status["description"],
                            "wrap": True,
                            "color": "#666666",
                            "size": "sm",
                            "margin": "md",
                        },
                    ],
                },
            ],
        },
        # ── 頁尾 ──
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#27ACB2",
                    "action": {
                        "type": "uri",
                        "label": "查看詳細趨勢圖",
                        "uri": liff_trends_url,
                    },
                },
                {
                    "type": "text",
                    "text": "此報告可縮短 50% 診間溝通時間，回診時請出示給醫師參考。",
                    "size": "xxs",
                    "color": "#aaaaaa",
                    "align": "center",
                    "margin": "md",
                    "wrap": True,
                },
            ],
        },
    }
