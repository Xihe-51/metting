"""
S9 踢人失效 —— 攻击复现脚本 + 修复后验证

【修复前的绕过路径】
1. 主持人踢人后，被踢用户刷新页面重新调用 /join 会走「新参与者」分支重新入会，踢人形同虚设；
2. 被踢用户直接拿旧的 participant_id + 自己的合法 JWT 重连 WebSocket
   （WS 入口只校验归属、不校验状态），一步绕过踢人；
3. 等候室被拒绝的用户可以反复 join 反复出现在等候室，骚扰主持人；
4. reject_user 对已入会成员也生效，等于给主持人一个「无痕断线」后门。

【修复要点】
- WS 入口：participant.status 必须属于 joined/waiting 且 left_at 为空；
- /join 入口：存在 kicked/rejected 记录的用户直接 403（创建者/主持人/联席主持例外）；
- admit_user / reject_user 只对「本会议仍在等候室」的目标生效。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s9_kick_reenroll.py -v
"""
import time
from contextlib import contextmanager

import pytest
from starlette.websockets import WebSocketDisconnect

from conftest import (  # noqa: E402
    create_meeting,
    get_meeting_row,
    get_participant,
    join_meeting,
    make_user,
    participant_id_of,
)
from app.websocket_manager import manager  # noqa: E402


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
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == expected_type:
            return msg
    raise AssertionError(f"未收到 {expected_type} 消息")


def _drain(ws, max_messages=40):
    """发送 ping 并等到 pong，返回此前收到的消息列表（同一连接内消息 FIFO）"""
    ws.send_json({"type": "ping"})
    seen = []
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == "pong":
            return seen
        seen.append(msg)
    raise AssertionError("未收到 pong，WebSocket 处理链路异常")


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _try_join(client, meeting_no, headers):
    return client.post(f"/api/v1/meetings/{meeting_no}/join",
                       json={"password": ""}, headers=headers)


def _setup_meeting(client, session_factory, host_name, guest_name, waiting_room=False):
    _, host_token, host_h = make_user(session_factory, host_name)
    _, guest_token, guest_h = make_user(session_factory, guest_name)

    data = create_meeting(client, host_h, title="踢人验证", waiting_room=waiting_room)
    meeting_no = data["meeting_no"]
    joined = join_meeting(client, guest_h, meeting_no)

    host_id = participant_id_of(session_factory, meeting_no, host_name)
    guest_id = participant_id_of(session_factory, meeting_no, guest_name)
    return meeting_no, host_id, host_token, guest_id, guest_token, guest_h, joined


# ---------------- 攻击复现 + 修复后验证 ----------------

def test_kicked_user_cannot_rejoin(client, session_factory):
    """攻击复现：被踢用户重新调用 /join 再次入会 —— 修复后必须 403"""
    meeting_no, host_id, host_token, guest_id, guest_token, guest_h, _ = _setup_meeting(
        client, session_factory, "host_s9a", "guest_s9a")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
        _wait_for(host_ws, "user_joined")

        host_ws.send_json({"type": "kick_user", "payload": {"target_id": guest_id}})
        assert _wait_for(guest_ws, "you_were_kicked")["payload"]["by"] == "host_s9a"

        assert _wait_until(lambda: get_participant(
            session_factory, meeting_no, "guest_s9a").status == "kicked")
        assert get_meeting_row(session_factory, meeting_no).participant_count == 1

        # 攻击复现：刷新页面后重新加入
        resp = _try_join(client, meeting_no, guest_h)
        assert resp.status_code == 403, resp.text
        assert "移出" in resp.json()["message"]

        # 会议内不存在该用户的活跃记录
        assert get_participant(session_factory, meeting_no, "guest_s9a").left_at is not None


def test_kicked_user_cannot_reconnect_websocket(client, session_factory):
    """攻击复现：被踢用户拿旧 participant_id + 合法 JWT 直接重连 WS"""
    meeting_no, host_id, host_token, guest_id, guest_token, guest_h, _ = _setup_meeting(
        client, session_factory, "host_s9b", "guest_s9b")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws:
        with _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
            # 先等同会议成员接入的广播，确保被踢者的连接已完成首帧鉴权并注册；
            # 否则踢人可能先于鉴权生效，被踢者收到的是 1008 硬断开而非 you_were_kicked
            _wait_for(host_ws, "user_joined")

            host_ws.send_json({"type": "kick_user", "payload": {"target_id": guest_id}})
            _wait_for(guest_ws, "you_were_kicked")
            assert _wait_until(lambda: get_participant(
                session_factory, meeting_no, "guest_s9b").status == "kicked")

        # 服务端必须在首帧鉴权后立即断开（连接已 accept，故表现为 WebSocketDisconnect）
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/v1/ws/{meeting_no}?participant_id={guest_id}"
            ) as ws:
                ws.send_json({"type": "auth", "token": guest_token})
                for _ in range(3):
                    ws.receive_json()


def test_kicked_waiting_user_cannot_rejoin(client, session_factory):
    """攻击复现：等候室中的用户被踢后反复 join 骚扰"""
    meeting_no, host_id, host_token, guest_id, guest_token, guest_h, joined = _setup_meeting(
        client, session_factory, "host_s9c", "guest_s9c", waiting_room=True)
    assert joined["status"] == "waiting"

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
        assert guest_ws.receive_json()["type"] == "waiting_room"

        host_ws.send_json({"type": "kick_user", "payload": {"target_id": guest_id}})
        assert _wait_for(guest_ws, "you_were_kicked")
        assert _wait_until(lambda: get_participant(
            session_factory, meeting_no, "guest_s9c").status == "kicked")

        resp = _try_join(client, meeting_no, guest_h)
        assert resp.status_code == 403, resp.text


