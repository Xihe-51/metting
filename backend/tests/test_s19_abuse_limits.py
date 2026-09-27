"""
中-2 / 中-3 / 中-6 —— 滥用面限制的修复验证与攻击复现

【修复前的问题】
1. 中-2 WebSocket 频率限制缺失：每条 WS 消息都会触发「1 份上行 → N 份下行」的
   广播/转发，单连接不限速时，攻击者用极少的上行带宽就能把整场会议打成洪泛。
2. 中-3 录制配额缺失：/recordings/{id}/chunk 接收原始二进制且没有任何大小上限，
   一个已登录的会议创建者即可用脚本把服务器磁盘写满。
3. 中-6 reload 重启留下「幽灵在线」：进程重启会丢掉 ConnectionManager 的内存连接，
   但 DB 里 participants.left_at IS NULL / meetings.participant_count > 0 还在，
   于是名单错乱、会议永不自动结束。

【修复要点】
- WS 按消息类型分档限流（app/routers/meeting_router.WS_RATE_LIMITS），
  超限丢弃并回 rate_limited 提示，连续超限 WS_MAX_VIOLATIONS 次即 1008 断开；
- 录制分片三层配额（单片 / 单场 / 目录总量），超限返回 413 且不落盘；
- run.py 默认关闭 reload；应用启动时 reconcile_ghost_participants() 对账清理；
- 认证接口限流（app/routers/auth_router.AUTH_LIMITS，阈值可经环境变量调整）：
  /login 单账号 + 单 IP、/register 单 IP、/send-code 单邮箱 + 单 IP、
  /reset-password 单邮箱 + 单 IP、/password 单账号，超限一律 429。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s19_abuse_limits.py -v
"""
import os
from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from conftest import create_meeting, make_user, participant_id_of  # noqa: E402
from app import config  # noqa: E402
from app.config import (  # noqa: E402
    DEFAULT_AUTH_CHANGE_PWD_LIMIT_PER_USER,
    DEFAULT_AUTH_LOGIN_LIMIT_PER_IP,
    DEFAULT_AUTH_LOGIN_LIMIT_PER_USER,
    DEFAULT_AUTH_REGISTER_LIMIT_PER_IP,
    DEFAULT_AUTH_RESET_LIMIT_PER_EMAIL,
    DEFAULT_AUTH_RESET_LIMIT_PER_IP,
    DEFAULT_AUTH_SEND_CODE_LIMIT_PER_EMAIL,
    DEFAULT_AUTH_SEND_CODE_LIMIT_PER_IP,
    DEFAULT_REC_MAX_CHUNK_BYTES,
    DEFAULT_REC_MAX_STORAGE_BYTES,
    DEFAULT_REC_MAX_TOTAL_BYTES,
    InsecureConfigError,
    load_auth_rate_limits,
    load_recording_quota,
)
from app.database import Base, create_db_engine, reconcile_ghost_participants  # noqa: E402
from app.models import Meeting, Participant, User  # noqa: E402
from app.routers import auth_router, meeting_router  # noqa: E402
from app.routers.meeting_router import (  # noqa: E402
    WS_MAX_VIOLATIONS,
    WS_RATE_LIMITS,
)


# ==================================================================
# 中-2：WebSocket 单连接限流
# ==================================================================

def _authed_ws(client, session_factory, username):
    """建会议（创建者即参会者）并返回 (meeting_no, participant_id, token)"""
    _, token, headers = make_user(session_factory, username)
    meeting_no = create_meeting(client, headers, title="WS 限流验证")["meeting_no"]
    pid = participant_id_of(session_factory, meeting_no, username)
    return meeting_no, pid, token


def _flood(ws, kind: str, count: int, payload: dict = None) -> int:
    """尽可能快地灌 count 条同类型消息，返回实际发出条数

    服务端判定洪泛后会关闭连接，之后的 send 可能失败，这里直接停止发送。
    """
    sent = 0
    for _ in range(count):
        try:
            ws.send_json({"type": kind, "payload": payload or {}})
            sent += 1
        except Exception:
            break
    return sent


