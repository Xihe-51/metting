"""
S20 —— Mesh（全互联）加固：ICE 分桶限流 + 视频席位仲裁

【修复的三处问题】
1. ICE 候选限流只有一个「连接级」桶（ws:{会议号}:{参会者}:ice）。
   Mesh 下每人同时维护 N-1 条连接，16 人规模下仅正常 trickle 就可能突破
   100/10s：候选人被丢弃会卡住协商，连续超限还会触发 1008，把整条连接
   （也就是这个参会者）踢出会议。
   修复：按对端分桶（ice:{target_id}），且只对会议内在册成员分桶，
   避免伪造 target_id 制造海量限流键。

2. 视频席位仲裁缺失。Mesh 下每人的上行 = 「同时开摄像头人数 − 1」路独立编码，
   席位不设上限时 16 人全开摄像头会把每个人的上行带宽与 CPU 一起打满。
   修复：后端统一仲裁（入会 + device 变更两条路径），
   超限回 video_denied，前端据此回滚为关闭状态。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s20_mesh_seats.py -v
"""
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from conftest import create_meeting, make_user, participant_id_of  # noqa: E402
from app import rate_limit  # noqa: E402
from app.config import (  # noqa: E402
    DEFAULT_VIDEO_SEAT_LIMIT,
    InsecureConfigError,
    load_video_seat_limit,
)
from app.websocket_manager import manager  # noqa: E402
from app.routers import meeting_router  # noqa: E402


# ==================================================================
# 公共辅助
# ==================================================================

def _authed_ws(client, session_factory, username):
    """建会议（创建者即参会者）并返回 (meeting_no, participant_id, token)"""
    _, token, headers = make_user(session_factory, username)
    meeting_no = create_meeting(client, headers, title="Mesh 加固验证")["meeting_no"]
    pid = participant_id_of(session_factory, meeting_no, username)
    return meeting_no, pid, token


def _inject_phantom(meeting_no: str, pid: int, video_on: bool = False) -> None:
    """注入一个假的在线成员，制造「会议里已经有人」的场景

    连接对象用 None：ConnectionManager 的所有发送路径都对异常做了容错
    （broadcast_to_meeting 捕获异常，send_personal_message 对 None 直接返回），
    因此假成员只会「吃掉」消息，不会影响被测行为。
    """
    manager.active_connections.setdefault(meeting_no, {})[pid] = None
    manager.participants_info.setdefault(meeting_no, {})[pid] = {
        "name": f"phantom{pid}",
        "audio_on": True,
        "video_on": video_on,
        "sharing_screen": False,
        "is_host": False,
        "muted": False,
        "chat_muted": False,
        "status": "joined",
        "avatar_url": "",
    }


def _sync(ws, limit: int = 40):
    """发一条 ping 并读到 pong，返回本次往返中 pong 之前的全部消息

    服务端对同一连接按序处理消息，因此收到 pong 时，之前发出的
    video_denied / device / rate_limited 等必然已经送达。
    用「已知必定有回包」的 ping 做同步点，可以确定性地收消息，
    不会因为「期待条数多于实际条数」而永久阻塞在 receive_json 上。
    """
    ws.send_json({"type": "ping"})
    msgs = []
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == "pong":
            return msgs
        msgs.append(msg)
    raise AssertionError(f"限定 {limit} 条内未读到 pong，服务端消息循环可能已中断")