def test_rejected_user_cannot_rejoin(client, session_factory):
    """攻击复现：被等候室拒绝的用户反复 join 反复出现"""
    meeting_no, host_id, host_token, guest_id, guest_token, guest_h, joined = _setup_meeting(
        client, session_factory, "host_s9d", "guest_s9d", waiting_room=True)

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
        assert guest_ws.receive_json()["type"] == "waiting_room"

        host_ws.send_json({"type": "reject_user", "payload": {"target_id": guest_id}})
        assert _wait_for(guest_ws, "you_are_rejected")
        assert _wait_until(lambda: get_participant(
            session_factory, meeting_no, "guest_s9d").status == "rejected")
        assert get_meeting_row(session_factory, meeting_no).participant_count == 1

        resp = _try_join(client, meeting_no, guest_h)
        assert resp.status_code == 403, resp.text


def test_reject_does_not_disconnect_joined_member(client, session_factory):
    """边界场景：reject_user 只对等候室成员生效，不能当成对已入会成员的静默断线"""
    meeting_no, host_id, host_token, guest_id, guest_token, guest_h, joined = _setup_meeting(
        client, session_factory, "host_s9e", "guest_s9e")
    assert joined["status"] == "joined"

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
        _wait_for(host_ws, "user_joined")
        _drain(host_ws)          # 清掉握手期消息（participants_list / waiting_participants）
        _drain(guest_ws)         # 清掉自身握手期的 participants_list

        host_ws.send_json({"type": "reject_user", "payload": {"target_id": guest_id}})
        _drain(host_ws)

        # 连接仍然可用，且没有收到拒绝通知
        assert _drain(guest_ws) == []
        row = get_participant(session_factory, meeting_no, "guest_s9e")
        assert row.status == "joined"
        assert row.left_at is None
        assert get_meeting_row(session_factory, meeting_no).participant_count == 2


def test_kick_only_affects_target(client, session_factory):
    """正常场景：踢人只影响目标，其他成员与会议状态不受影响"""
    _, host_token, host_h = make_user(session_factory, "host_s9f")
    _, b_token, b_h = make_user(session_factory, "guest_s9f")
    _, c_token, c_h = make_user(session_factory, "other_s9f")

    data = create_meeting(client, host_h, title="多人踢人")
    meeting_no = data["meeting_no"]
    join_meeting(client, b_h, meeting_no)
    join_meeting(client, c_h, meeting_no)

    host_id = participant_id_of(session_factory, meeting_no, "host_s9f")
    b_id = participant_id_of(session_factory, meeting_no, "guest_s9f")
    c_id = participant_id_of(session_factory, meeting_no, "other_s9f")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws, \
            _open_ws(client, meeting_no, b_id, b_token) as b_ws, \
            _open_ws(client, meeting_no, c_id, c_token) as c_ws:
        host_ws.send_json({"type": "kick_user", "payload": {"target_id": b_id}})
        assert _wait_for(b_ws, "you_were_kicked")

        assert _wait_until(lambda: get_participant(
            session_factory, meeting_no, "guest_s9f").status == "kicked")
        assert get_meeting_row(session_factory, meeting_no).participant_count == 2

        # 另一位成员仍然在会并可正常发言
        c_ws.send_json({"type": "chat", "payload": {"content": "我还在"}})
        assert _wait_for(host_ws, "chat")["payload"]["content"] == "我还在"
        c_row = get_participant(session_factory, meeting_no, "other_s9f")
        assert c_row.status == "joined" and c_row.left_at is None
        assert c_id in manager.active_connections.get(meeting_no, {})


def test_left_user_can_rejoin_but_kicked_cannot(client, session_factory):
    """负向对照：掉线离会的用户仍可重新入会，只有 kicked/rejected 被禁止

    同一个用户先「掉线 → 重连成功」，再「被踢 → 重连失败」，
    证明 403 是踢人状态直接导致的，而不是 /join 接口本身不可用（断言不是空过）。
    """
    meeting_no, host_id, host_token, guest_id, guest_token, guest_h, _ = _setup_meeting(
        client, session_factory, "host_s9g", "guest_s9g")

    with _open_ws(client, meeting_no, host_id, host_token) as host_ws:
        with _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws:
            _wait_for(host_ws, "user_joined")

        # 掉线（服务端置 left）后重新入会：应当成功
        assert _wait_until(lambda: get_participant(
            session_factory, meeting_no, "guest_s9g").left_at is not None)
        resp = _try_join(client, meeting_no, guest_h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "joined"

        # 重连入会后被主持人踢出
        with _open_ws(client, meeting_no, guest_id, guest_token) as guest_ws2:
            # 同上：先等新连接接入的广播，确保被踢者的连接已注册
            _wait_for(host_ws, "user_joined")

            host_ws.send_json({"type": "kick_user", "payload": {"target_id": guest_id}})
            _wait_for(guest_ws2, "you_were_kicked")
            assert _wait_until(lambda: get_participant(
                session_factory, meeting_no, "guest_s9g").status == "kicked")

        # 同一个用户，此时重新入会：必须被拒绝
        _r3 = _try_join(client, meeting_no, guest_h)
        assert _r3.status_code == 403, _r3.text
        # 断线处理不得把 kicked 抹成 left，否则会被「断线重连」分支放回会议
        final_row = get_participant(session_factory, meeting_no, "guest_s9g")
        assert final_row.status == "kicked"
        assert final_row.left_at is not None