def _drain_until_closed(ws, limit: int = 500):
    """读到连接被服务端关闭，返回收到的所有消息类型与断开码

    只有服务端显式发送 close 帧（限流强制断开路径）才会走到这里；
    正常退会路径只是 return，不发 close 帧，需用 _read_until。
    """
    types = []
    with pytest.raises(WebSocketDisconnect) as ei:
        for _ in range(limit):
            types.append(ws.receive_json()["type"])
    return types, ei.value.code


def _read_until(ws, wanted: str, limit: int = 40):
    """读到出现指定类型的消息为止（服务端不主动 close 的场景）"""
    types = []
    for _ in range(limit):
        types.append(ws.receive_json()["type"])
        if types[-1] == wanted:
            break
    return types


def test_ws_chat_flood_is_dropped_and_disconnected(client, session_factory):
    """攻击复现：单连接洪泛 chat，超出配额的消息被丢弃，持续超限后 1008 断开

    这是「广播放大」的最小复现：chat 每条都会广播给全会议，
    若放行 35 条，服务端就要向每个成员各推 35 条下行。
    """
    meeting_no, pid, token = _authed_ws(client, session_factory, "flood_s19a")
    allowed = WS_RATE_LIMITS["chat"][0]

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "participants_list"

        sent = _flood(ws, "chat", allowed + WS_MAX_VIOLATIONS + 5, {"content": "flood"})
        assert sent >= allowed, "至少要把配额内的消息发出去"

        types, code = _drain_until_closed(ws)

    # 配额内的 chat 正常回显；超出的被丢弃（否则这里会收到远多于 allowed 条）
    assert types.count("chat") <= allowed, f"洪泛未被丢弃：收到 {types.count('chat')} 条 chat 回显"
    assert "rate_limited" in types, "超限时必须明确回提示，而不是静默丢弃"
    assert code == 1008, "持续超限应判定为洪泛并断开连接"


def test_ws_normal_chat_frequency_is_not_limited(client, session_factory):
    """正向对照：正常频率的发言全部放行，限流不会误伤普通用户"""
    meeting_no, pid, token = _authed_ws(client, session_factory, "norm_s19b")

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "participants_list"

        for i in range(5):
            ws.send_json({"type": "chat", "payload": {"content": f"正常发言 {i}"}})

        # 主持人入会后还会收到 waiting_participants 等控制消息，这里只统计 chat
        types = []
        for _ in range(10):
            types.append(ws.receive_json()["type"])
            if types.count("chat") == 5:
                break

    assert types.count("chat") == 5, f"正常发言被误限流：{types}"
    assert "rate_limited" not in types


def test_ws_leave_meeting_bypasses_rate_limit(client, session_factory):
    """leave_meeting 不参与限流：被限流后仍能正常退会，不会被卡在会议里"""
    meeting_no, pid, token = _authed_ws(client, session_factory, "leave_s19c")
    allowed = WS_RATE_LIMITS["chat"][0]

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "participants_list"

        # 先把 chat 配额打满并制造若干次超限（未达到断开阈值）
        _flood(ws, "chat", allowed + 3, {"content": "flood"})
        ws.send_json({"type": "leave_meeting"})
        types = _read_until(ws, "user_left")

    assert "user_left" in types, "leave_meeting 必须始终被处理"


def test_ws_speaking_flood_is_dropped(client, session_factory):
    """speaking（语音激励）分档更严：1 秒窗口内超限即丢弃

    speaking 的广播对象是「除自己外的所有人」，且不上报数据库，
    是性价比最高的放大向量，必须单独有一档更紧的配额。
    """
    meeting_no, pid, token = _authed_ws(client, session_factory, "speaking_s19d")
    limit, _window = WS_RATE_LIMITS["speaking"]

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "participants_list"

        _flood(ws, "speaking", limit + WS_MAX_VIOLATIONS + 5, {"speaking": True})
        types, code = _drain_until_closed(ws)

    # speaking 对自己不回显，只有超限提示能证明限流生效
    assert "rate_limited" in types
    assert code == 1008


