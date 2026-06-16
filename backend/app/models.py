"""
数据库模型
User, VerificationCode, Meeting, Participant, Recording 五张表
"""
from sqlalchemy import Column, Integer, String, DateTime, Boolean, func, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(32), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    display_name = Column(String(64), nullable=False)
    email = Column(String(64), unique=True, nullable=False, index=True)
    phone = Column(String(16), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class VerificationCode(Base):
    """验证码表"""
    __tablename__ = "verification_codes"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(64), nullable=False, index=True)
    code = Column(String(6), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class Meeting(Base):
    """会议表"""
    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True, index=True)
    meeting_no = Column(String(6), unique=True, nullable=False, index=True)
    title = Column(String(128), default="")
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(16), default="ongoing")
    participant_count = Column(Integer, default=1)
    created_at = Column(DateTime, server_default=func.now())
    ended_at = Column(DateTime, nullable=True)

    creator = relationship("User")
    participants = relationship("Participant", back_populates="meeting", cascade="all, delete-orphan")
    recordings = relationship("Recording", back_populates="meeting", cascade="all, delete-orphan")


class Participant(Base):
    """参会人员表"""
    __tablename__ = "participants"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    display_name = Column(String(64), nullable=False)
    audio_on = Column(Boolean, default=True)
    video_on = Column(Boolean, default=True)
    sharing_screen = Column(Boolean, default=False)
    joined_at = Column(DateTime, server_default=func.now())
    left_at = Column(DateTime, nullable=True)

    meeting = relationship("Meeting", back_populates="participants")
    user = relationship("User")


class Recording(Base):
    """录制记录表"""
    __tablename__ = "recordings"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, default=0)
    duration = Column(Integer, default=0)
    status = Column(String(16), default="recording")
    started_at = Column(DateTime, server_default=func.now())
    ended_at = Column(DateTime, nullable=True)

    meeting = relationship("Meeting", back_populates="recordings")