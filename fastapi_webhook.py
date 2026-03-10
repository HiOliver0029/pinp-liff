import datetime
import os

from dotenv import load_dotenv
load_dotenv()  # 從 .env 載入機密環境變數

from fastapi import FastAPI, Request, Depends, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, TextMessage, FlexMessage, FlexContainer
)
from linebot.v3.webhooks import MessageEvent, ImageMessageContent

from database import SessionLocal, engine
import models
import processor

# 確保資料表已建立
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Epoch PINP 骨骼健康監測系統")

# 掛載靜態檔案 (LIFF 頁面)
app.mount("/static", StaticFiles(directory="static"), name="static")

# LINE Bot 設定（金鑰從環境變數 / .env 檔案載入，請勿硬編碼）
configuration = Configuration(
    access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
)
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])


# ── 資料庫依賴項 ────────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── 輔助函式 ────────────────────────────────────────────────────────────────────

STATUS_CONFIG = {
    "green":  {
        "label": "骨骼生長良好",
        "emoji": "✅",
        "color": "#28a745",
        "bg":    "#eafbea",
        "desc":  "PINP 濃度良好，顯示骨骼正在有效生長，藥效反應佳。",
    },
    "yellow": {
        "label": "骨骼狀態穩定",
        "emoji": "🟡",
        "color": "#ffc107",
        "bg":    "#fff8e1",
        "desc":  "數值穩定，維持目前治療方案，定期監測即可。",
    },
    "red": {
        "label": "需警示，建議諮詢醫師",
        "emoji": "⚠️",
        "color": "#dc3545",
        "bg":    "#fdecea",
        "desc":  "PINP 濃度偏低，建議儘快聯絡主治醫師評估調整藥方。",
    },
}


def _build_flex_contents(concentration: float, status_color: str) -> dict:
    """建立骨骼動力條 Flex Message 內容"""
    cfg = STATUS_CONFIG.get(status_color, STATUS_CONFIG["red"])
    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": cfg["color"],
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "text",
                    "text": "骨骼健康檢測報告",
                    "color": "#ffffff",
                    "weight": "bold",
                    "size": "lg",
                },
                {
                    "type": "text",
                    "text": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "color": "#ffffff",
                    "size": "sm",
                    "margin": "sm",
                },
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"PINP 濃度",
                    "color": "#888888",
                    "size": "sm",
                },
                {
                    "type": "text",
                    "text": f"{concentration} ng/mL",
                    "size": "3xl",
                    "weight": "bold",
                    "color": cfg["color"],
                },
                {
                    "type": "separator",
                    "margin": "md",
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "contents": [
                        {
                            "type": "text",
                            "text": cfg["emoji"],
                            "size": "xl",
                            "flex": 0,
                        },
                        {
                            "type": "text",
                            "text": cfg["label"],
                            "weight": "bold",
                            "color": cfg["color"],
                            "margin": "sm",
                        },
                    ],
                },
                {
                    "type": "text",
                    "text": cfg["desc"],
                    "wrap": True,
                    "color": "#555555",
                    "size": "sm",
                    "margin": "sm",
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "backgroundColor": "#f0f0f0",
                    "cornerRadius": "8px",
                    "paddingAll": "4px",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "vertical",
                            "backgroundColor": cfg["color"],
                            "cornerRadius": "6px",
                            "height": "12px",
                            "width": f"{min(100, int(concentration))}%",
                            "contents": [],
                        }
                    ],
                },
            ],
        },
    }


def send_result_flex(reply_token: str, concentration: float, status_color: str):
    """推播 PINP 檢測結果 Flex Message 給使用者"""
    try:
        flex_contents = _build_flex_contents(concentration, status_color)
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        FlexMessage(
                            alt_text=f"PINP 濃度：{concentration} ng/mL",
                            contents=FlexContainer.from_dict(flex_contents),
                        )
                    ],
                )
            )
    except Exception as e:
        print(f"Flex Message 發送失敗，改以文字回覆: {e}")
        cfg = STATUS_CONFIG.get(status_color, STATUS_CONFIG["red"])
        fallback_text = (
            f"✅ 檢測完成！\nPINP 濃度：{concentration} ng/mL\n狀態：{cfg['emoji']} {cfg['label']}"
        )
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=fallback_text)],
                )
            )


