"""
S10 断线重连放行过宽 —— 复现脚本 + 修复后验证

【修复前的绕过路径】
`join_meeting` 的判定顺序是「先查重连记录，命中即直接放行」，准入闸门
（锁定 / 密码 / 白名单）排在重连分支之后。于是攻击者只需：
    1) 用正确密码（或在白名单内、会议未锁时）合法入会一次；
    2) 主动断线或刷新页面，把 participant.left_at 置为「已离开」；
    3) 之后每次 /join 都命中重连分支，从闸门后面直入 ——
即使主持人随后锁会、改密码、清空白名单，也一律拦不住。

【修复要点】
把「锁定 / 密码 / 白名单」三项闸门整体上移到重连分支之前，使其对
「首次入会」与「断线重连」同等生效；创建者 / 主持人 / 联席主持仍享有豁免
（会议锁定后主持人必须能回到自己的会议）。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s10_reconnect_gate.py -v
"""
from contextlib import contextmanager
from datetime import datetime

from conftest import create_meeting, make_user, participant_id_of  # noqa: E402
from app.models import Meeting, MeetingWhitelist, Participant  # noqa: E402

PASSWORD = "Secret!123"


# ---------------- 辅助 ----------------

def _try_join(client, meeting_no, headers, password=""):
    """调用 /join 并返回原始响应（不 assert，便于断言失败状态码）"""
    return client.post(f"/api/v1/meetings/{meeting_no}/join",
                       json={"password": password}, headers=headers)


def _set_left(session_factory, meeting_no, username, status="left"):
    """把某参会者改为「已离开」，模拟断线 / 刷新页面"""
    db = session_factory()
    try:
        row = db.query(Participant).join(Meeting, Participant.meeting_id == Meeting.id).filter(
            Meeting.meeting_no == meeting_no,
            Participant.display_name == username,
        ).first()
        assert row is not None, f"{username} 不在会议 {meeting_no} 中"
        row.left_at = datetime.now()
        row.status = status
        db.commit()
        return row.id
    finally:
        db.close()


def _set_locked(session_factory, meeting_no, locked=True):
    """直接改库：锁定 / 解锁会议（当前没有对外的锁会 REST 接口）"""
    db = session_factory()
    try:
        db.query(Meeting).filter(Meeting.meeting_no == meeting_no).update({"locked": locked})
        db.commit()
    finally:
        db.close()


def _set_whitelist(session_factory, meeting_no, user_id, present=True):
    """直接增删白名单行（当前没有白名单维护接口）"""
    db = session_factory()
    try:
        meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
        row = db.query(MeetingWhitelist).filter(
            MeetingWhitelist.meeting_id == meeting.id,
            MeetingWhitelist.user_id == user_id,
        ).first()
        if present and row is None:
            db.add(MeetingWhitelist(meeting_id=meeting.id, user_id=user_id))
        elif not present and row is not None:
            db.delete(row)
        db.commit()
    finally:
        db.close()


def _legacy_reconnect_matches(session_factory, meeting_no, user_id):
    """复刻修复前的重连判定：只看「是否离开过」，完全不过准入闸门

    返回 True 表示「修复前的代码会把这名用户当作重连者直接放行」。
    """
    db = session_factory()
    try:
        meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
        row = db.query(Participant).filter(
            Participant.meeting_id == meeting.id,
            Participant.user_id == user_id,
            Participant.left_at != None,  # noqa: E711
            Participant.status.in_(["joined", "left", "waiting"]),
        ).first()
        return row is not None
    finally:
        db.close()


@contextmanager
def _open_ws(client, meeting_no, participant_id, token):
    """打开 WS 会话；退出时忽略「服务端已主动关闭」导致的异常"""
    session = client.websocket_connect(
        f"/api/v1/ws/{meeting_no}?participant_id={participant_id}")
    session.__enter__()
    try:
        # token 不再走 URL：连接建立后首帧上报，服务端校验通过前不下发任何数据
        session.send_json({"type": "auth", "token": token})
        yield session
    finally:
        try:
            session.__exit__(None, None, None)
        except Exception:
            pass


def _drain(ws, max_messages=40):
    """发送 ping 并等到 pong，确认该连接可正常收发（消息在同一连接内 FIFO）"""
    ws.send_json({"type": "ping"})
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == "pong":
            return
    raise AssertionError("未收到 pong，WebSocket 处理链路异常")


# ---------------- 复现 + 修复后验证 ----------------

def test_negative_control_locked_meeting_legacy_reconnect_bypass(client, session_factory):
    """负向对照：锁定会议 + 离开过的成员 —— 旧判定会放行，修复后必须 403

    这一条同时展示了攻击链的「修复前会失败」对照：
    同一份数据（已锁定 + left_at 非空），旧逻辑命中重连分支 ⇒ 直入；
    新逻辑先过锁定闸门 ⇒ 拒绝。
    """
    _, _, host_h = make_user(session_factory, "host_s10a")
    guest_id, _, guest_h = make_user(session_factory, "guest_s10a")
    meeting_no = create_meeting(client, host_h, title="锁定会议")["meeting_no"]

    assert _try_join(client, meeting_no, guest_h).status_code == 200
    _set_left(session_factory, meeting_no, "guest_s10a")
    _set_locked(session_factory, meeting_no, True)

    # 修复前的判定：命中重连记录 ⇒ 不走闸门，直接放行
    assert _legacy_reconnect_matches(session_factory, meeting_no, guest_id) is True

    # 修复后：同样的状态被锁定闸门拦下
    resp = _try_join(client, meeting_no, guest_h)
    assert resp.status_code == 403, resp.text
    assert "锁定" in resp.json()["message"]


