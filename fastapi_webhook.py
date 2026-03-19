import datetime
import csv
import io
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Literal

from fastapi import FastAPI, Request, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage, FlexMessage, FlexContainer
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
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
ADMIN_TOKEN_ISSUER_KEY = os.getenv("ADMIN_TOKEN_ISSUER_KEY", "")
SESSION_EXPIRE_DAYS = int(os.getenv("SESSION_EXPIRE_DAYS", "30"))
TOKEN_DEFAULT_SHOTS = 10
USE_LINE_WEBHOOK = os.getenv("USE_LINE_WEBHOOK", "true").strip().lower() == "true"

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

class LineAuthRequest(BaseModel):
    line_user_id: str
    display_name: Optional[str] = ""


class GoogleAuthRequest(BaseModel):
    id_token: str
    line_user_id: Optional[str] = None


class RedeemTokenRequest(BaseModel):
    session_token: str
    token_code: str


class GenerateTokensRequest(BaseModel):
    admin_key: str
    count: int = Field(default=1, ge=1, le=300)
    prefix: str = Field(default="PINP")
    shots_granted: int = Field(default=TOKEN_DEFAULT_SHOTS, ge=1, le=100)


class AdminUpdateUserQuotaRequest(BaseModel):
    admin_key: str
    user_id: int = Field(ge=1)
    mode: Literal["set", "add"] = "set"
    value: int = 0


def _ensure_primary_patient(db: Session, user: models.User, fallback_name: str = "使用者") -> models.Patient:
    if user.patients:
        patient = user.patients[0]
        if not patient.name:
            patient.name = fallback_name
        return patient

    patient = models.Patient(
        name=fallback_name,
        age=0,
        medication="",
        caregiver_id=user.id,
    )
    db.add(patient)
    db.flush()
    return patient


def _ensure_quota_wallet(db: Session, user_id: int) -> models.UserQuota:
    wallet = db.query(models.UserQuota).filter(models.UserQuota.user_id == user_id).first()
    if wallet:
        return wallet

    wallet = models.UserQuota(user_id=user_id, remaining_shots=0)
    db.add(wallet)
    db.flush()
    return wallet


def _create_auth_session(db: Session, user_id: int, provider: str) -> models.AuthSession:
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=SESSION_EXPIRE_DAYS)
    session = models.AuthSession(
        user_id=user_id,
        provider=provider,
        token=secrets.token_urlsafe(32),
        expires_at=expires_at,
    )
    db.add(session)
    db.flush()
    return session


def _get_session(db: Session, session_token: str) -> models.AuthSession:
    session = db.query(models.AuthSession).filter(models.AuthSession.token == session_token).first()
    if not session:
        raise HTTPException(status_code=401, detail="登入憑證無效，請重新登入")
    if session.expires_at < datetime.datetime.utcnow():
        raise HTTPException(status_code=401, detail="登入已過期，請重新登入")
    return session


def _verify_google_id_token(id_token: str) -> dict:
    query = urllib.parse.urlencode({"id_token": id_token})
    url = f"https://oauth2.googleapis.com/tokeninfo?{query}"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=401, detail="Google Token 驗證失敗") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=503, detail="目前無法連線 Google 驗證服務") from exc

    issuer = payload.get("iss")
    if issuer not in ("accounts.google.com", "https://accounts.google.com"):
        raise HTTPException(status_code=401, detail="Google Token 發行者不正確")

    if GOOGLE_CLIENT_ID and payload.get("aud") != GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=401, detail="Google Client ID 不匹配")

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Google Token 內容不完整")
    return payload


def _normalize_token_code(token_code: str) -> str:
    cleaned = token_code.strip().upper().replace(" ", "")
    return cleaned


def _build_new_token_code(prefix: str) -> str:
    p = "".join(ch for ch in prefix.upper() if ch.isalnum()) or "PINP"
    left = secrets.token_hex(2).upper()
    right = secrets.token_hex(2).upper()
    return f"{p}-{left}-{right}"