def _save_record(
    db: Session,
    line_user_id: str,
    result: dict,
    status_color: str,
    image_path: str,
    device_info: str = "",
):
    """建立或取得 User/Patient，並儲存本次檢測紀錄"""
    user = db.query(models.User).filter(
        models.User.line_user_id == line_user_id
    ).first()
    if not user:
        user = models.User(line_user_id=line_user_id, display_name="")
        db.add(user)
        db.flush()

    if not user.patients:
        patient = models.Patient(
            name="預設病患", age=0, medication="", caregiver_id=user.id
        )
        db.add(patient)
        db.flush()
    else:
        patient = user.patients[0]

    new_record = models.DetectionRecord(
        patient_id=patient.id,
        concentration=result["concentration"],
        gray_value=result["gray_value"],
        status_color=status_color,
        image_path=image_path,
        device_info=device_info,
        detected_at=datetime.datetime.utcnow(),
    )
    db.add(new_record)
    db.commit()


# ── LINE Webhook ────────────────────────────────────────────────────────────────

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        return {"status": "error", "message": "Invalid signature"}
    return {"status": "ok"}


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    """當使用者傳送試紙照片時觸發"""
    db = SessionLocal()
    try:
        line_user_id = event.source.user_id

        # 1. 從 LINE 下載影像
        with ApiClient(configuration) as api_client:
            image_bytes = MessagingApiBlob(api_client).get_message_content(
                message_id=event.message.id
            )

        # 儲存原始影像
        os.makedirs("images", exist_ok=True)
        image_path = f"images/{event.message.id}.jpg"
        with open(image_path, "wb") as f:
            f.write(image_bytes)

        # 2. AI 影像分析：識別 T/C 線並計算濃度
        result = processor.analyze_pinp_strip(event.message.id, image_bytes)

        # C 線無效 → 告知使用者重拍
        if not result.get("c_valid", True):
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(
                            text="⚠️ 檢測無效：C 線（控制線）顯色不足，可能是血清量不足或反應時間未到。\n請依步驟重新操作後再拍照。"
                        )],
                    )
                )
            return

        # 3. 判斷狀態顏色：綠 (生長)、黃 (穩定)、紅 (需警示)
        status_color = processor.determine_status(result["concentration"])

        # 4. 儲存紀錄
        _save_record(db, line_user_id, result, status_color, image_path)

        # 5. 推播 Flex Message 結果
        send_result_flex(event.reply_token, result["concentration"], status_color)

    except Exception as e:
        print(f"處理影像錯誤: {e}")
        db.rollback()
    finally:
        db.close()


# ── REST API (供 LIFF 趨勢圖表使用) ────────────────────────────────────────────

@app.post("/api/upload")
async def upload_image(
    file: UploadFile = File(...),
    line_user_id: str = "",
    device_info: str = "",
    db: Session = Depends(get_db),
):
    """
    LIFF 直接上傳試紙照片的端點。
    前端透過 FormData 以 POST 傳送，欄位：file, line_user_id, device_info。
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只接受圖片檔案")

    image_bytes = await file.read()

    # 儲存原始影像
    os.makedirs("images", exist_ok=True)
    safe_filename = os.path.basename(file.filename or "upload.jpg")
    image_path = f"images/liff_{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{safe_filename}"
    with open(image_path, "wb") as f:
        f.write(image_bytes)

    # AI 影像分析
    result = processor.analyze_pinp_strip("liff_upload", image_bytes)

    if not result.get("c_valid", True):
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_cline", "message": "C 線顯色不足，請重新操作後再拍照。"},
        )

    status_color = processor.determine_status(result["concentration"])

    if line_user_id:
        _save_record(db, line_user_id, result, status_color, image_path, device_info)

    cfg = STATUS_CONFIG[status_color]
    return {
        "concentration": result["concentration"],
        "status_color": status_color,
        "status_label": cfg["label"],
        "status_emoji": cfg["emoji"],
        "description": cfg["desc"],
    }


@app.get("/api/history/{line_user_id}")
async def get_history(line_user_id: str, db: Session = Depends(get_db)):
    """回傳使用者最近 12 筆 PINP 檢測記錄（供 LIFF 趨勢圖表使用）"""
    user = db.query(models.User).filter(
        models.User.line_user_id == line_user_id
    ).first()
    if not user or not user.patients:
        return []

    records = (
        db.query(models.DetectionRecord)
        .filter(models.DetectionRecord.patient_id == user.patients[0].id)
        .order_by(models.DetectionRecord.detected_at.asc())
        .limit(12)
        .all()
    )

    return [
        {
            "date": r.detected_at.strftime("%Y-%m-%d"),
            "concentration": r.concentration,
            "color": r.status_color,
        }
        for r in records
    ]


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "Epoch PINP Monitor"}

