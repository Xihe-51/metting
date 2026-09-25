"""
S1 SQLite 写锁 —— 并发写复现脚本 + 修复后验证

【修复前的问题】
app/database.py 创建引擎时只设了 check_same_thread=False，没有设置任何 PRAGMA：
- journal_mode 沿用默认的 delete（回滚日志），写事务期间读会被阻塞；
- 没有显式的 busy handler，写锁冲突时只能被动等待驱动的默认超时，
  遇到长事务 / 高并发就抛：
      sqlite3.OperationalError: database is locked
会议系统里「每发一条聊天消息 commit 一次」「录制分片上传」「参会人数更新」
三条写路径并发时最容易触发。

【修复要点】
app.database.create_db_engine() 统一为引擎注册：
- PRAGMA journal_mode=WAL    → 读写不再互相阻塞
- PRAGMA busy_timeout=5000   → 写锁冲突时最多重试等待 5s
- PRAGMA synchronous=NORMAL  → WAL 下安全且更快
- connect_args timeout=30    → 驱动层写锁等待上限

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s1_sqlite_lock.py -v
"""
import sqlite3
import threading

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.database import Base, _apply_sqlite_pragma, create_db_engine
from app.database import engine as app_engine
from app.models import ChatMessage, Meeting

WRITERS = 8              # 并发写线程数
WRITES_PER_THREAD = 25   # 每线程写入条数


def _prepare_db(engine):
    """建表 + 插入一个会议行，返回 meeting_id"""
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        meeting = Meeting(
            meeting_no="100001", title="并发写", creator_id=1,
            status="ongoing", participant_count=1,
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return meeting.id
    finally:
        db.close()


def _hold_write_lock(db_path, acquired, release, timeout=0):
    """在独立连接上持有写锁（BEGIN IMMEDIATE），直到 release 被设置

    用原始 sqlite3 连接，模拟「另一个连接/请求正在写库」。
    """
    con = sqlite3.connect(db_path, timeout=timeout, isolation_level=None,
                          check_same_thread=False)
    try:
        con.execute("BEGIN IMMEDIATE")   # 取得 RESERVED 写锁
        acquired.set()
        release.wait(5)
        con.execute("ROLLBACK")
    finally:
        con.close()


def _write_once(factory, meeting_id, worker_id, tag):
    """一次完整的写事务：提交一条聊天消息（与真实聊天路径一致）"""
    db = factory()
    try:
        db.add(ChatMessage(meeting_id=meeting_id, participant_id=worker_id,
                           display_name=f"u{worker_id}", content=tag))
        db.commit()
    finally:
        db.close()


def _hammer(factory, meeting_id, worker_id, barrier, errors, tag):
    barrier.wait()
    for i in range(WRITES_PER_THREAD):
        try:
            _write_once(factory, meeting_id, worker_id, f"msg-{i}")
        except Exception as exc:  # noqa: BLE001 - 需要把异常原文收集起来断言
            errors.append(f"{tag}: {exc}")


# ---------------- 修复代码本身的验证 ----------------

def test_fixed_engine_enables_wal_and_busy_timeout(tmp_path):
    """create_db_engine 必须真正把 WAL / busy_timeout / synchronous 落到连接上"""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'pragma.db'}")
    try:
        with engine.connect() as conn:
            assert str(conn.execute(text("PRAGMA journal_mode")).scalar()).lower() == "wal"
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() >= 5000
            assert conn.execute(text("PRAGMA synchronous")).scalar() == 1  # 1 = NORMAL
    finally:
        engine.dispose()


def test_app_engine_is_wired_with_pragma_listener():
    """应用引擎必须注册了 PRAGMA 监听器（否则生产库仍是修复前状态）"""
    assert event.contains(app_engine, "connect", _apply_sqlite_pragma)


# ---------------- 攻击/并发复现 ----------------

def test_negative_control_write_lock_error_without_busy_handler(tmp_path):
    """修复前配置复现：无 busy handler（timeout=0）时，写锁冲突直接抛 database is locked

    这里刻意不用 create_db_engine，构造出「修复前」的引擎配置，
    证明后面的正例不是空过。
    """
    db_path = str(tmp_path / "nofix.db")
    engine = create_engine(f"sqlite:///{db_path}",
                           connect_args={"check_same_thread": False, "timeout": 0})
    meeting_id = _prepare_db(engine)
    factory = sessionmaker(bind=engine)
    assert meeting_id

    acquired, release = threading.Event(), threading.Event()
    holder = threading.Thread(target=_hold_write_lock, args=(db_path, acquired, release, 0))
    holder.start()
    try:
        assert acquired.wait(2), "未能取得写锁"
        with pytest.raises(Exception) as ei:
            _write_once(factory, meeting_id, 1, "在锁持有期间写入")
        assert "locked" in str(ei.value).lower(), str(ei.value)
    finally:
        release.set()
        holder.join()
        engine.dispose()


def test_fixed_engine_waits_for_write_lock_instead_of_failing(tmp_path):
    """修复后：同样被别的连接占着写锁，写法应等待锁释放后成功，而不是抛错"""
    db_path = str(tmp_path / "fixed.db")
    engine = create_db_engine(f"sqlite:///{db_path}")
    meeting_id = _prepare_db(engine)
    factory = sessionmaker(bind=engine)

    acquired, release = threading.Event(), threading.Event()
    holder = threading.Thread(target=_hold_write_lock, args=(db_path, acquired, release, 0))
    holder.start()
    timer = None
    try:
        assert acquired.wait(2), "未能取得写锁"
        timer = threading.Timer(0.4, release.set)   # 0.4s 后释放写锁
        timer.start()
        _write_once(factory, meeting_id, 1, "等待锁释放后写入")  # 不应抛异常
    finally:
        if timer:
            timer.cancel()
        release.set()
        holder.join()
        db = factory()
        try:
            assert db.query(ChatMessage).filter(
                ChatMessage.meeting_id == meeting_id).count() == 1
        finally:
            db.close()
        engine.dispose()


def test_fixed_engine_concurrent_writers_all_succeed(tmp_path):
    """修复后：8 线程 × 25 次并发写全部成功，且条数精确（零失败、零丢失）"""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'concurrent.db'}")
    meeting_id = _prepare_db(engine)
    factory = sessionmaker(bind=engine)

    barrier = threading.Barrier(WRITERS)
    errors = []
    threads = [
        threading.Thread(target=_hammer,
                         args=(factory, meeting_id, i, barrier, errors, f"w{i}"))
        for i in range(WRITERS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], errors[:3]
    db = factory()
    try:
        assert db.query(ChatMessage).filter(
            ChatMessage.meeting_id == meeting_id).count() == WRITERS * WRITES_PER_THREAD
    finally:
        db.close()
        engine.dispose()