def _read_until_close(ws, wanted: str, limit: int = 20) -> bool:
    """读到指定类型、或连接被服务端关闭为止

    退会路径服务端会 return 并关闭连接，读不到消息时必须靠 WebSocketDisconnect
    退出，否则会永久阻塞在 receive_json 上。
    """
    for _ in range(limit):
        try:
            if ws.receive_json()["type"] == wanted:
                return True
        except WebSocketDisconnect:
            return False
    return False


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.02) -> bool:
    """轮询等待条件成立

    客户端主动断开后，服务端的收尾（清理连接、回收限流键）是在另一个
    线程里异步执行的：客户端退出 with 块时服务端可能还没观察到断开事件，
    因此断言前必须给一小段等待窗口，否则会误报「未回收」。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _type_counts(msgs):
    counts = {}
    for m in msgs:
        counts[m["type"]] = counts.get(m["type"], 0) + 1
    return counts


# ==================================================================
# 1. 视频席位上限配置
# ==================================================================

def test_video_seat_limit_default_is_four(monkeypatch):
    """默认席位上限为 4，且必须 ≥ 1"""
    monkeypatch.delenv("MEETING_VIDEO_SEAT_LIMIT", raising=False)
    assert DEFAULT_VIDEO_SEAT_LIMIT == 4
    assert load_video_seat_limit() == DEFAULT_VIDEO_SEAT_LIMIT


def test_video_seat_limit_env_override(monkeypatch):
    """环境变量可覆盖席位上限（16 人会议按需调整）"""
    monkeypatch.setenv("MEETING_VIDEO_SEAT_LIMIT", "6")
    assert load_video_seat_limit() == 6


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "3.5"])
def test_video_seat_limit_rejects_invalid_value(monkeypatch, bad):
    """非法值必须直接报错，而不是静默退化成默认值

    尤其 0：会让任何人都开不了摄像头，且前端显示「席位已满」却无人占用。
    """
    monkeypatch.setenv("MEETING_VIDEO_SEAT_LIMIT", bad)
    with pytest.raises(InsecureConfigError):
        load_video_seat_limit()


# ==================================================================
# 2. 席位统计（ConnectionManager）
# ==================================================================

def test_count_video_on_only_counts_active_members():
    """席位统计只算「已准入且在开会视频」的人，等候室用户不占席位"""
    meeting_no = "SEAT01"
    _inject_phantom(meeting_no, 1, video_on=True)
    _inject_phantom(meeting_no, 2, video_on=False)
    _inject_phantom(meeting_no, 3, video_on=True)
    # 等候室里的连接不在 active 池，即便标记了 video_on 也不该占席位
    manager.waiting_connections.setdefault(meeting_no, {})[4] = None
    manager.participants_info[meeting_no][4] = {"name": "waiting", "video_on": True, "status": "waiting"}

    try:
        assert manager.count_video_on(meeting_no) == 2
        assert manager.is_video_on(meeting_no, 1) is True
        assert manager.is_video_on(meeting_no, 2) is False
        assert manager.is_active(meeting_no, 1) is True
        assert manager.is_active(meeting_no, 4) is False, "等候室用户不算在会议内"
    finally:
        manager.active_connections.pop(meeting_no, None)
        manager.waiting_connections.pop(meeting_no, None)
        manager.participants_info.pop(meeting_no, None)


# ==================================================================
# 3. 入会即开摄像头也要过席位仲裁
# ==================================================================

def test_join_is_downgraded_when_video_seats_full(client, session_factory, monkeypatch):
    """席位已满时，以「默认开摄像头」入会的人会被降级为关视频并收到 video_denied

    这是席位制最容易失效的地方：若只在 device 变更时仲裁，
    所有人都会在入会瞬间把席位占满，上限形同虚设。
    """
    monkeypatch.setenv("MEETING_VIDEO_SEAT_LIMIT", "1")
    meeting_no, pid, token = _authed_ws(client, session_factory, "seat_full_s20a")
    # 会议里已经有一位开着摄像头的成员，席位已被占满
    _inject_phantom(meeting_no, 999001, video_on=True)

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        msgs = _sync(ws)
        types = [m["type"] for m in msgs]

        assert "video_denied" in types, f"席位已满时必须回 video_denied，实际收到：{types}"
        denied = [m for m in msgs if m["type"] == "video_denied"][0]
        assert denied["payload"]["limit"] == 1

        roster = [m for m in msgs if m["type"] == "participants_list"][0]["payload"]
        me = [p for p in roster if p["id"] == pid]
        assert me and me[0]["video_on"] is False, "被降级者必须以关视频状态出现在名单里"

    # 数据库里的状态同样要落成关闭，否则重连时会再次误判为「已占席位」
    from conftest import get_participant
    assert get_participant(session_factory, meeting_no, "seat_full_s20a").video_on is False


def test_video_seat_upgrade_is_denied_while_full(client, session_factory, monkeypatch):
    """已有席位时点开摄像头：服务端拒绝，且不广播 device（状态不被篡改）"""
    monkeypatch.setenv("MEETING_VIDEO_SEAT_LIMIT", "1")
    meeting_no, pid, token = _authed_ws(client, session_factory, "seat_deny_s20b")
    _inject_phantom(meeting_no, 999002, video_on=True)

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        join_msgs = _sync(ws)
        assert "video_denied" in [m["type"] for m in join_msgs], "入会时就应该已被降级"

        # 申请开启摄像头 → 应被拒绝
        ws.send_json({"type": "device", "payload": {"audio_on": True, "video_on": True}})
        counts = _type_counts(_sync(ws))

        assert counts.get("video_denied", 0) == 1, f"应回一次 video_denied，实际：{counts}"
        assert counts.get("device", 0) == 0, "被拒绝时不得广播 device，否则前端与后端状态会不一致"
        assert manager.is_video_on(meeting_no, pid) is False


def test_video_seat_can_be_acquired_after_release(client, session_factory, monkeypatch):
    """有人释放席位后，原本被拒的人可以正常开摄像头"""
    monkeypatch.setenv("MEETING_VIDEO_SEAT_LIMIT", "1")
    meeting_no, pid, token = _authed_ws(client, session_factory, "seat_release_s20c")
    _inject_phantom(meeting_no, 999003, video_on=True)

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert "video_denied" in [m["type"] for m in _sync(ws)]

        # 占位者离开（席位释放）
        manager.active_connections[meeting_no].pop(999003, None)

        ws.send_json({"type": "device", "payload": {"audio_on": True, "video_on": True}})
        msgs = _sync(ws)
        assert "device" in [m["type"] for m in msgs], "席位空出后应放行并广播 device"
        device = [m for m in msgs if m["type"] == "device"][0]
        assert device["payload"]["video_on"] is True
        assert manager.is_video_on(meeting_no, pid) is True


def test_leaving_meeting_releases_video_seat(client, session_factory, monkeypatch):
    """主动退会必须释放席位，否则会议里其他人都将永远开不了摄像头"""
    monkeypatch.setenv("MEETING_VIDEO_SEAT_LIMIT", "1")
    meeting_no, pid, token = _authed_ws(client, session_factory, "seat_leave_s20d")

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        _sync(ws)
        assert manager.count_video_on(meeting_no) == 1, "入会即开摄像头应占一个席位"

        ws.send_json({"type": "leave_meeting", "payload": {}})
        assert _read_until_close(ws, "user_left"), "退会应广播 user_left"

    # 退会收尾同样在服务端异步执行，断言前等它落地
    assert _wait_until(lambda: manager.count_video_on(meeting_no) == 0), \
        "退会后席位必须释放"


# ==================================================================
# 4. ICE 候选限流按对端分桶
# ==================================================================

def _ice_keys(meeting_no: str, pid: int):
    """取出该连接当前的 ICE 限流键（rate_limit 未提供只读接口，测试直接看内部表）"""
    prefix = f"ws:{meeting_no}:{pid}:ice"
    return sorted(k for k in rate_limit._buckets if k.startswith(prefix))


def test_ice_candidates_are_rate_limited_per_peer(client, session_factory):
    """同一连接的 ICE 候选按对端分别计数，不再共用单一连接级桶

    修复前所有对端共用一个桶：16 人会议（15 条连接）时，
    仅正常 trickle 就可能突破 100/10s，导致候选人被丢弃甚至连接被踢。
    """
    meeting_no, pid, token = _authed_ws(client, session_factory, "ice_bucket_s20e")
    peer_a, peer_b = 999101, 999102
    _inject_phantom(meeting_no, peer_a)
    _inject_phantom(meeting_no, peer_b)

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        _sync(ws)

        for target in (peer_a, peer_a, peer_b):
            ws.send_json({"type": "ice", "payload": {
                "target_id": target,
                "candidate": {"candidate": "candidate:1 1 udp 1 127.0.0.1 1 typ host"},
            }})
        _sync(ws)  # ping 回来即保证三条 ICE 都已处理完

    keys = _ice_keys(meeting_no, pid)
    assert f"ws:{meeting_no}:{pid}:ice:{peer_a}" in keys, "应按对端分桶"
    assert f"ws:{meeting_no}:{pid}:ice:{peer_b}" in keys, "不同对端必须各用一个桶"
    assert f"ws:{meeting_no}:{pid}:ice" not in keys, "不得再共用单一连接级 ICE 桶"
    # A 收到 2 条、B 收到 1 条：分桶后计数互不影响
    assert len(rate_limit._buckets[f"ws:{meeting_no}:{pid}:ice:{peer_a}"]) == 2
    assert len(rate_limit._buckets[f"ws:{meeting_no}:{pid}:ice:{peer_b}"]) == 1


def test_ice_bucket_does_not_accept_fake_target(client, session_factory):
    """伪造 target_id 不得制造新的限流键（否则可以用假目标把内存撑爆）"""
    meeting_no, pid, token = _authed_ws(client, session_factory, "ice_fake_s20f")
    fake_target = 999999  # 不在会议内

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        _sync(ws)

        ws.send_json({"type": "ice", "payload": {
            "target_id": fake_target,
            "candidate": {"candidate": "candidate:1 1 udp 1 127.0.0.1 1 typ host"},
        }})
        _sync(ws)

    keys = _ice_keys(meeting_no, pid)
    assert f"ws:{meeting_no}:{pid}:ice:{fake_target}" not in keys
    # 落入兜底桶，仍然受限流保护
    assert f"ws:{meeting_no}:{pid}:ice" in keys


def test_ice_over_limit_on_one_peer_is_denied(client, session_factory, monkeypatch):
    """单对端超限只影响该对端：与它对应的候选被丢弃并回 rate_limited"""
    meeting_no, pid, token = _authed_ws(client, session_factory, "ice_limit_s20g")
    peer_a = 999201
    _inject_phantom(meeting_no, peer_a)
    # 收紧 ICE 配额，便于在小流量下复现超限
    monkeypatch.setitem(meeting_router.WS_RATE_LIMITS, "ice", (2, 10.0))

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        _sync(ws)

        for _ in range(3):
            ws.send_json({"type": "ice", "payload": {
                "target_id": peer_a,
                "candidate": {"candidate": "candidate:1 1 udp 1 127.0.0.1 1 typ host"},
            }})
        msgs = _sync(ws)

    types = [m["type"] for m in msgs]
    assert "rate_limited" in types, f"超出单对端配额应回 rate_limited，实际：{types}"
    limited = [m for m in msgs if m["type"] == "rate_limited"][0]
    assert limited["payload"]["rejected_type"] == "ice"
    assert len(rate_limit._buckets[f"ws:{meeting_no}:{pid}:ice:{peer_a}"]) == 2, "超限的候选不得计入"


# ==================================================================
# 5. 席位键随连接回收（避免限流表随参会记录增长）
# ==================================================================

def test_ice_bucket_keys_are_released_on_disconnect(client, session_factory):
    """ICE 分桶后键会变多，断开连接时必须按前缀全部回收"""
    meeting_no, pid, token = _authed_ws(client, session_factory, "ice_cleanup_s20h")
    _inject_phantom(meeting_no, 999301)

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        _sync(ws)
        ws.send_json({"type": "ice", "payload": {
            "target_id": 999301,
            "candidate": {"candidate": "candidate:1 1 udp 1 127.0.0.1 1 typ host"},
        }})
        _sync(ws)
        assert _ice_keys(meeting_no, pid), "发送后应存在 ICE 限流键"

    # 连接上下文退出即断开：收尾流程会按前缀回收全部相关键
    assert _wait_until(lambda: _ice_keys(meeting_no, pid) == []), \
        f"断开后限流键未回收：{_ice_keys(meeting_no, pid)}"