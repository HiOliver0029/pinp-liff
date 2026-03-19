from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from database import Base
import datetime

class User(Base):
    """
    照顧者模型 (主要為 40-55 歲子女)
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    line_user_id = Column(String, unique=True, index=True) # LINE 唯一的識別碼
    display_name = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # 一個照顧者可以管理多位長輩 (如父親、母親)
    patients = relationship("Patient", back_populates="caregiver")
    identities = relationship("ExternalIdentity", back_populates="user")
    sessions = relationship("AuthSession", back_populates="user")
    quota = relationship("UserQuota", back_populates="user", uselist=False)

class Patient(Base):
    """
    受照護長輩模型
    """
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    age = Column(Integer)
    medication = Column(String) # 目前使用的骨鬆藥物 (如 Prolia)
    caregiver_id = Column(Integer, ForeignKey("users.id"))

    caregiver = relationship("User", back_populates="patients")
    records = relationship("DetectionRecord", back_populates="patient")

class DetectionRecord(Base):
    """
    PINP 定量檢測紀錄
    """
    __tablename__ = "detection_records"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    
    # AI 判讀數據
    concentration = Column(Float) # PINP 濃度 (ng/mL)
    gray_value = Column(Float)    # 原始影像灰階值
    
    # 視覺化狀態：綠 (生長中)、黃 (穩定)、紅 (需警示)
    status_color = Column(String) 
    
    image_path = Column(String)   # 原始試紙照片儲存路徑
    detected_at = Column(DateTime, default=datetime.datetime.utcnow)
    device_info = Column(String, nullable=True)  # 拍攝裝置資訊，供 AI 校準魯棒性分析

    # 醫師參考備註
    doctor_notes = Column(Text, nullable=True) 

    patient = relationship("Patient", back_populates="records")


class ExternalIdentity(Base):
    """
    外部登入身份綁定（LINE / Google）
    同一個使用者可綁定多個 provider。
    """

    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_provider_identity"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String, nullable=False, index=True)  # line / google
    provider_user_id = Column(String, nullable=False, index=True)
    email = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="identities")


class AuthSession(Base):
    """
    前端登入後使用的 session token。
    供 LIFF 網頁呼叫 API 時綁定使用者身份。
    """

    __tablename__ = "auth_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String, nullable=False)  # line / google
    token = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="sessions")


class UserQuota(Base):
    """
    使用者拍攝額度錢包。
    redeem token / qr code 後累加可拍攝次數。
    """

    __tablename__ = "user_quotas"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    remaining_shots = Column(Integer, default=0, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    user = relationship("User", back_populates="quota")


class QuotaToken(Base):
    """
    試紙包 token / QR code 對應資料。
    每個 token 只能兌換一次，預設給 10 次拍攝額度。
    """

    __tablename__ = "quota_tokens"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)
    shots_granted = Column(Integer, default=10, nullable=False)
    redeemed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    redeemed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    redeemed_by = relationship("User")