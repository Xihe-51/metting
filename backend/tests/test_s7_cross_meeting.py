"""
S7 修复验证：主持人操作必须以「会议归属」为边界，杜绝跨会议越权

修复点（app/routers/meeting_router.py）：
- 新增 get_meeting_participant(db, meeting_id, participant_id)，
  transfer_host / set_co_host / mute_user / kick_user / admit_user / reject_user
  / force_mute_all 全部改用它取目标；
- kick_user 顺带修复 target 不存在时 audit_log 取属性导致 AttributeError 崩溃。

运行：
    cd D:\\meeting\\backend
    D:\\conda\\envs\\meeting\\python.exe -m pytest tests/test_s7_cross_meeting.py -v
"""
from contextlib import contextmanager

from conftest import (  # noqa: E402
    create_meeting,
    get_meeting_row,
    get_participant,
    join_meeting,
    make_user,
    participant_id_of,
)


@contextmanager
def _open_ws(client, meeting_no, participant_id, token):
    """打开 WS 会话并完成首帧鉴权（token 不再走 URL）"""
    session = client.websocket_connect(
        f"/api/v1/ws/{meeting_no}?participant_id={participant_id}"
    )
    session.__enter__()
    try:
        session.send_json({"type": "auth", "token": token})
        yield session
    finally:
        try:
            session.__exit__(None, None, None)
        except Exception:
            pass


def _sync(ws, max_messages=30):
    """发送 ping 并等待 pong：确保此前发送的指令已被服务端处理完毕"""
    ws.send_json({"type": "ping"})
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == "pong":
            return
    raise AssertionError("未收到 pong，WebSocket 处理链路异常中断")


# ---------------- 攻击复现 ----------------

def test_cross_meeting_kick_is_ignored(client, session_factory):
    """攻击复现：A 会议主持人用 B 会议成员的 participant_id 踢人

    修复前：B 会议成员被标记 kicked + left_at，且 A 会议 participant_count 被误减
    修复后：目标不属于本会议 -> 直接忽略，双方数据均不受影响
    """
    _, host_a_tok, host_a_h = make_user(session_factory, "s7_hostA")
    meeting_a = create_meeting(client, host_a_h, title="会议A")
    a_no = meeting_a["meeting_no"]
    host_a_pid = participant_id_of(session_factory, a_no, "s7_hostA")

    _, _, host_b_h = make_user(session_factory, "s7_hostB")
    meeting_b = create_meeting(client, host_b_h, title="会议B")
    b_no = meeting_b["meeting_no"]

    _, victim_tok, victim_h = make_user(session_factory, "s7_victim")
    join_meeting(client, victim_h, b_no)
    victim_pid = participant_id_of(session_factory, b_no, "s7_victim")

    count_a_before = get_meeting_row(session_factory, a_no).participant_count
    count_b_before = get_meeting_row(session_factory, b_no).participant_count

    with _open_ws(client, a_no, host_a_pid, host_a_tok) as ws:
        _sync(ws)
        ws.send_json({"type": "kick_user", "payload": {"target_id": victim_pid}})
        _sync(ws)

        # 必须在连接保持期内断言：退出 with 后主持人自身断开会把 A 会议计数减到 0（预期行为）
        victim = get_participant(session_factory, b_no, "s7_victim")
        assert victim.left_at is None, "B 会议成员不得被 A 会议主持人踢出"
        assert victim.status == "joined", f"状态被篡改为 {victim.status}"
        assert get_meeting_row(session_factory, b_no).participant_count == count_b_before
        assert get_meeting_row(session_factory, a_no).participant_count == count_a_before, \
            "A 会议计数不得被无关目标污染"


def test_cross_meeting_mute_is_ignored(client, session_factory):
    """攻击复现：A 会议主持人禁言 B 会议成员"""
    _, host_a_tok, host_a_h = make_user(session_factory, "s7_mute_hostA")
    meeting_a = create_meeting(client, host_a_h, title="禁言会议A")
    a_no = meeting_a["meeting_no"]
    host_a_pid = participant_id_of(session_factory, a_no, "s7_mute_hostA")

    _, _, host_b_h = make_user(session_factory, "s7_mute_hostB")
    b_no = create_meeting(client, host_b_h, title="禁言会议B")["meeting_no"]

    _, _, victim_h = make_user(session_factory, "s7_mute_victim")
    join_meeting(client, victim_h, b_no)
    victim_pid = participant_id_of(session_factory, b_no, "s7_mute_victim")

    with _open_ws(client, a_no, host_a_pid, host_a_tok) as ws:
        _sync(ws)
        ws.send_json({"type": "mute_user",
                      "payload": {"target_id": victim_pid, "mute_audio": True, "mute_chat": True}})
        _sync(ws)

    victim = get_participant(session_factory, b_no, "s7_mute_victim")
    assert victim.muted is False, "B 会议成员不得被 A 会议主持人禁麦"
    assert victim.chat_muted is False, "B 会议成员不得被 A 会议主持人禁聊"


