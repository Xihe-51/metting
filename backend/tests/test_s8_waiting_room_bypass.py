"""
S8 等候室绕过 —— 攻击复现脚本 + 修复后验证

【修复前的绕过路径】
1. 等候中的用户与已入会成员共用同一个广播池 active_connections，
   群聊、私聊、参会名单、设备状态、屏幕共享、表情全部泄露；
2. 等候中的用户可以直接发送 offer/answer/ice，与主会场成员建立 P2P 连接，实质进入会议；
3. 等候中的用户可以发送 device/screen/speaking/reaction 干扰主会场；
4. 等候室模式下「进等候室 → 主动断开 → 重新 join」会被判定为 joined，直接绕过等候室。

【修复要点】
- 连接层：waiting_connections 独立于 active_connections，所有广播只覆盖后者；
- 消息层：等候中的连接只允许 ping / leave_meeting，其余消息静默丢弃（状态动态读取）；
- 信令层：offer/answer/ice 不允许转发到等候中的连接；
- 状态层：Participant.admitted 标记「曾被正式准入」，断线重连据此判断是否需重新等候。

【断言口径】
- 等候中的用户：必须「一条主会场消息都收不到」，因此直接断言收到的消息列表为空；
- 已入会的主持人：自身握手时会收到 participants_list / waiting_participants，
  这类消息不属于泄露，用 FORBIDDEN_TYPES 过滤后再断言。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s8_waiting_room_bypass.py -v
"""
import time
from contextlib import contextmanager

from conftest import (  # noqa: E402
    create_meeting,
    get_meeting_row,
    get_participant,
    join_meeting,
    make_user,
    participant_id_of,
)
from app.models import ChatMessage, Meeting  # noqa: E402
from app.websocket_manager import manager  # noqa: E402

# 主会场成员之间才会产生的消息类型：等候中的用户绝不应该收到
FORBIDDEN_TYPES = {
    "chat", "whisper", "offer", "answer", "ice", "device", "screen",
    "reaction", "speaking", "user_joined", "user_left",
    "meeting_locked", "host_changed", "co_host_changed", "mute_status",
    "force_mute", "you_were_kicked",
}


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


def _wait_for(ws, expected_type, max_messages=40):
    """等待指定类型的消息（同一连接内消息 FIFO）"""
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == expected_type:
            return msg
    raise AssertionError(f"未收到 {expected_type} 消息")


def _drain(ws, max_messages=40):
    """发送 ping 并等到 pong，返回此前收到的消息列表

    同一连接内的消息严格 FIFO：若发送者先发出某条消息再发 ping，
    收到 pong 即证明该消息已被服务端处理完毕。
    """
    ws.send_json({"type": "ping"})
    seen = []
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == "pong":
            return seen
        seen.append(msg)
    raise AssertionError("未收到 pong，WebSocket 处理链路异常")


def _forbidden(seen):
    return [m for m in seen if m.get("type") in FORBIDDEN_TYPES]


