"""
数据库配置
SQLite + SQLAlchemy 2.0
"""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
import logging

logger = logging.getLogger("uvicorn.error")

# SQLite 数据库路径
SQLALCHEMY_DATABASE_URL = "sqlite:///./meeting.db"

# 创建引擎
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False}  # SQLite 需要这个配置
)

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
            ("created_at", "DATETIME", None),
        ],
        "verification_codes": [
            ("id", "INTEGER", None),
            ("email", "VARCHAR(64)", None),
            ("code", "VARCHAR(6)", None),
            ("expires_at", "DATETIME", None),
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