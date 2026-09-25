"""
S3 参会人数丢失更新 —— 并发 join 复现脚本 + 修复后验证

【修复前的问题】
/join、断线重连、踢人、拒绝、主动退出 都用「先读后写」更新 participant_count：
    meeting.participant_count = (meeting.participant_count or 0) + 1
多个请求并发进入时读到同一个旧值，各自 +1 再写回，最终只 +1：
10 个人同时加入，在线人数可能只涨了 1~2。人数偏小还会让
「人数归零 → 自动结束会议」被提前触发。

【修复要点】
改为数据库层原子更新，杜绝读—改—写窗口：
    UPDATE meetings SET participant_count = participant_count + 1 WHERE id = ?
自减统一带 `participant_count > 0` 守卫，并在 commit 后重新查询判断是否归零。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s3_participant_count.py -v
"""
import threading
import time
from contextlib import contextmanager

import pytest

from conftest import (  # noqa: E402
    create_meeting,
    get_meeting_row,
    get_participant,
    join_meeting,
    make_user,
    participant_id_of,
)
from app.models import Meeting, Participant  # noqa: E402

MEETING_NO = "200001"
JOINERS = 12


def _make_meeting(session_factory, count=1):
    """直接建一个会议行，返回 meeting_id"""
    db = session_factory()
    try:
        meeting = Meeting(meeting_no=MEETING_NO, title="计数并发", creator_id=1,
                          status="ongoing", participant_count=count)
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return meeting.id
    finally:
        db.close()


def _count(session_factory, meeting_id):
    """参会记录条数（人数的一致性参照物）"""
    db = session_factory()
    try:
        return db.query(Participant).filter(Participant.meeting_id == meeting_id).count()
    finally:
        db.close()


def _join_atomic(session_factory, meeting_id, worker_id, barrier, errors):
    """修复后的写法：插入参会记录 + 原子 SQL 自增，同一事务提交"""
    barrier.wait()
    db = session_factory()
    try:
        db.add(Participant(meeting_id=meeting_id, user_id=1000 + worker_id,
                           display_name=f"u{worker_id}", status="joined",
                           admitted=True, audio_on=True, video_on=True))
        db.query(Meeting).filter(Meeting.id == meeting_id).update(
            {"participant_count": Meeting.participant_count + 1},
            synchronize_session=False,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    finally:
        db.close()


def _join_legacy(session_factory, meeting_id, worker_id, barrier, errors):
    """修复前的写法：先读出来、再 +1 写回（刻意放大读—写窗口）"""
    barrier.wait()
    db = session_factory()
    try:
        db.add(Participant(meeting_id=meeting_id, user_id=1000 + worker_id,
                           display_name=f"u{worker_id}", status="joined",
                           admitted=True, audio_on=True, video_on=True))
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        current = meeting.participant_count or 0
        time.sleep(0.02)   # 放大「读到旧值 → 写回」之间的竞态窗口
        meeting.participant_count = current + 1
        db.commit()
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    finally:
        db.close()


def _run_joiners(worker, session_factory, meeting_id):
    barrier = threading.Barrier(JOINERS)
    errors = []
    threads = [
        threading.Thread(target=worker,
                         args=(session_factory, meeting_id, i, barrier, errors))
        for i in range(JOINERS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


# ---------------- 并发复现 + 修复后验证 ----------------

def test_negative_control_read_modify_write_loses_updates(session_factory):
    """修复前写法复现：12 个并发 join 只涨了 1，人数远小于真实到场人数"""
    meeting_id = _make_meeting(session_factory, count=1)

    errors = _run_joiners(_join_legacy, session_factory, meeting_id)

    assert errors == [], errors[:3]          # 不是数据库报错，而是纯逻辑丢更新
    assert _count(session_factory, meeting_id) == JOINERS       # 参会记录一条不少
    final = get_meeting_row(session_factory, MEETING_NO).participant_count
    assert final < 1 + JOINERS, f"旧写法本应丢更新，实际 count={final}"


def test_atomic_increment_no_lost_update(session_factory):
    """修复后：12 个并发 join 后人数精确等于真实到场人数（零丢失）"""
    meeting_id = _make_meeting(session_factory, count=1)

    errors = _run_joiners(_join_atomic, session_factory, meeting_id)

    assert errors == [], errors[:3]
    assert _count(session_factory, meeting_id) == JOINERS
    assert get_meeting_row(session_factory, MEETING_NO).participant_count == 1 + JOINERS


def test_atomic_decrement_guard_stops_at_zero(session_factory):
    """负数守卫：人数已是 0 时再执行原子自减，不会变成 -1"""
    meeting_id = _make_meeting(session_factory, count=0)

    db = session_factory()
    try:
        db.query(Meeting).filter(
            Meeting.id == meeting_id,
            Meeting.participant_count > 0,      # 与业务代码一致的守卫条件
        ).update({"participant_count": Meeting.participant_count - 1},
                 synchronize_session=False)
        db.commit()
    finally:
        db.close()

    assert get_meeting_row(session_factory, MEETING_NO).participant_count == 0


# ---------------- 接口层回归（确认原子改写没破坏正常流程） ----------------

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


def test_join_reconnect_count_consistency(client, session_factory):
    """顺序流转下人数始终一致：新加入 +1 / 断线 -1 / 重连 +1"""
    _, _, host_h = make_user(session_factory, "host_s3")
    _, guest_token, guest_h = make_user(session_factory, "guest_s3")

    data = create_meeting(client, host_h, title="计数回归")
    meeting_no = data["meeting_no"]
    assert data["participant_count"] == 1

    join_meeting(client, guest_h, meeting_no)
    assert get_meeting_row(session_factory, meeting_no).participant_count == 2

    guest_id = participant_id_of(session_factory, meeting_no, "guest_s3")

    # 建立后立刻断开 → 断线处理器把人数减回 1
    with _open_ws(client, meeting_no, guest_id, guest_token) as ws:
        _drain(ws)
        assert get_meeting_row(session_factory, meeting_no).participant_count == 2
    assert _wait_until(
        lambda: get_meeting_row(session_factory, meeting_no).participant_count == 1
    ), "断线后人数未回落到 1"

    # 断线重连（走 /join 的 reconnecting 分支）→ 原子自增回 2
    again = join_meeting(client, guest_h, meeting_no)
    assert again["status"] == "joined"
    assert get_meeting_row(session_factory, meeting_no).participant_count == 2


def test_leave_to_zero_ends_meeting(client, session_factory):
    """回归：单人会议主动退出后人数归零，会议自动结束"""
    _, token, headers = make_user(session_factory, "solo_s3")
    data = create_meeting(client, headers, title="单人会议")
    meeting_no = data["meeting_no"]
    pid = participant_id_of(session_factory, meeting_no, "solo_s3")

    with _open_ws(client, meeting_no, pid, token) as ws:
        _drain(ws)
        assert get_meeting_row(session_factory, meeting_no).participant_count == 1
        ws.send_json({"type": "leave_meeting"})
        assert _wait_until(
            lambda: (get_meeting_row(session_factory, meeting_no).participant_count or 0) == 0
        ), "主动退出后人数未归零"
        row = get_meeting_row(session_factory, meeting_no)
        assert row.participant_count == 0
        assert row.status == "ended"
        assert get_participant(session_factory, meeting_no, "solo_s3").status == "left"