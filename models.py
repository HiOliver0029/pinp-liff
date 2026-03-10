from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, Text
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