def _assert_admin_key(admin_key: str) -> None:
    if not ADMIN_TOKEN_ISSUER_KEY or admin_key != ADMIN_TOKEN_ISSUER_KEY:
        raise HTTPException(status_code=403, detail="admin_key 無效")


def _csv_response(filename: str, rows: list[list]) -> Response:
    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerows(rows)
    content = "\ufeff" + sio.getvalue()  # Excel 相容 BOM
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

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


def push_result_flex(
    line_user_id: str,
    concentration: float,
    status_color: str,
    patient_name: str = "使用者",
):
    """
    主動推播 Flex Message 給指定 LINE 使用者（用於 LIFF 上傳後回報結果）。
    使用 Push Message API（不需要 reply_token）。
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
        MessagingApi(api_client).push_message(
            PushMessageRequest(
                to=line_user_id,
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


@app.get("/api/config/public")
async def get_public_config():
    """提供前端所需公開設定。"""
    return {
        "google_client_id": GOOGLE_CLIENT_ID,
    }


@app.post("/api/auth/line")
async def auth_line(payload: LineAuthRequest, db: Session = Depends(get_db)):
    """
    使用 LINE userId 建立 / 取得帳號，回傳 session token。
    LIFF 進入時可直接完成登入綁定。
    """
    line_user_id = payload.line_user_id.strip()
    if not line_user_id:
        raise HTTPException(status_code=400, detail="line_user_id 不可為空")

    user = db.query(models.User).filter(models.User.line_user_id == line_user_id).first()
    if not user:
        user = models.User(
            line_user_id=line_user_id,
            display_name=(payload.display_name or ""),
        )
        db.add(user)
        db.flush()
    elif payload.display_name:
        user.display_name = payload.display_name

    identity = (
        db.query(models.ExternalIdentity)
        .filter(
            models.ExternalIdentity.provider == "line",
            models.ExternalIdentity.provider_user_id == line_user_id,
        )
        .first()
    )
    if not identity:
        db.add(
            models.ExternalIdentity(
                user_id=user.id,
                provider="line",
                provider_user_id=line_user_id,
            )
        )

    patient = _ensure_primary_patient(db, user, fallback_name=user.display_name or "使用者")
    wallet = _ensure_quota_wallet(db, user.id)
    session = _create_auth_session(db, user.id, provider="line")
    db.commit()

    return {
        "session_token": session.token,
        "provider": "line",
        "display_name": user.display_name,
        "patient_name": patient.name,
        "remaining_shots": wallet.remaining_shots,
    }


@app.post("/api/auth/google")
async def auth_google(payload: GoogleAuthRequest, db: Session = Depends(get_db)):
    """
    使用 Google ID Token 登入。
    若帶入 line_user_id，會嘗試綁定到同一位 User。
    """
    token_claims = _verify_google_id_token(payload.id_token)
    google_sub = token_claims.get("sub")
    email = token_claims.get("email")
    name = token_claims.get("name") or email or "Google 使用者"

    identity = (
        db.query(models.ExternalIdentity)
        .filter(
            models.ExternalIdentity.provider == "google",
            models.ExternalIdentity.provider_user_id == google_sub,
        )
        .first()
    )

    if identity:
        user = db.query(models.User).filter(models.User.id == identity.user_id).first()
        if not user:
            user = models.User(line_user_id=payload.line_user_id, display_name=name)
            db.add(user)
            db.flush()
            identity.user_id = user.id
    else:
        user = None
        if payload.line_user_id:
            user = db.query(models.User).filter(
                models.User.line_user_id == payload.line_user_id
            ).first()

        if not user:
            user = models.User(line_user_id=payload.line_user_id, display_name=name)
            db.add(user)
            db.flush()
        elif not user.display_name:
            user.display_name = name

        db.add(
            models.ExternalIdentity(
                user_id=user.id,
                provider="google",
                provider_user_id=google_sub,
                email=email,
            )
        )

    patient = _ensure_primary_patient(db, user, fallback_name=user.display_name or "使用者")
    wallet = _ensure_quota_wallet(db, user.id)
    session = _create_auth_session(db, user.id, provider="google")
    db.commit()

    return {
        "session_token": session.token,
        "provider": "google",
        "display_name": user.display_name,
        "patient_name": patient.name,
        "remaining_shots": wallet.remaining_shots,
    }


@app.get("/api/quota/status")
async def quota_status(session_token: str, db: Session = Depends(get_db)):
    session = _get_session(db, session_token)
    user = db.query(models.User).filter(models.User.id == session.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="找不到使用者")

    patient = _ensure_primary_patient(db, user, fallback_name=user.display_name or "使用者")
    wallet = _ensure_quota_wallet(db, user.id)
    db.commit()

    return {
        "provider": session.provider,
        "display_name": user.display_name,
        "patient_name": patient.name,
        "remaining_shots": wallet.remaining_shots,
    }


@app.post("/api/quota/redeem")
async def redeem_quota_token(payload: RedeemTokenRequest, db: Session = Depends(get_db)):
    session = _get_session(db, payload.session_token)
    user = db.query(models.User).filter(models.User.id == session.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="找不到使用者")

    code = _normalize_token_code(payload.token_code)
    if len(code) < 6:
        raise HTTPException(status_code=400, detail="Token 格式不正確")

    token = db.query(models.QuotaToken).filter(models.QuotaToken.code == code).first()
    if not token:
        raise HTTPException(status_code=404, detail="查無此 Token，請確認輸入")

    if token.redeemed_by_user_id:
        if token.redeemed_by_user_id == user.id:
            raise HTTPException(status_code=409, detail="此 Token 已被您兌換過")
        raise HTTPException(status_code=409, detail="此 Token 已被其他帳號使用")

    wallet = _ensure_quota_wallet(db, user.id)
    wallet.remaining_shots += token.shots_granted
    token.redeemed_by_user_id = user.id
    token.redeemed_at = datetime.datetime.utcnow()
    db.commit()

    return {
        "ok": True,
        "token_code": token.code,
        "granted_shots": token.shots_granted,
        "remaining_shots": wallet.remaining_shots,
    }


@app.post("/api/admin/quota/generate")
async def generate_quota_tokens(payload: GenerateTokensRequest, db: Session = Depends(get_db)):
    """
    產生可印在試紙包上的 token（可再轉為 QR code 內容）。
    需提供 ADMIN_TOKEN_ISSUER_KEY。
    """
    _assert_admin_key(payload.admin_key)

    codes = []
    for _ in range(payload.count):
        code = _build_new_token_code(payload.prefix)
        while db.query(models.QuotaToken).filter(models.QuotaToken.code == code).first():
            code = _build_new_token_code(payload.prefix)

        db.add(
            models.QuotaToken(
                code=code,
                shots_granted=payload.shots_granted,
            )
        )
        codes.append(code)

    db.commit()
    return {
        "count": len(codes),
        "shots_per_token": payload.shots_granted,
        "codes": codes,
    }


@app.get("/api/admin/overview")
async def admin_overview(admin_key: str, db: Session = Depends(get_db)):
    """管理頁總覽：token 發放與使用者額度概況。"""
    _assert_admin_key(admin_key)

    total_tokens = db.query(models.QuotaToken).count()
    redeemed_tokens = (
        db.query(models.QuotaToken)
        .filter(models.QuotaToken.redeemed_by_user_id.isnot(None))
        .count()
    )
    total_users = db.query(models.User).count()
    quota_wallets = db.query(models.UserQuota).all()
    users_with_quota = len([q for q in quota_wallets if q.remaining_shots > 0])
    total_remaining_shots = sum(q.remaining_shots for q in quota_wallets)

    return {
        "total_tokens": total_tokens,
        "redeemed_tokens": redeemed_tokens,
        "unredeemed_tokens": max(total_tokens - redeemed_tokens, 0),
        "total_users": total_users,
        "users_with_quota": users_with_quota,
        "total_remaining_shots": total_remaining_shots,
    }


@app.get("/api/admin/tokens")
async def admin_tokens(
    admin_key: str,
    status: str = "all",
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """
    管理頁 token 清單。
    status: all | redeemed | unredeemed
    """
    _assert_admin_key(admin_key)
    status = status.lower().strip()
    if status not in {"all", "redeemed", "unredeemed"}:
        raise HTTPException(status_code=400, detail="status 必須為 all / redeemed / unredeemed")

    q = db.query(models.QuotaToken)
    if status == "redeemed":
        q = q.filter(models.QuotaToken.redeemed_by_user_id.isnot(None))
    elif status == "unredeemed":
        q = q.filter(models.QuotaToken.redeemed_by_user_id.is_(None))

    tokens = (
        q.order_by(models.QuotaToken.created_at.desc())
        .offset(max(offset, 0))
        .limit(min(max(limit, 1), 500))
        .all()
    )

    items = []
    for token in tokens:
        redeemed_user = None
        patient_name = ""
        if token.redeemed_by_user_id:
            redeemed_user = db.query(models.User).filter(models.User.id == token.redeemed_by_user_id).first()
            if redeemed_user and redeemed_user.patients:
                patient_name = redeemed_user.patients[0].name or ""

        items.append(
            {
                "code": token.code,
                "shots_granted": token.shots_granted,
                "created_at": token.created_at.isoformat() if token.created_at else None,
                "redeemed": token.redeemed_by_user_id is not None,
                "redeemed_at": token.redeemed_at.isoformat() if token.redeemed_at else None,
                "redeemed_by_user_id": token.redeemed_by_user_id,
                "redeemed_by_display_name": redeemed_user.display_name if redeemed_user else "",
                "redeemed_by_patient_name": patient_name,
                "redeemed_by_line_user_id": redeemed_user.line_user_id if redeemed_user else "",
            }
        )

    return {
        "status": status,
        "count": len(items),
        "offset": max(offset, 0),
        "limit": min(max(limit, 1), 500),
        "items": items,
    }


@app.get("/api/admin/users")
async def admin_users(
    admin_key: str,
    keyword: str = "",
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """管理頁使用者清單：剩餘拍攝次數、病患姓名、綁定 provider。"""
    _assert_admin_key(admin_key)

    users = (
        db.query(models.User)
        .order_by(models.User.created_at.desc())
        .offset(max(offset, 0))
        .limit(min(max(limit, 1), 500))
        .all()
    )

    kw = keyword.strip().lower()
    items = []
    for user in users:
        patient_name = user.patients[0].name if user.patients else ""
        quota = user.quota.remaining_shots if user.quota else 0
        providers = [identity.provider for identity in user.identities]

        search_target = " ".join(
            [
                user.display_name or "",
                user.line_user_id or "",
                patient_name or "",
            ]
        ).lower()
        if kw and kw not in search_target:
            continue

        items.append(
            {
                "user_id": user.id,
                "display_name": user.display_name,
                "line_user_id": user.line_user_id,
                "patient_name": patient_name,
                "remaining_shots": quota,
                "providers": providers,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            }
        )

    return {
        "count": len(items),
        "offset": max(offset, 0),
        "limit": min(max(limit, 1), 500),
        "items": items,
    }


@app.post("/api/admin/users/quota/update")
async def admin_update_user_quota(payload: AdminUpdateUserQuotaRequest, db: Session = Depends(get_db)):
    """管理端手動調整使用者剩餘拍攝次數。"""
    _assert_admin_key(payload.admin_key)

    user = db.query(models.User).filter(models.User.id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="找不到使用者")

    wallet = _ensure_quota_wallet(db, user.id)
    before = wallet.remaining_shots

    if payload.mode == "set":
        wallet.remaining_shots = max(int(payload.value), 0)
    else:  # add
        wallet.remaining_shots = max(before + int(payload.value), 0)

    db.commit()

    return {
        "ok": True,
        "user_id": user.id,
        "display_name": user.display_name,
        "patient_name": user.patients[0].name if user.patients else "",
        "before": before,
        "after": wallet.remaining_shots,
        "mode": payload.mode,
        "value": payload.value,
    }


@app.get("/api/admin/tokens/export.csv")
async def admin_tokens_csv(
    admin_key: str,
    status: str = "all",
    limit: int = 2000,
    db: Session = Depends(get_db),
):
    """匯出 token 清單 CSV。"""
    _assert_admin_key(admin_key)
    status = status.lower().strip()
    if status not in {"all", "redeemed", "unredeemed"}:
        raise HTTPException(status_code=400, detail="status 必須為 all / redeemed / unredeemed")

    q = db.query(models.QuotaToken)
    if status == "redeemed":
        q = q.filter(models.QuotaToken.redeemed_by_user_id.isnot(None))
    elif status == "unredeemed":
        q = q.filter(models.QuotaToken.redeemed_by_user_id.is_(None))

    tokens = (
        q.order_by(models.QuotaToken.created_at.desc())
        .limit(min(max(limit, 1), 5000))
        .all()
    )

    rows = [[
        "token_code",
        "shots_granted",
        "redeemed",
        "created_at",
        "redeemed_at",
        "redeemed_by_user_id",
        "redeemed_by_display_name",
        "redeemed_by_patient_name",
        "redeemed_by_line_user_id",
    ]]

    for token in tokens:
        redeemed_user = None
        patient_name = ""
        if token.redeemed_by_user_id:
            redeemed_user = db.query(models.User).filter(models.User.id == token.redeemed_by_user_id).first()
            if redeemed_user and redeemed_user.patients:
                patient_name = redeemed_user.patients[0].name or ""

        rows.append([
            token.code,
            token.shots_granted,
            "yes" if token.redeemed_by_user_id else "no",
            token.created_at.isoformat() if token.created_at else "",
            token.redeemed_at.isoformat() if token.redeemed_at else "",
            token.redeemed_by_user_id or "",
            redeemed_user.display_name if redeemed_user else "",
            patient_name,
            redeemed_user.line_user_id if redeemed_user else "",
        ])

    return _csv_response("tokens_export.csv", rows)


@app.get("/api/admin/users/export.csv")
async def admin_users_csv(
    admin_key: str,
    keyword: str = "",
    limit: int = 2000,
    db: Session = Depends(get_db),
):
    """匯出使用者額度清單 CSV。"""
    _assert_admin_key(admin_key)

    users = (
        db.query(models.User)
        .order_by(models.User.created_at.desc())
        .limit(min(max(limit, 1), 5000))
        .all()
    )

    kw = keyword.strip().lower()
    rows = [[
        "user_id",
        "display_name",
        "patient_name",
        "line_user_id",
        "remaining_shots",
        "providers",
        "created_at",
    ]]

    for user in users:
        patient_name = user.patients[0].name if user.patients else ""
        quota = user.quota.remaining_shots if user.quota else 0
        providers = ",".join(identity.provider for identity in user.identities)

        search_target = " ".join(
            [
                user.display_name or "",
                user.line_user_id or "",
                patient_name or "",
            ]
        ).lower()
        if kw and kw not in search_target:
            continue

        rows.append([
            user.id,
            user.display_name or "",
            patient_name,
            user.line_user_id or "",
            quota,
            providers,
            user.created_at.isoformat() if user.created_at else "",
        ])

    return _csv_response("users_export.csv", rows)


# ── LINE Webhook ────────────────────────────────────────────────────────────────

@app.post("/callback")
async def callback(request: Request):
    if not USE_LINE_WEBHOOK:
        return {
            "status": "ok",
            "mode": "line-biz",
            "message": "Webhook handling is disabled by USE_LINE_WEBHOOK=false",
        }

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

        identity = (
            db.query(models.ExternalIdentity)
            .filter(
                models.ExternalIdentity.provider == "line",
                models.ExternalIdentity.provider_user_id == line_user_id,
            )
            .first()
        )
        if not identity:
            db.flush()
            db.add(
                models.ExternalIdentity(
                    user_id=user.id,
                    provider="line",
                    provider_user_id=line_user_id,
                )
            )

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

        # ── 一般文字：不做預設回覆 ──
        # 目的：保留 LINE Biz 後台設定的關鍵字回覆，不由 webhook 蓋掉。
        # 仍保留 webhook 指令：重設姓名 / 改名 / 設定姓名，以及 onboarding 姓名輸入流程。
        return
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

_STATUS_DESCRIPTIONS = {
    "green":  "骨鬆藥物反應良好，骨骼正在積極生長。請維持目前的用藥習慣。",
    "yellow": "骨骼狀態穩定，建議繼續追蹤觀察並定期檢測。",
    "red":    "骨骼生長指數偏低，建議儘快諮詢骨科醫師評估治療方案。",
}


@app.post("/api/upload")
async def upload_image(
    file: UploadFile = File(...),
    session_token: str = Form(default=None),
    line_user_id: str = Form(default=None),
    device_info: str = Form(default=None),
    db: Session = Depends(get_db),
):
    """
    LIFF 直接上傳試紙照片的端點。
    1. AI 影像判讀（白平衡 → C 線驗證 → T/C 比值換算）
    2. 驗證帳號與拍攝額度（session token）
    3. 儲存檢測紀錄至資料庫
    3. 透過 LINE Push API 主動推播 Flex Message 給使用者
    4. 回傳 JSON 供 LIFF 頁面即時顯示結果
    """
    user = None
    wallet = None

    if session_token:
        auth_session = _get_session(db, session_token)
        user = db.query(models.User).filter(models.User.id == auth_session.user_id).first()
    elif line_user_id:
        user = db.query(models.User).filter(models.User.line_user_id == line_user_id).first()

    if not user:
        raise HTTPException(status_code=401, detail="請先登入 LINE 或 Google 帳號")

    patient = _ensure_primary_patient(db, user, fallback_name=user.display_name or "使用者")

    # session token 登入模式：強制檢查拍攝額度
    if session_token:
        wallet = _ensure_quota_wallet(db, user.id)
        if wallet.remaining_shots <= 0:
            return JSONResponse(
                status_code=403,
                content={
                    "message": "拍攝額度不足，請先輸入 token 或掃描 QR code 兌換 10 次拍攝資格。",
                    "remaining_shots": 0,
                },
            )
        wallet.remaining_shots -= 1

    image_bytes = await file.read()

    # 儲存影像
    os.makedirs("images", exist_ok=True)
    image_path = f"images/liff_{os.urandom(8).hex()}.jpg"
    with open(image_path, "wb") as f:
        f.write(image_bytes)

    # AI 分析
    result = processor.analyze_pinp_strip(image_path, image_bytes)

    if not result.get("c_valid", True):
        db.commit()
        return JSONResponse(
            status_code=422,
            content={
                "message": "C 線無效，請確認血清量是否足夠，並重新操作後再拍攝。",
                "remaining_shots": wallet.remaining_shots if wallet else None,
            },
        )

    status_color = processor.determine_status(result["concentration"])

    record = models.DetectionRecord(
        patient_id=patient.id,
        concentration=result["concentration"],
        gray_value=result["gray_value"],
        status_color=status_color,
        image_path=image_path,
        device_info=device_info,
        detected_at=datetime.datetime.utcnow(),
    )
    db.add(record)
    db.commit()

    # 若使用者有綁定 LINE，推播 Flex Message 到聊天室
    if user.line_user_id:
        try:
            push_result_flex(
                user.line_user_id,
                result["concentration"],
                status_color,
                patient_name=patient.name or "使用者",
            )
        except Exception as push_err:
            print(f"[WARN] push_result_flex 失敗: {push_err}")

    return {
        "concentration": result["concentration"],
        "status_color": status_color,
        "description": _STATUS_DESCRIPTIONS.get(status_color, ""),
        "remaining_shots": wallet.remaining_shots if wallet else None,
    }

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

