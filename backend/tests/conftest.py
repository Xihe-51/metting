"""
S12 / S6 / S7 修复验证 —— 公共测试夹具与业务辅助函数

设计要点：
1. 每个用例使用独立的临时 SQLite 库，通过 dependency_overrides 注入，
   绝不污染开发库 meeting.db。
2. 登录接口 _authenticate 内部直接使用 SessionLocal（不走依赖注入），
   因此测试里改为：直接建用户 + 用 create_access_token 自行签发 JWT。
3. ConnectionManager 是模块级单例，用例前后必须清空，避免用例之间串场。
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

# app.config 在导入阶段就会校验 SECRET_KEY（配置不合格直接拒绝启动），
# 因此必须在导入任何 app 模块之前给测试环境准备一个足够强的密钥。
# setdefault 而非直接赋值：便于本地用真实 .env 跑测试时不被覆盖。
os.environ.setdefault("SECRET_KEY", "pytest-only-secret-key-" + "0123456789" * 3)

from app import rate_limit  # noqa: E402
from app.database import Base, create_db_engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Meeting, Participant, User  # noqa: E402
from app.routers import meeting_router  # noqa: E402
from app.routers.auth_router import create_access_token, get_password_hash  # noqa: E402
from app.websocket_manager import manager  # noqa: E402

DEFAULT_PASSWORD = "Passw0rd!123"[:72]


def _reset_manager():
    """清空连接管理器的三个池、限流计数与录制分片缓冲，避免用例之间串场"""
    manager.active_connections.clear()
    manager.waiting_connections.clear()
    manager.participants_info.clear()
    rate_limit.reset()
    # 录制分片组装器是模块级字典，且用 recording_id 作键；
    # 每个用例的临时库都从 id=1 重新计数，不清理会串到下一个用例
    meeting_router._chunk_state.clear()


@pytest.fixture()
def session_factory(tmp_path):
    """临时库会话工厂（同时供直连断言使用）

    复用 app.database.create_db_engine：测试库与应用库使用同一套
    WAL / busy_timeout 配置，保证并发相关用例的结论对生产配置有效。
    """
    engine = create_db_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture()
def client(session_factory):
    """覆盖 get_db 依赖的测试客户端"""

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    _reset_manager()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    _reset_manager()


# ---------------- 业务辅助 ----------------

def make_user(session_factory, username, password=DEFAULT_PASSWORD):
    """直接建用户并签发 JWT，返回 (user_id, token, headers)"""
    db = session_factory()
    try:
        user = User(
            username=username,
            password_hash=get_password_hash(password),
            display_name=username,
            email=f"{username}@test.local",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        token = create_access_token({"user_id": user.id, "username": user.username})
        return user.id, token, {"Authorization": f"Bearer {token}"}
    finally:
        db.close()


def create_meeting(client, headers, title="测试会议", password="", waiting_room=False, whitelist=False):
    """创建会议，返回 data 字典（含 meeting_no / status）"""
    resp = client.post("/api/v1/meetings", json={
        "title": title,
        "password": password,
        "scheduled_at": None,
        "waiting_room": waiting_room,
        "whitelist": whitelist,
    }, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def join_meeting(client, headers, meeting_no, password=""):
    """加入会议，返回 data 字典"""
    resp = client.post(f"/api/v1/meetings/{meeting_no}/join",
                       json={"password": password}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def get_participant(session_factory, meeting_no, username):
    """按会议号 + 用户名取 participant 行"""
    db = session_factory()
    try:
        return db.query(Participant).join(Meeting, Participant.meeting_id == Meeting.id).filter(
            Meeting.meeting_no == meeting_no,
            Participant.display_name == username,
        ).order_by(Participant.id.desc()).first()
    finally:
        db.close()


def get_meeting_row(session_factory, meeting_no):
    db = session_factory()
    try:
        return db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    finally:
        db.close()


def participant_id_of(session_factory, meeting_no, username):
    row = get_participant(session_factory, meeting_no, username)
    assert row is not None, f"{username} 不在会议 {meeting_no} 中"
    return row.id