def test_reconnect_rejected_when_meeting_locked(client, session_factory):
    """断线重连同样受「会议锁定」约束"""
    _, _, host_h = make_user(session_factory, "host_s10b")
    _, _, guest_h = make_user(session_factory, "guest_s10b")
    meeting_no = create_meeting(client, host_h, title="锁会后重连")["meeting_no"]

    assert _try_join(client, meeting_no, guest_h).status_code == 200
    _set_locked(session_factory, meeting_no, True)
    _set_left(session_factory, meeting_no, "guest_s10b")

    resp = _try_join(client, meeting_no, guest_h)
    assert resp.status_code == 403, resp.text
    assert "锁定" in resp.json()["message"]


def test_reconnect_requires_correct_password(client, session_factory):
    """断线重连同样要校验会议密码：改密后旧连接者无法刷新直入"""
    _, _, host_h = make_user(session_factory, "host_s10c")
    _, _, guest_h = make_user(session_factory, "guest_s10c")
    meeting_no = create_meeting(client, host_h, title="改密会议", password=PASSWORD)["meeting_no"]

    assert _try_join(client, meeting_no, guest_h, PASSWORD).status_code == 200
    _set_left(session_factory, meeting_no, "guest_s10c")

    # 不带密码 → 提示需要密码
    resp = _try_join(client, meeting_no, guest_h)
    assert resp.status_code == 403, resp.text
    assert "需要会议密码" in resp.json()["message"]

    # 密码错误 → 拒绝
    resp = _try_join(client, meeting_no, guest_h, "WrongPass")
    assert resp.status_code == 403, resp.text
    assert "密码错误" in resp.json()["message"]

    # 密码正确 → 放行，且仍是「重连」而非新建参会记录
    resp = _try_join(client, meeting_no, guest_h, PASSWORD)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "joined"


def test_reconnect_rejected_when_removed_from_whitelist(client, session_factory):
    """断线重连同样受白名单约束：被移出白名单后无法刷新直入"""
    _, _, host_h = make_user(session_factory, "host_s10d")
    guest_id, _, guest_h = make_user(session_factory, "guest_s10d")
    meeting_no = create_meeting(client, host_h, title="白名单会议", whitelist=True)["meeting_no"]

    _set_whitelist(session_factory, meeting_no, guest_id, present=True)
    assert _try_join(client, meeting_no, guest_h).status_code == 200

    _set_whitelist(session_factory, meeting_no, guest_id, present=False)
    _set_left(session_factory, meeting_no, "guest_s10d")

    resp = _try_join(client, meeting_no, guest_h)
    assert resp.status_code == 403, resp.text
    assert "白名单" in resp.json()["message"]

    # 放回白名单 → 恢复重连
    _set_whitelist(session_factory, meeting_no, guest_id, present=True)
    assert _try_join(client, meeting_no, guest_h).status_code == 200


def test_creator_can_rejoin_locked_meeting(client, session_factory):
    """锁定闸门对创建者 / 主持人 / 联席主持豁免：主持人必须能回到自己的会议"""
    _, _, host_h = make_user(session_factory, "host_s10e")
    meeting_no = create_meeting(client, host_h, title="主持人回场")["meeting_no"]

    _set_locked(session_factory, meeting_no, True)
    _set_left(session_factory, meeting_no, "host_s10e")

    resp = _try_join(client, meeting_no, host_h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "joined"

    # 同一会议内的对照：普通成员在同样条件下被锁定闸门拦下
    _, _, guest_h = make_user(session_factory, "guest_s10e")
    assert _try_join(client, meeting_no, guest_h).status_code == 403


def test_normal_reconnect_still_allowed_and_functional(client, session_factory):
    """无锁 / 无密码 / 无白名单时，正常断线重连必须照常放行并可用"""
    _, _, host_h = make_user(session_factory, "host_s10f")
    _, guest_token, guest_h = make_user(session_factory, "guest_s10f")
    meeting_no = create_meeting(client, host_h, title="正常重连")["meeting_no"]

    assert _try_join(client, meeting_no, guest_h).status_code == 200
    _set_left(session_factory, meeting_no, "guest_s10f")

    resp = _try_join(client, meeting_no, guest_h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "joined"

    # 重连后应当能真正建立 WS（而不是只拿到一个 URL）
    guest_pid = participant_id_of(session_factory, meeting_no, "guest_s10f")
    with _open_ws(client, meeting_no, guest_pid, guest_token) as ws:
        _drain(ws)   # 能完成 ping→pong 即证明连接已进入会议

    # 重连复用同一条参会记录，不应产生重复行
    db = session_factory()
    try:
        rows = db.query(Participant).join(
            Meeting, Participant.meeting_id == Meeting.id).filter(
            Meeting.meeting_no == meeting_no,
            Participant.display_name == "guest_s10f",
        ).all()
    finally:
        db.close()
    assert len(rows) == 1, "断线重连应复用参会记录，而非新建"