def _wait_until(predicate, timeout=2.0):
    """等待某个服务端副作用落地（例如断开连接后 left_at 被写入）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _chat_count(session_factory, meeting_no):
    db = session_factory()
    try:
        meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
        return db.query(ChatMessage).filter(ChatMessage.meeting_id == meeting.id).count()
    finally:
        db.close()


def _setup_waiting_meeting(client, session_factory, host_name, guest_name):
    """建一个开启等候室的会议，主持人 + 1 名等候者，返回关键标识"""
    _, host_token, host_h = make_user(session_factory, host_name)
    _, guest_token, guest_h = make_user(session_factory, guest_name)

    data = create_meeting(client, host_h, title="等候室隔离验证", waiting_room=True)
    meeting_no = data["meeting_no"]
    joined = join_meeting(client, guest_h, meeting_no)
    assert joined["status"] == "waiting", "等候室模式下新成员必须是 waiting"

    host_id = participant_id_of(session_factory, meeting_no, host_name)
    guest_id = participant_id_of(session_factory, meeting_no, guest_name)
    return meeting_no, host_id, host_token, guest_id, guest_token, guest_h


# ---------------- 攻击复现 + 修复后验证 ----------------

def test_waiting_user_is_isolated_from_broadcast_pool(client, session_factory):
    """攻击复现：等候中的用户不在广播池内，收不到任何主会场消息"""
    meeting_no, host_id, host_token, guest_id, guest_token, _ = _setup_waiting_meeting(
        client, session_factory, "host_s8a", "guest_s8a")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
        # 等候者只收到自己的等候室提示
        assert guest_ws.receive_json()["type"] == "waiting_room"

        # 白盒：等候者只在等候池，绝不在广播池
        assert guest_id in manager.waiting_connections.get(meeting_no, {})
        assert guest_id not in manager.active_connections.get(meeting_no, {})
        assert host_id in manager.active_connections.get(meeting_no, {})

        # 黑盒：主持人发群聊（主持人自己收到作为同步屏障），等候者收不到
        host_ws.send_json({"type": "chat", "payload": {"content": "主会场机密内容"}})
        chat = _wait_for(host_ws, "chat")
        assert chat["payload"]["content"] == "主会场机密内容"

        assert _drain(guest_ws) == [], "等候者不应收到任何主会场消息"


def test_waiting_user_cannot_send_chat(client, session_factory):
    """攻击复现：等候中的用户发言必须被静默丢弃，且不落库"""
    meeting_no, host_id, host_token, guest_id, guest_token, _ = _setup_waiting_meeting(
        client, session_factory, "host_s8b", "guest_s8b")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
        guest_ws.receive_json()
        _drain(host_ws)  # 清掉握手期的 participants_list / waiting_participants

        guest_ws.send_json({"type": "chat", "payload": {"content": "偷跑的消息"}})
        assert _drain(guest_ws) == []                   # 发送者自己也收不到回显
        assert _forbidden(_drain(host_ws)) == []        # 主会场成员收不到
        assert _chat_count(session_factory, meeting_no) == 0, "等候者的消息不能落库"


def test_waiting_user_cannot_send_webrtc_signaling(client, session_factory):
    """攻击复现：等候中的用户发 offer 拉主会场成员建 P2P，必须被拦截"""
    meeting_no, host_id, host_token, guest_id, guest_token, _ = _setup_waiting_meeting(
        client, session_factory, "host_s8c", "guest_s8c")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
        guest_ws.receive_json()
        _drain(host_ws)

        guest_ws.send_json({"type": "offer", "payload": {
            "target_id": host_id, "sdp": {"type": "offer", "sdp": "fake-sdp"}}})
        assert _drain(guest_ws) == []
        assert _forbidden(_drain(host_ws)) == [], "等候者不得与主会场建立信令通道"


def test_signaling_to_waiting_user_is_blocked(client, session_factory):
    """修复后验证：信令双向隔离 —— 已入会成员也不能把等候者拉进 P2P"""
    meeting_no, host_id, host_token, guest_id, guest_token, _ = _setup_waiting_meeting(
        client, session_factory, "host_s8d", "guest_s8d")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
        guest_ws.receive_json()

        host_ws.send_json({"type": "offer", "payload": {
            "target_id": guest_id, "sdp": {"type": "offer", "sdp": "fake-sdp"}}})
        _drain(host_ws)
        assert _drain(guest_ws) == [], "等候者不得收到任何信令"


def test_all_waiting_user_actions_are_ignored(client, session_factory):
    """攻击复现：等候中的用户发送任何非心跳消息都不产生副作用"""
    meeting_no, host_id, host_token, guest_id, guest_token, _ = _setup_waiting_meeting(
        client, session_factory, "host_s8e", "guest_s8e")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
        guest_ws.receive_json()
        _drain(host_ws)

        for msg_type, payload in [
            ("device", {"audio_on": False, "video_on": False}),
            ("screen", {"sharing": True}),
            ("reaction", {"emoji": "👍"}),
            ("speaking", {"speaking": True}),
            ("whisper", {"target_id": host_id, "content": "私聊"}),
            ("lock_meeting", {}),
            ("unlock_meeting", {}),
            ("force_mute_all", {}),
            ("kick_user", {"target_id": host_id}),
            ("admit_user", {"target_id": guest_id}),
            ("transfer_host", {"target_id": guest_id}),
        ]:
            guest_ws.send_json({"type": msg_type, "payload": payload})

        assert _drain(guest_ws) == []
        assert _forbidden(_drain(host_ws)) == []

        # 会议状态未被篡改
        row = get_meeting_row(session_factory, meeting_no)
        assert row.locked is False
        host_p = get_participant(session_factory, meeting_no, "host_s8e")
        assert host_p.status == "joined" and host_p.left_at is None
        assert host_p.audio_on is True
        assert row.host_id != guest_id


def test_waiting_user_cannot_observe_other_admissions(client, session_factory):
    """修复后验证：他人被准入时，等候者收不到 user_joined / participants_list"""
    _, host_token, host_h = make_user(session_factory, "host_s8f")
    _, guest_token, guest_h = make_user(session_factory, "guest_s8f")
    _, third_token, third_h = make_user(session_factory, "third_s8f")

    data = create_meeting(client, host_h, title="多人等候", waiting_room=True)
    meeting_no = data["meeting_no"]
    join_meeting(client, guest_h, meeting_no)
    join_meeting(client, third_h, meeting_no)

    host_id = participant_id_of(session_factory, meeting_no, "host_s8f")
    guest_id = participant_id_of(session_factory, meeting_no, "guest_s8f")
    third_id = participant_id_of(session_factory, meeting_no, "third_s8f")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws, \
            _open_ws(client, meeting_no, third_id, third_token) as third_ws:
        assert guest_ws.receive_json()["type"] == "waiting_room"
        assert third_ws.receive_json()["type"] == "waiting_room"

        host_ws.send_json({"type": "admit_user", "payload": {"target_id": third_id}})

        # 被准入者正常收到通知与名单
        admitted = _wait_for(third_ws, "you_are_admitted")
        assert admitted["type"] == "you_are_admitted"
        assert "participants_list" in [m["type"] for m in _drain(third_ws)]

        # 仍在等候的 guest 什么也看不到
        assert _drain(guest_ws) == []


def test_disconnect_and_rejoin_cannot_bypass_waiting_room(client, session_factory):
    """攻击复现：进等候室 → 主动断开 → 重新 join，不得直入主会场"""
    meeting_no, host_id, host_token, guest_id, guest_token, guest_h = _setup_waiting_meeting(
        client, session_factory, "host_s8g", "guest_s8g")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws:
        with _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
            assert guest_ws.receive_json()["type"] == "waiting_room"

        # 模拟刷新页面：连接断开，服务端把该记录置为 left
        assert _wait_until(
            lambda: get_participant(session_factory, meeting_no, "guest_s8g").left_at is not None)

        resp = client.post(f"/api/v1/meetings/{meeting_no}/join",
                           json={"password": ""}, headers=guest_h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "waiting", "重新加入必须回到等候室"

        row = get_participant(session_factory, meeting_no, "guest_s8g")
        assert row.status == "waiting"
        assert row.admitted is False


def test_admitted_user_regains_full_access(client, session_factory):
    """正常场景：准入后连接迁入广播池，可以正常收发群聊"""
    meeting_no, host_id, host_token, guest_id, guest_token, _ = _setup_waiting_meeting(
        client, session_factory, "host_s8h", "guest_s8h")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
        guest_ws.receive_json()

        host_ws.send_json({"type": "admit_user", "payload": {"target_id": guest_id}})
        _wait_for(guest_ws, "you_are_admitted")

        # 白盒：连接已从等候池迁入广播池
        assert guest_id in manager.active_connections.get(meeting_no, {})
        assert guest_id not in manager.waiting_connections.get(meeting_no, {})

        # 准入后发言恢复正常
        guest_ws.send_json({"type": "chat", "payload": {"content": "我进来了"}})
        assert "chat" in [m["type"] for m in _drain(guest_ws)]
        assert _wait_for(host_ws, "chat")["payload"]["content"] == "我进来了"

        row = get_participant(session_factory, meeting_no, "guest_s8h")
        assert row.status == "joined" and row.admitted is True


def test_admitted_user_reconnect_stays_joined(client, session_factory):
    """边界场景：曾被正式准入的成员掉线重连，无需重新等候"""
    meeting_no, host_id, host_token, guest_id, guest_token, guest_h = _setup_waiting_meeting(
        client, session_factory, "host_s8i", "guest_s8i")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws:
        with _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
            guest_ws.receive_json()
            host_ws.send_json({"type": "admit_user", "payload": {"target_id": guest_id}})
            _wait_for(guest_ws, "you_are_admitted")

        assert _wait_until(
            lambda: get_participant(session_factory, meeting_no, "guest_s8i").left_at is not None)

        resp = client.post(f"/api/v1/meetings/{meeting_no}/join",
                           json={"password": ""}, headers=guest_h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "joined", "已准入成员重连不应回到等候室"


def test_waiting_user_can_still_ping(client, session_factory):
    """边界场景：等候中的连接不会被误杀，心跳与主动退出仍然可用"""
    meeting_no, host_id, host_token, guest_id, guest_token, _ = _setup_waiting_meeting(
        client, session_factory, "host_s8j", "guest_s8j")

    with _open_ws(client, meeting_no, host_id, host_token):
        with _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
            guest_ws.receive_json()
            assert _drain(guest_ws) == []       # ping → pong，无任何其他消息
            assert manager.get_status(meeting_no, guest_id) == "waiting"


def test_negative_control_pool_sharing_would_leak(client, session_factory):
    """负向对照：把等候者手工放回广播池（等价于修复前 connect() 的实现），群聊立刻泄露

    本用例证明上面的断言不是「空过」：只要等候者出现在 active_connections 中，
    主会场的群聊就会送达等候者，test_waiting_user_is_isolated_from_broadcast_pool 必然失败。
    """
    meeting_no, host_id, host_token, guest_id, guest_token, _ = _setup_waiting_meeting(
        client, session_factory, "host_s8k", "guest_s8k")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
        guest_ws.receive_json()

        # 复现修复前的连接池结构：等候者与已入会成员共用同一个 Dict
        manager.active_connections[meeting_no][guest_id] = \
            manager.waiting_connections[meeting_no][guest_id]

        host_ws.send_json({"type": "chat", "payload": {"content": "泄露内容"}})
        _wait_for(host_ws, "chat")

        leaked = [m for m in _drain(guest_ws) if m["type"] == "chat"]
        assert leaked, "广播池混用时群聊必然泄露（修复前正是此结构）"
        assert leaked[0]["payload"]["content"] == "泄露内容"