def test_cross_meeting_host_transfer_is_ignored(client, session_factory):
    """攻击复现：A 会议主持人把 B 会议成员设为自己的主持人/联席主持"""
    host_a_id, host_a_tok, host_a_h = make_user(session_factory, "s7_th_hostA")
    meeting_a = create_meeting(client, host_a_h, title="转移会议A")
    a_no = meeting_a["meeting_no"]
    host_a_pid = participant_id_of(session_factory, a_no, "s7_th_hostA")

    _, _, host_b_h = make_user(session_factory, "s7_th_hostB")
    b_no = create_meeting(client, host_b_h, title="转移会议B")["meeting_no"]

    _, _, victim_h = make_user(session_factory, "s7_th_victim")
    join_meeting(client, victim_h, b_no)
    victim_pid = participant_id_of(session_factory, b_no, "s7_th_victim")

    with _open_ws(client, a_no, host_a_pid, host_a_tok) as ws:
        _sync(ws)
        ws.send_json({"type": "transfer_host", "payload": {"target_id": victim_pid}})
        _sync(ws)
        ws.send_json({"type": "set_co_host", "payload": {"target_id": victim_pid, "enabled": True}})
        _sync(ws)

    assert get_meeting_row(session_factory, a_no).host_id == host_a_id, "A 会议主持身份不得被转移给外会成员"
    assert get_meeting_row(session_factory, a_no).co_host_id is None, "外会成员不得成为联席主持"


def test_non_host_cannot_operate_members(client, session_factory):
    """攻击复现：普通成员冒充主持人踢人"""
    _, host_tok, host_h = make_user(session_factory, "s7_perm_host")
    no = create_meeting(client, host_h, title="权限会议")["meeting_no"]

    _, member_tok, member_h = make_user(session_factory, "s7_perm_member")
    join_meeting(client, member_h, no)
    member_pid = participant_id_of(session_factory, no, "s7_perm_member")

    _, _, target_h = make_user(session_factory, "s7_perm_target")
    join_meeting(client, target_h, no)
    target_pid = participant_id_of(session_factory, no, "s7_perm_target")

    with _open_ws(client, no, member_pid, member_tok) as ws:
        _sync(ws)
        ws.send_json({"type": "kick_user", "payload": {"target_id": target_pid}})
        _sync(ws)

        assert get_participant(session_factory, no, "s7_perm_target").left_at is None, \
            "普通成员不得踢人"
        assert get_meeting_row(session_factory, no).participant_count == 3, "计数不得被误减"


# ---------------- 正常场景（确认修复未误伤合法操作） ----------------

def test_same_meeting_kick_still_works(client, session_factory):
    """正常场景：主持人踢出本会议成员仍然生效"""
    _, host_tok, host_h = make_user(session_factory, "s7_ok_host")
    no = create_meeting(client, host_h, title="同会踢人")["meeting_no"]
    host_pid = participant_id_of(session_factory, no, "s7_ok_host")

    _, _, member_h = make_user(session_factory, "s7_ok_member")
    join_meeting(client, member_h, no)
    member_pid = participant_id_of(session_factory, no, "s7_ok_member")

    with _open_ws(client, no, host_pid, host_tok) as ws:
        _sync(ws)
        ws.send_json({"type": "kick_user", "payload": {"target_id": member_pid}})
        _sync(ws)

        row = get_participant(session_factory, no, "s7_ok_member")
        assert row.status == "kicked" and row.left_at is not None
        assert get_meeting_row(session_factory, no).participant_count == 1


