"""
数据库配置
SQLite + SQLAlchemy 2.0
"""
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
import logging

logger = logging.getLogger("uvicorn.error")

# SQLite 数据库路径
SQLALCHEMY_DATABASE_URL = "sqlite:///./meeting.db"


def _apply_sqlite_pragma(dbapi_connection, connection_record):
    """每条 SQLite 连接建立时设置并发相关 PRAGMA

    - journal_mode=WAL：写操作不再阻塞读，避免「发聊天消息 / 上传录制分片 /
      更新参会人数」同时落库时互相锁死；
    - busy_timeout=5000：写锁冲突时最多等待 5s 重试，而不是立刻抛
      "database is locked"；
    - synchronous=NORMAL：WAL 下的安全默认值，兼顾耐久性与写入速度。
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def create_db_engine(url: str):
    """统一创建 SQLite 引擎（应用引擎与测试引擎共用同一套并发配置）

    check_same_thread=False：SQLite 连接允许跨线程/协程复用；
    timeout=30：驱动层写锁等待上限，与 busy_timeout 一起兜住并发写。
    """
    eng = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    event.listens_for(eng, "connect")(_apply_sqlite_pragma)
    return eng


# 创建引擎
engine = create_db_engine(SQLALCHEMY_DATABASE_URL)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建基类
Base = declarative_base()


# 依赖注入：获取数据库会话
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def auto_migrate():
    """自动迁移：检测缺失的列并 ALTER TABLE ADD COLUMN"""
    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    # 定义所有表应有的列：{表名: [(列名, 类型, 默认值或None), ...]}
    expected = {
        "users": [
            ("id", "INTEGER", None),
            ("username", "VARCHAR(32)", None),
            ("password_hash", "VARCHAR(128)", None),
            ("display_name", "VARCHAR(64)", None),
            ("email", "VARCHAR(64)", None),
            ("phone", "VARCHAR(16)", None),
            ("avatar_url", "VARCHAR(256)", None),
            ("cover_url", "VARCHAR(256)", None),
            ("bio", "VARCHAR(256)", None),
            ("verified", "BOOLEAN", "0"),
            ("token_version", "INTEGER", "0"),
            ("created_at", "DATETIME", None),
        ],
        "verification_codes": [
            ("id", "INTEGER", None),
            ("email", "VARCHAR(64)", None),
            ("code", "VARCHAR(6)", None),
            ("expires_at", "DATETIME", None),
            ("attempts", "INTEGER", "0"),
            ("created_at", "DATETIME", None),
        ],
        "meetings": [
            ("id", "INTEGER", None),
            ("meeting_no", "VARCHAR(6)", None),
            ("title", "VARCHAR(128)", None),
            ("creator_id", "INTEGER", None),
            ("status", "VARCHAR(16)", None),
            ("password_hash", "VARCHAR(128)", None),
            ("scheduled_at", "DATETIME", None),
            ("waiting_room_enabled", "BOOLEAN", "0"),
            ("whitelist_enabled", "BOOLEAN", "0"),
            ("recording_permission", "VARCHAR(16)", "'host_only'"),
            ("participant_count", "INTEGER", None),
            ("created_at", "DATETIME", None),
            ("ended_at", "DATETIME", None),
            ("announcement", "TEXT", None),
            ("locked", "BOOLEAN", "0"),
            ("host_id", "INTEGER", None),
            ("co_host_id", "INTEGER", None),
        ],
        "participants": [
            ("id", "INTEGER", None),
            ("meeting_id", "INTEGER", None),
            ("user_id", "INTEGER", None),
            ("display_name", "VARCHAR(64)", None),
            ("status", "VARCHAR(16)", "'joined'"),
            ("admitted", "BOOLEAN", "0"),
            ("audio_on", "BOOLEAN", None),
            ("video_on", "BOOLEAN", None),
            ("sharing_screen", "BOOLEAN", None),
            ("muted", "BOOLEAN", "0"),
            ("chat_muted", "BOOLEAN", "0"),
            ("joined_at", "DATETIME", None),
            ("left_at", "DATETIME", None),
        ],
        "recordings": [
            ("id", "INTEGER", None),
            ("meeting_id", "INTEGER", None),
            ("file_name", "VARCHAR(255)", None),
            ("file_path", "VARCHAR(500)", None),
            ("file_size", "INTEGER", None),
            ("duration", "INTEGER", None),
            ("status", "VARCHAR(16)", None),
            ("started_at", "DATETIME", None),
            ("ended_at", "DATETIME", None),
        ],
        "meeting_whitelist": [
            ("id", "INTEGER", None),
            ("meeting_id", "INTEGER", None),
            ("user_id", "INTEGER", None),
            ("created_at", "DATETIME", None),
        ],
        "audit_logs": [
            ("id", "INTEGER", None),
            ("meeting_id", "INTEGER", None),
            ("user_id", "INTEGER", None),
            ("action", "VARCHAR(32)", None),
            ("details", "TEXT", None),
            ("created_at", "DATETIME", None),
        ],
        "reactions": [
            ("id", "INTEGER", None),
            ("meeting_id", "INTEGER", None),
            ("participant_id", "INTEGER", None),
            ("emoji", "VARCHAR(8)", None),
            ("created_at", "DATETIME", None),
        ],
        "chat_messages": [
            ("id", "INTEGER", None),
            ("meeting_id", "INTEGER", None),
            ("participant_id", "INTEGER", None),
            ("display_name", "VARCHAR(64)", None),
            ("content", "TEXT", None),
            ("is_whisper", "BOOLEAN", "0"),
            ("target_id", "INTEGER", None),
            ("recalled", "BOOLEAN", "0"),
            ("created_at", "DATETIME", None),
        ],
    }

    for table_name, columns in expected.items():
        if table_name not in table_names:
            continue  # 表不存在，create_all 会建

        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
        for col_name, col_type, default_val in columns:
            if col_name not in existing_cols:
                default_clause = f" DEFAULT {default_val}" if default_val else ""
                sql = f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}{default_clause}"
                try:
                    with engine.connect() as conn:
                        conn.execute(text(sql))
                        conn.commit()
                    logger.info(f"迁移: {table_name}.{col_name} 已添加")
                except Exception as e:
                    logger.warning(f"迁移失败: {table_name}.{col_name} — {e}")


def reconcile_ghost_participants(target_engine=None) -> dict:
    """启动对账：清理「幽灵在线」成员，并修正会议人数与状态

    进程重启（含 reload=True 下每次改代码触发的重启）会把 ConnectionManager 里的
    内存连接全部丢掉，但数据库仍停留在重启前：participants.left_at IS NULL、
    status='joined'，meetings.participant_count > 0。后果是
      - 名单/首页显示早已不在线的成员；
      - participant_count 永远大于 0，「人数归零自动结束」这条兜底永远不触发，
        会议一直挂在 ongoing。

    进程启动的瞬间必然没有任何活连接，因此这里把「未离会」的参会记录一律置为已离开，
    清零人数，并把 ongoing 的会议按既有的「在线人数归零」规则结束掉。

    返回 {"participants_left": n, "meetings_ended": m}，便于启动日志与测试断言。
    """
    eng = target_engine if target_engine is not None else engine
    # 显式格式化为字符串再绑定：避免依赖 sqlite3 已废弃的 datetime 隐式适配，
    # 格式与 SQLAlchemy 的 SQLite DATETIME 存储格式一致，ORM 读取时可正常解析。
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    with eng.connect() as conn:
        participants_left = conn.execute(text(
            "UPDATE participants SET left_at = :now, status = 'left' "
            "WHERE left_at IS NULL AND status IN ('joined', 'waiting')"
        ), {"now": now}).rowcount or 0

        conn.execute(text(
            "UPDATE meetings SET participant_count = 0 WHERE participant_count <> 0"
        ))

        meetings_ended = conn.execute(text(
            "UPDATE meetings SET status = 'ended', ended_at = COALESCE(ended_at, :now) "
            "WHERE status = 'ongoing'"
        ), {"now": now}).rowcount or 0

        conn.commit()

    if participants_left or meetings_ended:
        logger.warning(
            f"启动对账：{participants_left} 名「幽灵在线」成员已置为离会，"
            f"{meetings_ended} 场会议已按人数归零结束（进程重启后内存连接不可恢复）"
        )
    return {"participants_left": participants_left, "meetings_ended": meetings_ended}