# ==================================================================
# 中-3：录制分片配额
# ==================================================================

QUOTA = {"max_chunk_bytes": 1024, "max_total_bytes": 4096, "max_storage_bytes": 8192}


@pytest.fixture()
def quota_env(tmp_path, monkeypatch):
    """把录制目录与配额都改到临时目录 + 极小阈值，便于快速触发各层限制"""
    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir()
    monkeypatch.setattr(meeting_router, "RECORDINGS_DIR", str(rec_dir))
    monkeypatch.setattr(meeting_router, "RECORDING_QUOTA", dict(QUOTA))
    return rec_dir


def _dir_bytes(rec_dir) -> int:
    return sum(p.stat().st_size for p in rec_dir.iterdir() if p.is_file())


def _start_recording(client, session_factory, name):
    _, _, headers = make_user(session_factory, name)
    meeting_no = create_meeting(client, headers, title="录制配额")["meeting_no"]
    resp = client.post(f"/api/v1/meetings/{meeting_no}/recordings/start", headers=headers)
    assert resp.status_code == 200, resp.text
    return meeting_no, resp.json()["data"]["recording_id"], resp.json()["data"]["file_name"], headers


def _post_chunk(client, meeting_no, rid, headers, data: bytes, seq: int):
    h = dict(headers)
    h["X-Chunk-Seq"] = str(seq)
    return client.post(
        f"/api/v1/meetings/{meeting_no}/recordings/{rid}/chunk",
        content=data, headers=h)


def test_oversized_chunk_is_rejected_and_not_written(client, session_factory, quota_env):
    """攻击复现：单个巨块被 413 拒绝，且磁盘上不产生任何写入"""
    meeting_no, rid, file_name, headers = _start_recording(client, session_factory, "quota_chunk_s19")

    resp = _post_chunk(client, meeting_no, rid, headers, b"X" * (QUOTA["max_chunk_bytes"] + 1), seq=0)

    assert resp.status_code == 413, resp.text
    assert not (quota_env / file_name).exists(), "被拒绝的分片不允许落盘"
    assert _dir_bytes(quota_env) == 0


def test_chunk_within_quota_still_accepted(client, session_factory, quota_env):
    """正向对照：配额内的分片正常落盘（防止限制写成一律拒绝）"""
    meeting_no, rid, file_name, headers = _start_recording(client, session_factory, "quota_ok_s19")

    resp = _post_chunk(client, meeting_no, rid, headers, b"X" * 512, seq=0)

    assert resp.status_code == 200, resp.text
    assert (quota_env / file_name).stat().st_size == 512


def test_meeting_total_quota_blocks_further_chunks(client, session_factory, quota_env):
    """攻击复现：灌满单场上限后继续上传被 413 拒绝，文件大小不超过上限"""
    meeting_no, rid, file_name, headers = _start_recording(client, session_factory, "quota_total_s19")
    size = QUOTA["max_chunk_bytes"]
    chunks = QUOTA["max_total_bytes"] // size

    for seq in range(chunks):
        assert _post_chunk(client, meeting_no, rid, headers, b"A" * size, seq=seq).status_code == 200

    overflow = _post_chunk(client, meeting_no, rid, headers, b"B" * size, seq=chunks)

    assert overflow.status_code == 413, overflow.text
    assert (quota_env / file_name).stat().st_size == QUOTA["max_total_bytes"], "超出上限的字节不得落盘"
    assert _dir_bytes(quota_env) <= QUOTA["max_storage_bytes"]


def test_global_storage_quota_blocks_new_recording(client, session_factory, quota_env):
    """攻击复现：单场上限可通过不断开新会议绕过，目录总量上限必须拦住"""
    # 模拟历史录像已占用大部分空间（未超过目录上限，但装不下新分片）
    (quota_env / "old_recording.webm").write_bytes(b"O" * 8000)
    before = _dir_bytes(quota_env)

    meeting_no, rid, file_name, headers = _start_recording(client, session_factory, "quota_disk_s19")
    resp = _post_chunk(client, meeting_no, rid, headers, b"X" * 1024, seq=0)

    assert resp.status_code == 413, resp.text
    assert _dir_bytes(quota_env) == before, "磁盘占用不得因为被拒绝的请求而增长"
    assert not (quota_env / file_name).exists()


