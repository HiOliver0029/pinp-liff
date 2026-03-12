import datetime
import os

from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, TextMessage, FlexMessage, FlexContainer
)
from linebot.v3.webhooks import MessageEvent, ImageMessageContent, TextMessageContent, FollowEvent

from database import SessionLocal, engine
import models
import processor
import templates

# 確保資料表已建立
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Epoch PINP 骨骼健康監測系統")

# 掛載靜態檔案 (LIFF 頁面)
app.mount("/static", StaticFiles(directory="static"), name="static")

# LIFF 趨勢圖 URL（在 LINE Developers Console 取得 LIFF ID 後填入 .env）
TRENDS_LIFF_URL = os.getenv(
    "TRENDS_LIFF_URL",
    "https://liff.line.me/YOUR_TRENDS_LIFF_ID",
)

# LINE Bot 設定（金鑰建議透過環境變數注入）
configuration = Configuration(
    access_token=os.getenv(
        "LINE_CHANNEL_ACCESS_TOKEN",
        "B5WyfxGzkQeux13b5JsRzMlKqnPrdkijHva91lNI5l+5Gbd66MoZYSQsn1Rt49ulTU7jWaYrRxHcquLXiMNUa9f1On83mWHG2CUGAovLD01OChndBLvs2FUJtGoqLmdyelABf7MTxzCo8Zou6GcqlwdB04t89/1O/w1cDnyilFU="
    )
)
handler = WebhookHandler(
    os.getenv("LINE_CHANNEL_SECRET", "02dba6286426db7cf24ca10b1cd09ed4")
)


# ── 資料庫依賴項 ────────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── 輔助函式 ────────────────────────────────────────────────────────────────────

def send_result_flex(
    reply_token: str,
    concentration: float,
    status_color: str,
    patient_name: str = "使用者",
):
    """
    推播 PINP 骨骼健康報告 Flex Message 給使用者。
    Flex Message JSON 由 templates.build_result_flex() 動態組裝。
    """
    flex_dict = templates.build_result_flex(
        concentration=concentration,
        status_color=status_color,
        patient_name=patient_name,
        liff_trends_url=TRENDS_LIFF_URL,
    )
    flex_message = FlexMessage(
        alt_text=f"您的骨骼健康報告｜PINP {concentration:.1f} ng/mL",
        contents=FlexContainer.from_dict(flex_dict),
    )
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[flex_message],
            )
        )


# ── Onboarding 狀態（記憶體，伺服器重啟後清空；MVP 夠用）──────────────────────
# key: line_user_id, value: True 代表正在等待使用者輸入受檢者姓名
_pending_name: dict[str, bool] = {}


def _reply_text(reply_token: str, text: str) -> None:
    """快速回覆純文字訊息的輔助函式"""
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)],
            )
        )


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


