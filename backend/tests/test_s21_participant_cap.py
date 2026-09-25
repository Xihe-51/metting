"""
S21 —— 单会议总人数硬上限（Mesh 容量护栏）

【修复的问题】
Mesh 全互联下每人要维护「人数 − 1」条连接，上行带宽与 CPU 随人数快速增长：
16 人时每人已要维护 15 条连接、开视频者上行 = 15 × 单路码率。
此前系统对单会议人数没有任何硬上限，第 17、50 人照样能进，
会把所有人的音视频一起拖垮。

【修复方式】
MEETING_MAX_PARTICIPANTS（默认 16，可用环境变量覆盖），作用于会议总人数（含等候室）：
1. /join 新增入会：先做原子条件自增「占名额」
   （UPDATE ... WHERE participant_count < 上限），rowcount=0 即已满，返回 409。
   条件写进 UPDATE，由 SQLite 写锁串行化，并发入会不会双双挤进最后一个名额。
2. /join 断线重连：走同一套原子闸门 —— 重连同样重新占名额，
   否则「断开再重连」就是绕过上限的入口。
3. WebSocket 鉴权兜底：participant_count > 上限（只可能来自历史数据/异常路径）
   时拒绝建连；恰好等于上限必须放行（该参会者本身就在计数内，
   用 >= 判断会把正常成员挡在门外）。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s21_participant_cap.py -v
"""
from datetime import datetime

import pytest
from starlette.websockets import WebSocketDisconnect

from conftest import create_meeting, get_participant, make_user, participant_id_of  # noqa: E402
from app.config import (  # noqa: E402
    DEFAULT_MAX_PARTICIPANTS,
    InsecureConfigError,
    load_max_participants,
)
from app.models import Meeting, Participant  # noqa: E402


# ==================================================================
# 公共辅助
# ==================================================================

def _meeting_count(session_factory, meeting_no) -> int:
    """直连读当前会议人数（断言计数不漂移用）"""
    db = session_factory()
    try:
        meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
        return meeting.participant_count or 0
    finally:
        db.close()


def _set_count(session_factory, meeting_no, value: int) -> None:
    """人工改计数：用来制造「超编」这类正常接口不会产生的历史状态"""
    db = session_factory()
    try:
        meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
        meeting.participant_count = value
        db.commit()
    finally:
        db.close()


def _leave_participant(session_factory, meeting_no, username: str) -> None:
    """把某人置为已离会并自减计数（等价真实退会路径的落库结果）"""
    db = session_factory()
    try:
        row = db.query(Participant).join(Meeting, Participant.meeting_id == Meeting.id).filter(
            Meeting.meeting_no == meeting_no,
            Participant.display_name == username,
        ).order_by(Participant.id.desc()).first()
        assert row is not None, f"{username} 不在会议 {meeting_no} 中"
        row.status = "left"
        row.left_at = datetime.now()
        meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
        meeting.participant_count = max(0, (meeting.participant_count or 0) - 1)
        db.commit()
    finally:
        db.close()


def _join(client, headers, meeting_no, password=""):
    """直接调 /join 并原样返回响应（本模块要断言 409，不能用 conftest 的 join_meeting）"""
    return client.post(
        f"/api/v1/meetings/{meeting_no}/join",
        json={"password": password},
        headers=headers,
    )


def _authed_ws(client, session_factory, username):
    """建会议（创建者即参会者）并返回 (meeting_no, participant_id, token)"""
    _, token, headers = make_user(session_factory, username)
    meeting_no = create_meeting(client, headers, title="人数上限验证")["meeting_no"]
    pid = participant_id_of(session_factory, meeting_no, username)
    return meeting_no, pid, token


# ==================================================================
# 1. 配置项
# ==================================================================

def test_max_participants_default_is_sixteen(monkeypatch):
    """默认上限 16，且必须 ≥ 1"""
    monkeypatch.delenv("MEETING_MAX_PARTICIPANTS", raising=False)
    assert DEFAULT_MAX_PARTICIPANTS == 16
    assert load_max_participants() == DEFAULT_MAX_PARTICIPANTS


def test_max_participants_env_override(monkeypatch):
    """环境变量可覆盖人数上限"""
    monkeypatch.setenv("MEETING_MAX_PARTICIPANTS", "32")
    assert load_max_participants() == 32


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "3.5"])
def test_max_participants_rejects_invalid_value(monkeypatch, bad):
    """非法值必须直接报错，而不是静默退化成默认值

    尤其 0：会让任何人都进不了会议，而报错信息指向配置本身才能被修复。
    """
    monkeypatch.setenv("MEETING_MAX_PARTICIPANTS", bad)
    with pytest.raises(InsecureConfigError):
        load_max_participants()


# ==================================================================
# 2. /join 入会闸门
# ==================================================================