def test_recording_quota_config_defaults_and_validation(monkeypatch):
    """配置校验：默认值可用；非法值与「阈值不逐级放宽」都直接报错"""
    monkeypatch.setattr(config, "load_env_file", lambda *a, **k: None)
    for name in ("REC_MAX_CHUNK_BYTES", "REC_MAX_TOTAL_BYTES", "REC_MAX_STORAGE_BYTES"):
        monkeypatch.delenv(name, raising=False)

    assert load_recording_quota() == {
        "max_chunk_bytes": DEFAULT_REC_MAX_CHUNK_BYTES,
        "max_total_bytes": DEFAULT_REC_MAX_TOTAL_BYTES,
        "max_storage_bytes": DEFAULT_REC_MAX_STORAGE_BYTES,
    }

    monkeypatch.setenv("REC_MAX_CHUNK_BYTES", "not-a-number")
    with pytest.raises(InsecureConfigError):
        load_recording_quota()

    # 单片上限高于单场上限：单场限制会先触发，单片限制形同虚设 → 必须拒绝这种配置
    monkeypatch.setenv("REC_MAX_CHUNK_BYTES", "1048576")
    monkeypatch.setenv("REC_MAX_TOTAL_BYTES", "1024")
    with pytest.raises(InsecureConfigError):
        load_recording_quota()


def test_frontend_stops_recording_on_quota_rejection():
    """前端契约：分片被 413 拒绝时必须停录并提示，而不是静默丢分片"""
    path = os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "front", "src", "views", "MeetingView.vue"))
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    assert "res.status === 413" in src
    assert "stopRecordingByQuota" in src


# ==================================================================
# 中-6：重启后「幽灵在线」对账
# ==================================================================

@pytest.fixture()
def ghost_engine(tmp_path):
    """独立临时库（直接建表，不走 app 依赖注入）"""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'ghost.db'}")
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


def _seed(engine):
    """造出重启前的残留状态：会议 ongoing + 在线人数 3，参会记录含各类状态

    返回 (ongoing_no, scheduled_no, participant_ids)
    """
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        users = []
        for i in range(4):
            u = User(username=f"ghost_u{i}", password_hash="x", display_name=f"成员{i}",
                     email=f"ghost{i}@test.local")
            db.add(u)
            users.append(u)
        db.commit()

        ongoing = Meeting(meeting_no="800001", title="重启前进行中", creator_id=users[0].id,
                          status="ongoing", participant_count=3)
        scheduled = Meeting(meeting_no="800002", title="重启前预约", creator_id=users[0].id,
                            status="scheduled", participant_count=1)
        db.add_all([ongoing, scheduled])
        db.commit()

        in_meeting = Participant(meeting_id=ongoing.id, user_id=users[0].id, display_name="成员0",
                                 status="joined", left_at=None)
        waiting = Participant(meeting_id=ongoing.id, user_id=users[1].id, display_name="成员1",
                              status="waiting", left_at=None)
        already_left = Participant(meeting_id=ongoing.id, user_id=users[2].id, display_name="成员2",
                                   status="left", left_at=datetime(2026, 1, 1, 10, 0, 0))
        kicked = Participant(meeting_id=ongoing.id, user_id=users[3].id, display_name="成员3",
                             status="kicked", left_at=None)
        db.add_all([in_meeting, waiting, already_left, kicked])
        db.commit()

        return ongoing.meeting_no, scheduled.meeting_no, {
            "joined": in_meeting.id, "waiting": waiting.id,
            "left": already_left.id, "kicked": kicked.id,
        }
    finally:
        db.close()