def test_same_meeting_mute_still_works(client, session_factory):
    """正常场景：主持人禁言本会议成员仍然生效"""
    _, host_tok, host_h = make_user(session_factory, "s7_mk_host")
    no = create_meeting(client, host_h, title="同会禁言")["meeting_no"]
    host_pid = participant_id_of(session_factory, no, "s7_mk_host")

    _, _, member_h = make_user(session_factory, "s7_mk_member")
    join_meeting(client, member_h, no)
    member_pid = participant_id_of(session_factory, no, "s7_mk_member")

    with _open_ws(client, no, host_pid, host_tok) as ws:
        _sync(ws)
        ws.send_json({"type": "mute_user",
                      "payload": {"target_id": member_pid, "mute_chat": True}})
        _sync(ws)

    assert get_participant(session_factory, no, "s7_mk_member").chat_muted is True


# ---------------- 异常边界 ----------------

def test_invalid_target_id_does_not_break_connection(client, session_factory):
    """异常边界：不存在的 target_id 不得让连接崩溃（修复前会 AttributeError）"""
    _, host_tok, host_h = make_user(session_factory, "s7_bad_host")
    no = create_meeting(client, host_h, title="非法目标")["meeting_no"]
    host_pid = participant_id_of(session_factory, no, "s7_bad_host")

    with _open_ws(client, no, host_pid, host_tok) as ws:
        _sync(ws)
        ws.send_json({"type": "kick_user", "payload": {"target_id": 999999}})
        _sync(ws)  # 连接仍可用即为通过
        ws.send_json({"type": "mute_user", "payload": {"target_id": 888888, "mute_audio": True}})
        _sync(ws)
        ws.send_json({"type": "transfer_host", "payload": {"target_id": 777777}})
        _sync(ws)
        # 断言必须在连接保持期内进行：退出 with 后主持人自身断开会把计数减到 0
        assert get_meeting_row(session_factory, no).participant_count == 1


def test_kick_already_left_target_is_ignored(client, session_factory):
    """异常边界：重复踢同一目标不得重复扣减 participant_count"""
    _, host_tok, host_h = make_user(session_factory, "s7_dup_host")
    no = create_meeting(client, host_h, title="重复踢人")["meeting_no"]
    host_pid = participant_id_of(session_factory, no, "s7_dup_host")

    _, _, member_h = make_user(session_factory, "s7_dup_member")
    join_meeting(client, member_h, no)
    member_pid = participant_id_of(session_factory, no, "s7_dup_member")

    with _open_ws(client, no, host_pid, host_tok) as ws:
        _sync(ws)
        ws.send_json({"type": "kick_user", "payload": {"target_id": member_pid}})
        _sync(ws)
        ws.send_json({"type": "kick_user", "payload": {"target_id": member_pid}})
        _sync(ws)
        ws.send_json({"type": "kick_user", "payload": {"target_id": member_pid}})
        _sync(ws)

        assert get_meeting_row(session_factory, no).participant_count == 1, \
            "重复踢人不得把计数减成负数或多次扣减"


def test_two_meetings_online_interleaved_admin_actions(client, session_factory):
    """并发场景：两个会议同时在线，双方主持人交叉发起管理指令，互不影响"""
    _, a_tok, a_h = make_user(session_factory, "s7_dual_A")
    a_no = create_meeting(client, a_h, title="并存A")["meeting_no"]
    a_pid = participant_id_of(session_factory, a_no, "s7_dual_A")

    _, b_tok, b_h = make_user(session_factory, "s7_dual_B")
    b_no = create_meeting(client, b_h, title="并存B")["meeting_no"]
    b_pid = participant_id_of(session_factory, b_no, "s7_dual_B")

    with _open_ws(client, a_no, a_pid, a_tok) as ws_a, \
            _open_ws(client, b_no, b_pid, b_tok) as ws_b:
        _sync(ws_a)
        _sync(ws_b)
        # A 主持人踢 B 主持人；B 主持人禁言 A 主持人
        ws_a.send_json({"type": "kick_user", "payload": {"target_id": b_pid}})
        ws_b.send_json({"type": "mute_user", "payload": {"target_id": a_pid, "mute_audio": True}})
        _sync(ws_a)
        _sync(ws_b)

        assert get_participant(session_factory, b_no, "s7_dual_B").left_at is None
        assert get_participant(session_factory, a_no, "s7_dual_A").muted is False
        assert get_meeting_row(session_factory, a_no).participant_count == 1
        assert get_meeting_row(session_factory, b_no).participant_count == 1