def test_join_rejected_when_capacity_full(client, session_factory, monkeypatch):
    """满员后第 N+1 人入会被 409 明确拒绝，且不产生参会记录、计数不漂移"""
    monkeypatch.setenv("MEETING_MAX_PARTICIPANTS", "2")
    _, _, h1 = make_user(session_factory, "cap_full_a")
    _, _, h2 = make_user(session_factory, "cap_full_b")
    _, _, h3 = make_user(session_factory, "cap_full_c")
    meeting_no = create_meeting(client, h1)["meeting_no"]  # 建会即占第 1 个名额

    assert _join(client, h2, meeting_no).status_code == 200
    assert _meeting_count(session_factory, meeting_no) == 2, "满员时计数应为上限值"

    resp = _join(client, h3, meeting_no)
    assert resp.status_code == 409, resp.text
    assert "人数已达上限" in resp.json()["message"]

    assert _meeting_count(session_factory, meeting_no) == 2, "被拒绝的入会不得改动计数"
    assert get_participant(session_factory, meeting_no, "cap_full_c") is None, "被拒绝者不得落库"


def test_reconnect_rejected_when_capacity_full(client, session_factory, monkeypatch):
    """断线重连同样受上限约束：不能成为「断开再重连」绕过上限的入口"""
    monkeypatch.setenv("MEETING_MAX_PARTICIPANTS", "2")
    _, _, h1 = make_user(session_factory, "cap_re_a")
    _, _, h2 = make_user(session_factory, "cap_re_b")
    _, _, h3 = make_user(session_factory, "cap_re_c")
    meeting_no = create_meeting(client, h1)["meeting_no"]
    assert _join(client, h2, meeting_no).status_code == 200

    # B 离会释放名额，C 补位后会议再次满员
    _leave_participant(session_factory, meeting_no, "cap_re_b")
    assert _meeting_count(session_factory, meeting_no) == 1
    assert _join(client, h3, meeting_no).status_code == 200
    assert _meeting_count(session_factory, meeting_no) == 2

    # B 重新入会走的是「断线重连」分支（left_at != None），必须同样被闸门拦下
    resp = _join(client, h2, meeting_no)
    assert resp.status_code == 409, resp.text
    assert "人数已达上限" in resp.json()["message"]
    assert _meeting_count(session_factory, meeting_no) == 2


def test_slot_release_allows_new_join(client, session_factory, monkeypatch):
    """有人离会后名额立即释放，新人入会不再被拒"""
    monkeypatch.setenv("MEETING_MAX_PARTICIPANTS", "2")
    _, _, h1 = make_user(session_factory, "cap_rel_a")
    _, _, h2 = make_user(session_factory, "cap_rel_b")
    _, _, h3 = make_user(session_factory, "cap_rel_c")
    meeting_no = create_meeting(client, h1)["meeting_no"]
    assert _join(client, h2, meeting_no).status_code == 200
    assert _join(client, h3, meeting_no).status_code == 409, "满员时必须先被拒绝"

    _leave_participant(session_factory, meeting_no, "cap_rel_b")
    assert _meeting_count(session_factory, meeting_no) == 1

    assert _join(client, h3, meeting_no).status_code == 200, "名额释放后应可正常入会"
    assert _meeting_count(session_factory, meeting_no) == 2
    assert get_participant(session_factory, meeting_no, "cap_rel_c") is not None


def test_waiting_room_members_count_toward_capacity(client, session_factory, monkeypatch):
    """等候室成员也占名额：上限约束的是「会议总人数」，不是「主会场人数」

    等候者同样占用 WebSocket 连接与后端资源，且随时可能被准入主会场，
    因此计入总数；否则「一直往等候室塞人」就成了绕过上限的路径。
    """
    monkeypatch.setenv("MEETING_MAX_PARTICIPANTS", "2")
    _, _, h1 = make_user(session_factory, "cap_wait_a")
    _, _, h2 = make_user(session_factory, "cap_wait_b")
    _, _, h3 = make_user(session_factory, "cap_wait_c")
    meeting_no = create_meeting(client, h1, waiting_room=True)["meeting_no"]

    resp = _join(client, h2, meeting_no)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "waiting", "等候室模式下新成员应进等候室"

    resp = _join(client, h3, meeting_no)
    assert resp.status_code == 409, resp.text
    assert "人数已达上限" in resp.json()["message"]


# ==================================================================
# 3. WebSocket 鉴权兜底
# ==================================================================

def test_ws_connect_rejected_for_over_capacity_meeting(client, session_factory, monkeypatch):
    """超编会议（只可能来自历史数据/异常路径）拒绝新建 WebSocket 连接"""
    monkeypatch.setenv("MEETING_MAX_PARTICIPANTS", "16")
    meeting_no, pid, token = _authed_ws(client, session_factory, "cap_ws_over")
    _set_count(session_factory, meeting_no, 17)  # 人为制造超编（> 16）

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_ws_connect_allowed_at_exact_capacity(client, session_factory, monkeypatch):
    """恰好等于上限必须放行：参会者本身就在计数内，用 >= 判断会误伤正常成员"""
    monkeypatch.setenv("MEETING_MAX_PARTICIPANTS", "1")
    meeting_no, pid, token = _authed_ws(client, session_factory, "cap_ws_equal")
    assert _meeting_count(session_factory, meeting_no) == 1, "建会者应恰好占满上限"

    with client.websocket_connect(f"/api/v1/ws/{meeting_no}?participant_id={pid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        # 用 ping/pong 做同步点：收到 pong 时，之前的入会消息必然已送达
        ws.send_json({"type": "ping"})
        types = []
        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "pong":
                break
            types.append(msg["type"])
        assert "participants_list" in types, f"恰好满员时必须能正常入会，实际收到：{types}"