def _meeting_state(engine, meeting_no):
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        m = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
        return {"status": m.status, "participant_count": m.participant_count, "ended_at": m.ended_at}
    finally:
        db.close()


def _participant_state(engine, meeting_no, name):
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
        p = db.query(Participant).filter(
            Participant.meeting_id == meeting.id,
            Participant.display_name == name,
        ).first()
        return {"status": p.status, "left_at": p.left_at}
    finally:
        db.close()


def test_reconcile_clears_ghost_participants_and_ends_meeting(ghost_engine):
    """故障复现：重启后 DB 仍显示 3 人在线、会议 ongoing —— 对账必须纠正"""
    ongoing_no, _scheduled_no, _ids = _seed(ghost_engine)

    progress = reconcile_ghost_participants(ghost_engine)

    assert progress["participants_left"] == 2, "joined 与 waiting 都是重启后的幽灵成员"
    assert progress["meetings_ended"] == 1

    for name in ("成员0", "成员1"):
        p = _participant_state(ghost_engine, ongoing_no, name)
        assert p["status"] == "left" and p["left_at"] is not None, f"{name} 仍是幽灵在线状态"

    meeting = _meeting_state(ghost_engine, ongoing_no)
    assert meeting["participant_count"] == 0, "人数不归零则「归零自动结束」永远不触发"
    assert meeting["status"] == "ended"
    assert meeting["ended_at"] is not None


def test_reconcile_keeps_already_departed_and_scheduled_meetings(ghost_engine):
    """边界：已离会/被踢记录不被改写，预约会议不被误结束"""
    ongoing_no, scheduled_no, _ids = _seed(ghost_engine)

    reconcile_ghost_participants(ghost_engine)

    already_left = _participant_state(ghost_engine, ongoing_no, "成员2")
    assert already_left["status"] == "left"
    assert already_left["left_at"] == datetime(2026, 1, 1, 10, 0, 0), "原有的离会时间不得被覆盖"

    kicked = _participant_state(ghost_engine, ongoing_no, "成员3")
    assert kicked["status"] == "kicked", "被踢记录必须保持 kicked，否则可重连绕过踢人"

    scheduled = _meeting_state(ghost_engine, scheduled_no)
    assert scheduled["status"] == "scheduled"
    assert scheduled["participant_count"] == 0


def test_reconcile_is_idempotent(ghost_engine):
    """幂等：连续两次对账，第二次不再改动任何行（启动日志不会被刷屏）"""
    _seed(ghost_engine)

    first = reconcile_ghost_participants(ghost_engine)
    second = reconcile_ghost_participants(ghost_engine)

    assert first["participants_left"] == 2 and first["meetings_ended"] == 1
    assert second == {"participants_left": 0, "meetings_ended": 0}


def test_ws_force_close_finalizes_participant(client, session_factory):
    """洪泛被断开后与「正常掉线」走同一套收尾：DB 落库离会 + 限流键释放

    用强制断开路径验证收尾是刻意的：正常关闭（客户端发 disconnect）在 TestClient
    里发生在会话销毁的同一瞬间，收尾协程可能还没跑完就被拆掉，断言会不稳定。
    """
    from app import rate_limit

    meeting_no, pid, token = _authed_ws(client, session_factory, "finalize_s19")
    prefix = f"ws:{meeting_no}:{pid}:"

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "participants_list"
        # 先制造一个正常的限流桶，用于验证断开后被回收
        ws.send_json({"type": "ping"})
        assert "pong" in _read_until(ws, "pong")
        assert any(k.startswith(prefix) for k in rate_limit._buckets), "连接期间应存在限流桶"

        _flood(ws, "chat", WS_RATE_LIMITS["chat"][0] + WS_MAX_VIOLATIONS + 5, {"content": "flood"})
        _drain_until_closed(ws)

    db = session_factory()
    try:
        p = db.query(Participant).filter(Participant.id == pid).first()
        assert p.left_at is not None, "被强制断开的连接也必须落库离会"
        assert p.status == "left"
    finally:
        db.close()

    assert not any(k.startswith(prefix) for k in rate_limit._buckets), "断开后限流键必须被回收"


