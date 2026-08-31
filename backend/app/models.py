"""
数据库模型
User, VerificationCode, Meeting, Participant, Recording, MeetingWhitelist, AuditLog, Reaction
"""
from sqlalchemy import Column, Integer, String, DateTime, Boolean, func, ForeignKey, Text
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
    avatar_url = Column(String(256), nullable=True)   # 头像
    cover_url = Column(String(256), nullable=True)    # 封面图
    bio = Column(String(256), nullable=True)          # 个性签名
    verified = Column(Boolean, default=False)         # 认证标识
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
    status = Column(String(16), default="ongoing")  # ongoing / ended / scheduled
    password_hash = Column(String(128), nullable=True)  # bcrypt 会议密码
    scheduled_at = Column(DateTime, nullable=True)  # 预约时间
    waiting_room_enabled = Column(Boolean, default=False)  # 是否开启等候室
    whitelist_enabled = Column(Boolean, default=False)  # 是否开启参会白名单
    recording_permission = Column(String(16), default="host_only")  # host_only / all
    participant_count = Column(Integer, default=1)
    created_at = Column(DateTime, server_default=func.now())
    ended_at = Column(DateTime, nullable=True)
    announcement = Column(Text, nullable=True)  # 房间公告/议程
    locked = Column(Boolean, default=False)  # 是否锁定（禁止新成员加入）
    host_id = Column(Integer, nullable=True)  # 当前主持人 user_id
    co_host_id = Column(Integer, nullable=True)  # 联席主持人 user_id

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
    status = Column(String(16), default="joined")  # joined / waiting / left / kicked
    audio_on = Column(Boolean, default=True)
    video_on = Column(Boolean, default=True)
    sharing_screen = Column(Boolean, default=False)
    muted = Column(Boolean, default=False)  # 被主持人禁言（禁麦）
    chat_muted = Column(Boolean, default=False)  # 被禁止聊天/私聊
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


class MeetingWhitelist(Base):
    """参会白名单"""
    __tablename__ = "meeting_whitelist"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    meeting = relationship("Meeting")
    user = relationship("User")


class AuditLog(Base):
    """审计日志"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(32), nullable=False)  # create, join, leave, kick, mute_all, admit, reject, record_start, record_stop, end
    details = Column(Text, nullable=True)  # JSON 格式的额外信息
    created_at = Column(DateTime, server_default=func.now())

    meeting = relationship("Meeting")
    user = relationship("User")


class Reaction(Base):
    """表情反应记录"""
    __tablename__ = "reactions"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True)
    participant_id = Column(Integer, ForeignKey("participants.id"), nullable=False)
    emoji = Column(String(8), nullable=False)  # 表情符号
    created_at = Column(DateTime, server_default=func.now())

    meeting = relationship("Meeting")
    participant = relationship("Participant")


class ChatMessage(Base):
    """聊天消息记录（群聊 + 私聊）"""
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True)
    participant_id = Column(Integer, ForeignKey("participants.id"), nullable=False)  # 发送者
    display_name = Column(String(64), nullable=False)
    content = Column(Text, nullable=False)
    is_whisper = Column(Boolean, default=False)
    target_id = Column(Integer, nullable=True)  # 私聊对象 participant_id（群聊为 None）
    recalled = Column(Boolean, default=False)  # 是否已撤回
    created_at = Column(DateTime, server_default=func.now())

    meeting = relationship("Meeting")