@handler.add(FollowEvent)
def handle_follow(event):
    """
    使用者將 Bot 加為好友時觸發。
    建立 User 記錄並進入 Onboarding：詢問受檢者姓名。
    """
    db = SessionLocal()
    try:
        line_user_id = event.source.user_id
        user = db.query(models.User).filter(
            models.User.line_user_id == line_user_id
        ).first()
        if not user:
            user = models.User(line_user_id=line_user_id, display_name="")
            db.add(user)
            db.commit()
        # 標記為等待輸入姓名
        _pending_name[line_user_id] = True
        _reply_text(
            event.reply_token,
            "👋 您好！歡迎使用 Epoch PINP 骨骼健康監測系統。\n\n"
            "請先告訴我受檢者的姓名（例如：王大明 先生），"
            "之後的健康報告會顯示此姓名供醫師辨識。\n\n"
            "📝 請直接回覆姓名：",
        )
    except Exception as e:
        print(f"handle_follow 錯誤: {e}")
        db.rollback()
    finally:
        db.close()


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    """
    處理使用者傳送的文字訊息。
    - 若正在等待輸入姓名 → 儲存姓名，完成 Onboarding
    - 否則 → 顯示使用說明
    """
    db = SessionLocal()
    try:
        line_user_id = event.source.user_id
        text = event.message.text.strip()

        # ── 特殊指令：重新設定姓名 ──
        if text in ("重設姓名", "改名", "設定姓名"):
            _pending_name[line_user_id] = True
            _reply_text(event.reply_token, "📝 請輸入新的受檢者姓名：")
            return

        # ── Onboarding：等待使用者輸入姓名 ──
        if _pending_name.get(line_user_id):
            user = db.query(models.User).filter(
                models.User.line_user_id == line_user_id
            ).first()
            if not user:
                user = models.User(line_user_id=line_user_id, display_name="")
                db.add(user)
                db.flush()

            if not user.patients:
                patient = models.Patient(
                    name=text, age=0, medication="", caregiver_id=user.id
                )
                db.add(patient)
            else:
                # 更新既有病患姓名
                user.patients[0].name = text

            db.commit()
            _pending_name.pop(line_user_id, None)
            _reply_text(
                event.reply_token,
                f"✅ 已設定受檢者姓名為「{text}」。\n\n"
                "接下來請傳送試紙照片，系統會自動分析並產生骨骼健康報告！",
            )
            return

        # ── 一般文字：顯示使用說明 ──
        user = db.query(models.User).filter(
            models.User.line_user_id == line_user_id
        ).first()
        name = (
            user.patients[0].name
            if user and user.patients
            else "尚未設定"
        )
        _reply_text(
            event.reply_token,
            f"🦴 Epoch PINP 骨骼健康監測系統\n"
            f"目前受檢者：{name}\n\n"
            "📷 傳送試紙照片 → 自動 AI 判讀並產生報告\n"
            "📊 查看歷史趨勢 → 點選選單「歷史數據」\n"
            "✏️ 修改受檢者姓名 → 輸入「重設姓名」",
        )
    except Exception as e:
        print(f"handle_text 錯誤: {e}")
        db.rollback()
    finally:
        db.close()


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    """當使用者傳送試紙照片時觸發"""
    db = SessionLocal()
    try:
        line_user_id = event.source.user_id

        # 0. 確認 Onboarding 已完成（有受檢者姓名）
        user = db.query(models.User).filter(
            models.User.line_user_id == line_user_id
        ).first()
        if not user or not user.patients or user.patients[0].name in ("", "預設病患"):
            _pending_name[line_user_id] = True
            _reply_text(
                event.reply_token,
                "📝 請先設定受檢者姓名再進行檢測。\n直接回覆姓名即可（例如：王大明 先生）：",
            )
            return

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

        # 2. AI 影像分析：識別 T/C 線並計算濃度 (R^2 = 0.99)
        result = processor.analyze_pinp_strip(event.message.id, image_bytes)

        # 3. 判斷狀態顏色：綠 (生長)、黃 (穩定)、紅 (需警示)
        status_color = processor.determine_status(result["concentration"])

        # 4. (Onboarding 保證 user 與 patient 已存在，直接使用)

        # 5. 儲存檢測紀錄
        new_record = models.DetectionRecord(
            patient_id=user.patients[0].id,
            concentration=result["concentration"],
            gray_value=result["gray_value"],
            status_color=status_color,
            image_path=image_path,
            detected_at=datetime.datetime.utcnow(),
        )
        db.add(new_record)
        db.commit()

        # 6. 推播 Flex Message 結果卡片
        patient_name = user.patients[0].name if user.patients else "使用者"
        send_result_flex(
            event.reply_token,
            result["concentration"],
            status_color,
            patient_name=patient_name,
        )

    except Exception as e:
        print(f"處理影像錯誤: {e}")
        db.rollback()
    finally:
        db.close()


# ── REST API (供 LIFF 趨勢圖表使用) ────────────────────────────────────────────

@app.get("/api/history/{line_user_id}")
async def get_history(line_user_id: str, db: Session = Depends(get_db)):
    """回傳使用者最近 10 筆 PINP 檢測記錄"""
    user = db.query(models.User).filter(
        models.User.line_user_id == line_user_id
    ).first()
    if not user or not user.patients:
        return []

    records = (
        db.query(models.DetectionRecord)
        .filter(models.DetectionRecord.patient_id == user.patients[0].id)
        .order_by(models.DetectionRecord.detected_at.asc())
        .limit(10)
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

