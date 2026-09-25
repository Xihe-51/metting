"""
S14 participants_list 触发全量重建 PeerConnection —— 契约验证

【修复前的问题】
前端收到 `participants_list` 时无条件对名单中每个人调用 `createOfferForPeer`，
而 `createOfferForPeer` 又无条件 `new RTCPeerConnection` 并覆盖 Map 中的旧连接：
1. 旧连接从未 close → Map 只保留最后一个引用，形成 RTCPeerConnection 泄漏；
2. 已有连接的远端视频流被重新协商 → 画面闪断。
只要名单被刷新一次（主持人转移主持权 / 设置联席主持 / 禁言成员，服务端都会向
全员广播 participants_list），所有成员就会集体重建全部 P2P 连接。

【修复要点（前端）】
- `createOfferForPeer` 幂等化：该成员已有未关闭的连接时直接复用，绝不重建；
- `participants_list` 里只为「还没有连接」的新成员发 offer，并为已不在名单里的
  成员关闭连接。

【本文件验证的是前端修复所依赖的服务端契约】
前端之所以必须承受「同一条 participants_list 反复到达」，是因为服务端在名单
变更时会向全员广播它。这里把这个契约固定下来：
- 新成员入会：只有他自己收到 participants_list（初始名单），已在会成员只收到
  user_joined（不触发全量刷新）；
- 名单变更（禁言）：确实会向全员广播完整 participants_list —— 即客户端必须
  幂等，才能既同步名单又不闪断。

说明：仓库没有前端测试运行器（无 vitest），因此 P2P 连接生命周期的断言由本契约
测试 + 修复代码中的 `createOfferForPeer` 复用守卫共同保证。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s14_participants_list.py -v
"""
from contextlib import contextmanager

from conftest import create_meeting, join_meeting, make_user, participant_id_of  # noqa: E402


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
    """发送 ping 并读到 pong，返回此前收到的消息列表（同一连接内消息 FIFO）"""
    ws.send_json({"type": "ping"})
    seen = []
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == "pong":
            return seen
        seen.append(msg)
    raise AssertionError("未收到 pong，WebSocket 处理链路异常")


def _types(messages):
    return [m["type"] for m in messages]


def test_only_new_joiner_receives_participants_list(client, session_factory):
    """新成员入会时，只有他自己收到 participants_list，已在会成员只收到 user_joined"""
    host_token, host_h = _make(session_factory, "host_s14a")
    _, guest_token, guest_h = make_user(session_factory, "guest_s14a")
    meeting_no = create_meeting(client, host_h, title="名单契约")["meeting_no"]
    join_meeting(client, guest_h, meeting_no)

    host_pid = participant_id_of(session_factory, meeting_no, "host_s14a")
    guest_pid = participant_id_of(session_factory, meeting_no, "guest_s14a")

    with _open_ws(client, meeting_no, host_pid, host_token) as host_ws:
        _drain(host_ws)   # 丢弃主持人自身的入会消息

        with _open_ws(client, meeting_no, guest_pid, guest_token) as guest_ws:
            guest_seen = _drain(guest_ws)
            host_seen = _drain(host_ws)

    # 新成员：恰好一份完整名单
    guest_lists = [m for m in guest_seen if m["type"] == "participants_list"]
    assert len(guest_lists) == 1, _types(guest_seen)
    assert {p["id"] for p in guest_lists[0]["payload"]} == {host_pid, guest_pid}

    # 已在会成员：只收到 user_joined，没有被动收到名单刷新
    assert "user_joined" in _types(host_seen), _types(host_seen)
    assert "participants_list" not in _types(host_seen), \
        "入会不应让已在会成员被动收到 participants_list（否则会触发全量重建）"
    assert [m["payload"]["id"] for m in host_seen if m["type"] == "user_joined"] == [guest_pid]


def test_roster_change_pushes_full_participants_list(client, session_factory):
    """名单变更（禁言成员）会向全员广播完整 participants_list

    这就是「每次名单刷新都重建全部 PeerConnection」的触发源：
    服务端的语义是「一份完整名单」，客户端必须幂等复用已有连接才不会闪断。
    """
    host_token, host_h = _make(session_factory, "host_s14b")
    _, guest_token, guest_h = make_user(session_factory, "guest_s14b")
    meeting_no = create_meeting(client, host_h, title="名单广播")["meeting_no"]
    join_meeting(client, guest_h, meeting_no)

    host_pid = participant_id_of(session_factory, meeting_no, "host_s14b")
    guest_pid = participant_id_of(session_factory, meeting_no, "guest_s14b")

    with _open_ws(client, meeting_no, host_pid, host_token) as host_ws:
        _drain(host_ws)
        with _open_ws(client, meeting_no, guest_pid, guest_token) as guest_ws:
            _drain(guest_ws)
            _drain(host_ws)

            # 主持人禁言该成员
            host_ws.send_json({
                "type": "mute_user",
                "payload": {"target_id": guest_pid, "mute_audio": True}
            })
            host_seen = _drain(host_ws)
            guest_seen = _drain(guest_ws)

    assert "mute_status" in _types(guest_seen), _types(guest_seen)

    host_lists = [m for m in host_seen if m["type"] == "participants_list"]
    assert len(host_lists) == 1, _types(host_seen)
    assert {p["id"] for p in host_lists[0]["payload"]} == {host_pid, guest_pid}
    assert "participants_list" in _types(guest_seen), _types(guest_seen)


def _make(session_factory, name):
    """make_user 的简写：返回 (token, headers)（本文件不需要 user_id）"""
    _, token, headers = make_user(session_factory, name)
    return token, headers