"""SQLite 访问。单用户本地演示，用一把锁串行写。"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from threading import Lock

_lock = Lock()
_conn: sqlite3.Connection | None = None


def connect(database_path: Path, schema_path: Path) -> sqlite3.Connection:
    global _conn
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    _migrate_integer_user_ids(conn)
    schema = schema_path.read_text(encoding="utf-8")
    conn.executescript(schema)
    _copy_legacy_users(conn)
    _ensure_preference_columns(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(briefings)")}
    if "query" not in columns:
        conn.execute("ALTER TABLE briefings ADD COLUMN query TEXT")
    if "items" not in columns:
        conn.execute("ALTER TABLE briefings ADD COLUMN items TEXT NOT NULL DEFAULT '[]'")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    _conn = conn
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _id_is_integer(conn: sqlite3.Connection, table: str) -> bool:
    if not _table_exists(conn, table):
        return False
    for column in conn.execute(f"PRAGMA table_info({table})"):
        if column[1] == "id" and "INT" in str(column[2]).upper():
            return True
    return False


def _migrate_integer_user_ids(conn: sqlite3.Connection) -> None:
    """旧库的自增整数 id 改成随机 UUID，画像和简报一起迁过去。"""
    if not _id_is_integer(conn, "users"):
        return
    conn.execute("ALTER TABLE users RENAME TO users_old")
    if _table_exists(conn, "preferences"):
        conn.execute("ALTER TABLE preferences RENAME TO preferences_old")
    if _table_exists(conn, "briefings"):
        conn.execute("ALTER TABLE briefings RENAME TO briefings_old")
    if _table_exists(conn, "agent_steps"):
        conn.execute("ALTER TABLE agent_steps RENAME TO agent_steps_old")


def _copy_legacy_users(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "users_old"):
        return
    mapping: dict[int, str] = {}
    for row in conn.execute("SELECT id, name, email, created_at FROM users_old"):
        new_id = str(uuid.uuid4())
        mapping[row[0]] = new_id
        conn.execute(
            "INSERT INTO users (id, legacy_id, name, email, created_at) VALUES (?, ?, ?, ?, ?)",
            (new_id, row[0], row[1], row[2], row[3]),
        )
    if _table_exists(conn, "preferences_old"):
        for row in conn.execute("SELECT * FROM preferences_old"):
            keys = row.keys()
            user_id = mapping.get(row["user_id"])
            if user_id is None:
                continue
            conn.execute(
                """
                INSERT INTO preferences
                    (user_id, topics, keywords, language, recall_count, custom_prompt, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    row["topics"],
                    row["keywords"],
                    row["language"],
                    row["recall_count"] if "recall_count" in keys and row["recall_count"] else 5,
                    row["custom_prompt"] if "custom_prompt" in keys and row["custom_prompt"] else "",
                    row["updated_at"],
                ),
            )
    if _table_exists(conn, "briefings_old"):
        for row in conn.execute("SELECT * FROM briefings_old"):
            user_id = mapping.get(row["user_id"])
            if user_id is None:
                continue
            keys = row.keys()
            conn.execute(
                """
                INSERT INTO briefings
                    (id, user_id, title, content, summary, query, items, status, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    user_id,
                    row["title"],
                    row["content"],
                    row["summary"],
                    row["query"] if "query" in keys else "",
                    row["items"] if "items" in keys and row["items"] else "[]",
                    row["status"],
                    row["error"],
                    row["created_at"],
                ),
            )
    if _table_exists(conn, "agent_steps_old"):
        for row in conn.execute("SELECT * FROM agent_steps_old"):
            conn.execute(
                """
                INSERT INTO agent_steps
                    (id, briefing_id, step_index, tool_name, arguments, result, ok, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["briefing_id"],
                    row["step_index"],
                    row["tool_name"],
                    row["arguments"],
                    row["result"],
                    row["ok"],
                    row["created_at"],
                ),
            )
        conn.execute("DROP TABLE IF EXISTS agent_steps_old")
    conn.execute("DROP TABLE IF EXISTS briefings_old")
    conn.execute("DROP TABLE IF EXISTS preferences_old")
    conn.execute("DROP TABLE IF EXISTS users_old")


def _ensure_preference_columns(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "preferences"):
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(preferences)")}
    if "recall_count" not in columns:
        conn.execute("ALTER TABLE preferences ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 5")
    if "custom_prompt" not in columns:
        conn.execute("ALTER TABLE preferences ADD COLUMN custom_prompt TEXT NOT NULL DEFAULT ''")
    if "preferred_sites" not in columns:
        conn.execute("ALTER TABLE preferences ADD COLUMN preferred_sites TEXT NOT NULL DEFAULT '[]'")
    if "site_hits" not in columns:
        conn.execute("ALTER TABLE preferences ADD COLUMN site_hits TEXT NOT NULL DEFAULT '{}'")
    if "search_count" not in columns:
        conn.execute("ALTER TABLE preferences ADD COLUMN search_count INTEGER NOT NULL DEFAULT 0")


def get_conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("数据库尚未初始化")
    return _conn


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        cur = get_conn().execute(sql, params)
        get_conn().commit()
        return cur


def query_all(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return list(get_conn().execute(sql, params).fetchall())


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    with _lock:
        return get_conn().execute(sql, params).fetchone()
