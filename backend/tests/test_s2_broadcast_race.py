"""
S2 WebSocket 广播竞态 —— 复现脚本 + 修复后验证

【修复前的问题】
ConnectionManager.broadcast_to_meeting 直接遍历
    self.active_connections[meeting_no].items()
而循环体里有 `await websocket.send_json(...)`。await 挂起期间事件循环会切到别的协程，
此时若有新用户 connect（往同一个 dict 插入）或有人 disconnect（删除键），
下一次迭代立刻抛：
    RuntimeError: dictionary changed size during iteration
后果是「有人进出会议的那一刻，群聊 / 设备状态 / 名单广播整体炸掉」。
broadcast_to_host 遍历 participants_info 时是同类问题。

【修复要点】
遍历前先 list(...) 取快照，循环期间字典怎么增删都不影响本轮广播；
名单类方法 get_participants / get_waiting_participants 同步做了快照处理。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s2_broadcast_race.py -v
"""
import asyncio

import pytest

from app.websocket_manager import manager


class FakeWebSocket:
    """最小 WebSocket 替身

    send_json 记录收到的消息，并可在「发送过程中」回调去修改连接字典 ——
    精确模拟 await 挂起期间另一个协程执行 connect / disconnect。
    """

    def __init__(self, on_send=None):
        self.sent = []
        self._on_send = on_send

    async def send_json(self, message):
        self.sent.append(message)
        if self._on_send:
            self._on_send()


def test_negative_control_plain_iteration_raises_on_mutation():
    """对照：直接遍历 dict 且中途插入新键 —— 这正是修复前的崩溃方式"""
    conns = {1: "a", 2: "b", 3: "c"}
    with pytest.raises(RuntimeError, match="changed size during iteration"):
        for pid in conns:
            if pid == 1:
                conns[999] = "late"   # 模拟广播挂起期间新用户入会


def test_broadcast_to_meeting_survives_concurrent_join():
    """广播挂起期间有人入会：修复后本轮广播完整送达且不抛 RuntimeError"""
    room = "S2_RACE_JOIN"
    try:
        late = FakeWebSocket()
        # 第一个连接「发送时」往同一个 dict 插入新连接 —— 旧写法此处必崩
        ws1 = FakeWebSocket(on_send=lambda: manager.active_connections[room].__setitem__(99, late))
        ws2, ws3 = FakeWebSocket(), FakeWebSocket()
        manager.active_connections[room] = {1: ws1, 2: ws2, 3: ws3}

        asyncio.run(manager.broadcast_to_meeting(room, {"type": "chat"}))

        assert [len(w.sent) for w in (ws1, ws2, ws3)] == [1, 1, 1]
        assert late.sent == []                       # 本轮快照之外的新连接不被本轮波及
        assert 99 in manager.active_connections[room]  # 新连接保留，后续广播能覆盖
    finally:
        manager.active_connections.pop(room, None)


def test_broadcast_to_meeting_survives_concurrent_leave():
    """广播挂起期间有人退会（删除键）：修复后同样不崩，且不中断后续成员"""
    room = "S2_RACE_LEAVE"
    try:
        ws1 = FakeWebSocket(on_send=lambda: manager.active_connections[room].pop(3, None))
        ws2, ws3 = FakeWebSocket(), FakeWebSocket()
        manager.active_connections[room] = {1: ws1, 2: ws2, 3: ws3}

        asyncio.run(manager.broadcast_to_meeting(room, {"type": "chat"}))

        # 快照语义：本轮 3 个连接都送达（离开者本轮仍可能收到一次，属可接受）
        assert [len(w.sent) for w in (ws1, ws2, ws3)] == [1, 1, 1]
        assert 3 not in manager.active_connections[room]
    finally:
        manager.active_connections.pop(room, None)


def test_broadcast_to_host_tolerates_mutation_during_send():
    """主持人广播期间参会者表被修改：修复后仍能准确送达主持人且不抛异常

    注：该函数命中主持人后立即 break，单主持人场景下旧写法未必会崩；
    本用例把「快照遍历 + 仍然送达」这一行为锁定下来，防止后续重构回退。
    """
    room = "S2_RACE_HOST"
    try:
        host_ws = FakeWebSocket(
            on_send=lambda: manager.participants_info[room].__setitem__(
                88, {"name": "late", "is_host": False})
        )
        manager.participants_info[room] = {
            2: {"name": "guest", "is_host": False},
            1: {"name": "host", "is_host": True},
        }
        manager.active_connections[room] = {1: host_ws}

        asyncio.run(manager.broadcast_to_host(room, {"type": "waiting_participants"}))

        assert len(host_ws.sent) == 1
        assert host_ws.sent[0]["type"] == "waiting_participants"
        assert 88 in manager.participants_info[room]
    finally:
        manager.active_connections.pop(room, None)
        manager.participants_info.pop(room, None)