def test_run_py_reload_defaults_to_off():
    """中-6 契约：run.py 默认不开 reload，需显式 DEV_RELOAD=1 才启用"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    assert "reload=reload_enabled" in src
    assert "reload=True" not in src, "默认开启 reload 会把每次改代码变成一次「全员掉线」"


# ==================================================================
# 认证接口限流（撞库 / 批量注册 / 验证码轰炸）
# ==================================================================

LOGIN = "/api/v1/auth/login"
REGISTER = "/api/v1/auth/register"
SEND_CODE = "/api/v1/auth/send-code"
RESET = "/api/v1/auth/reset-password"
CHANGE_PWD = "/api/v1/auth/password"

SMALL_LIMITS = {
    "login_per_user": 3,
    "login_per_ip": 5,
    "register_per_ip": 2,
    "send_code_per_email": 2,
    "send_code_per_ip": 4,
    "reset_per_email": 3,
    "reset_per_ip": 4,
    "change_pwd_per_user": 2,
}


@pytest.fixture()
def small_auth_limits(monkeypatch):
    """把认证限流阈值改小，便于在少量请求内触发 429"""
    monkeypatch.setattr(auth_router, "AUTH_LIMITS", dict(SMALL_LIMITS))
    return SMALL_LIMITS


def _login(client, username, password="wrong-password"):
    return client.post(LOGIN, json={"username": username, "password": password})


def test_login_bruteforce_is_rate_limited(client, small_auth_limits, monkeypatch):
    """攻击复现：对同一账号连续撞库，超出配额后 429，且不再进入密码校验（不消耗 bcrypt）"""
    checked = []
    monkeypatch.setattr(auth_router, "_authenticate",
                        lambda u, p: checked.append(u) or None)

    for _ in range(small_auth_limits["login_per_user"]):
        assert _login(client, "victim").status_code == 401

    resp = _login(client, "victim")
    assert resp.status_code == 429, resp.text
    assert resp.json()["code"] == 429
    assert len(checked) == small_auth_limits["login_per_user"], \
        "被限流的请求不得再触发密码校验（否则限流挡不住 CPU 消耗）"


def test_login_limit_is_per_account_and_per_ip(client, small_auth_limits, monkeypatch):
    """边界：账号维度不误伤其他账号；IP 维度配额耗尽后新账号同样被拒"""
    monkeypatch.setattr(auth_router, "_authenticate", lambda u, p: None)

    for _ in range(small_auth_limits["login_per_user"]):
        _login(client, "victim")
    assert _login(client, "victim").status_code == 429

    # 其他账号不受 victim 的账号桶影响（此时 IP 桶已用 4 次，仍在配额内）
    assert _login(client, "other").status_code == 401, "其他账号不应被 victim 的限流牵连"
    # 继续请求会耗尽 IP 维度配额（5），此后即便换账号也被拒
    assert _login(client, "third").status_code == 429


def test_send_code_is_rate_limited_per_email(client, small_auth_limits, monkeypatch):
    """攻击复现：对同一邮箱反复触发验证码下发（邮件轰炸）被 429 拦截"""
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)

    for _ in range(small_auth_limits["send_code_per_email"]):
        assert client.post(SEND_CODE, json={"email": "target@test.local"}).status_code == 200

    resp = client.post(SEND_CODE, json={"email": "target@test.local"})
    assert resp.status_code == 429, resp.text

    # 换邮箱不受该邮箱桶影响（IP 桶此时用 3 次 < 4）
    assert client.post(SEND_CODE, json={"email": "other@test.local"}).status_code == 200


def test_reset_password_attempts_are_rate_limited(client, small_auth_limits):
    """攻击复现：对同一邮箱连续猜码，超过配额后 429（与 MAX_CODE_ATTEMPTS 构成双重防护）"""
    for _ in range(small_auth_limits["reset_per_email"]):
        resp = client.post(RESET, json={
            "email": "brute@test.local", "code": "000000", "new_password": "NewPassw0rd!"})
        assert resp.status_code == 400, resp.text

    resp = client.post(RESET, json={
        "email": "brute@test.local", "code": "000000", "new_password": "NewPassw0rd!"})
    assert resp.status_code == 429, resp.text


def test_register_is_rate_limited_per_ip(client, small_auth_limits):
    """攻击复现：脚本批量注册被单 IP 配额拦住（配额内成功，超出即 429）"""
    def _reg(i):
        return client.post(REGISTER, json={
            "username": f"bulk_{i}", "password": "Passw0rd!123",
            "display_name": f"批量{i}", "email": f"bulk_{i}@test.local",
        }).status_code

    for i in range(small_auth_limits["register_per_ip"]):
        assert _reg(i) == 200, f"配额内的第 {i + 1} 个注册不应被拒"
    assert _reg(99) == 429


def test_change_password_attempts_are_rate_limited(client, session_factory, small_auth_limits):
    """攻击复现：拿到 token 后无限试原密码，被按账号限速"""
    _, _, headers = make_user(session_factory, "chpwd_s19")
    body = {"old_password": "wrong-password", "new_password": "NewPassw0rd!"}

    for _ in range(small_auth_limits["change_pwd_per_user"]):
        assert client.put(CHANGE_PWD, json=body, headers=headers).status_code == 400
    assert client.put(CHANGE_PWD, json=body, headers=headers).status_code == 429


def test_normal_auth_traffic_is_not_limited(client, monkeypatch):
    """正向对照：默认阈值下正常频率的操作全部放行，限流不误伤真实用户"""
    monkeypatch.setattr(auth_router, "_authenticate", lambda u, p: None)

    for _ in range(5):
        assert _login(client, "normal_user").status_code == 401
    assert client.post(SEND_CODE, json={"email": "normal@test.local"}).status_code == 200


def test_auth_rate_limit_config_defaults_and_validation(monkeypatch):
    """配置校验：默认值可用；非法值（非整数 / 非正数）直接报错"""
    monkeypatch.setattr(config, "load_env_file", lambda *a, **k: None)
    for name in (
        "AUTH_LOGIN_LIMIT_PER_USER", "AUTH_LOGIN_LIMIT_PER_IP",
        "AUTH_REGISTER_LIMIT_PER_IP", "AUTH_SEND_CODE_LIMIT_PER_EMAIL",
        "AUTH_SEND_CODE_LIMIT_PER_IP", "AUTH_RESET_LIMIT_PER_EMAIL",
        "AUTH_RESET_LIMIT_PER_IP", "AUTH_CHANGE_PWD_LIMIT_PER_USER",
    ):
        monkeypatch.delenv(name, raising=False)

    assert load_auth_rate_limits() == {
        "login_per_user": DEFAULT_AUTH_LOGIN_LIMIT_PER_USER,
        "login_per_ip": DEFAULT_AUTH_LOGIN_LIMIT_PER_IP,
        "register_per_ip": DEFAULT_AUTH_REGISTER_LIMIT_PER_IP,
        "send_code_per_email": DEFAULT_AUTH_SEND_CODE_LIMIT_PER_EMAIL,
        "send_code_per_ip": DEFAULT_AUTH_SEND_CODE_LIMIT_PER_IP,
        "reset_per_email": DEFAULT_AUTH_RESET_LIMIT_PER_EMAIL,
        "reset_per_ip": DEFAULT_AUTH_RESET_LIMIT_PER_IP,
        "change_pwd_per_user": DEFAULT_AUTH_CHANGE_PWD_LIMIT_PER_USER,
    }

    monkeypatch.setenv("AUTH_LOGIN_LIMIT_PER_USER", "ten")
    with pytest.raises(InsecureConfigError):
        load_auth_rate_limits()

    monkeypatch.setenv("AUTH_LOGIN_LIMIT_PER_USER", "0")
    with pytest.raises(InsecureConfigError):
        load_auth_rate_limits()