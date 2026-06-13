import sqlite3
import hashlib
import os
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "werewolf.db")


@contextmanager
def _connect():
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    try:
        yield con
    except Exception:
        con.rollback()
        raise
    else:
        con.commit()
    finally:
        con.close()


def init_db():
    with _connect() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=5000")
        con.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                username   TEXT PRIMARY KEY,
                password   TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS sessions (
                username     TEXT PRIMARY KEY,
                room_code    TEXT,
                is_connected INTEGER DEFAULT 0,
                last_seen    TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (username) REFERENCES users(username)
            );
        """)
        # Clear all stale sessions on every server startup so no one gets
        # locked out due to a previous crash leaving is_connected=1.
        con.execute("UPDATE sessions SET is_connected = 0, room_code = ''")


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def register_user(username: str, password: str) -> tuple[bool, str]:
    """Returns (ok, message)."""
    try:
        with _connect() as con:
            con.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, _hash(password))
            )
        return True, "OK"
    except sqlite3.IntegrityError:
        return False, "Username already taken"


def login_user(username: str, password: str) -> tuple[bool, str]:
    """Returns (ok, message). Does NOT update session — caller must call set_connected."""
    with _connect() as con:
        row = con.execute(
            "SELECT password FROM users WHERE username = ?", (username,)
        ).fetchone()
    if not row:
        return False, "Username not found"
    if row[0] != _hash(password):
        return False, "Wrong password"
    return True, "OK"


def is_online(username: str) -> bool:
    with _connect() as con:
        row = con.execute(
            "SELECT is_connected FROM sessions WHERE username = ?", (username,)
        ).fetchone()
    return bool(row and row[0] == 1)


def set_connected(username: str, room_code: str, connected: bool):
    with _connect() as con:
        con.execute("""
            INSERT INTO sessions (username, room_code, is_connected, last_seen)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(username) DO UPDATE SET
                room_code    = excluded.room_code,
                is_connected = excluded.is_connected,
                last_seen    = datetime('now')
        """, (username, room_code, 1 if connected else 0))


def get_session(username: str) -> dict | None:
    """Returns session row as dict or None."""
    with _connect() as con:
        row = con.execute(
            "SELECT username, room_code, is_connected FROM sessions WHERE username = ?",
            (username,)
        ).fetchone()
    if not row:
        return None
    return {"username": row[0], "room_code": row[1], "is_connected": row[2]}


def clear_session(username: str):
    with _connect() as con:
        con.execute(
            "UPDATE sessions SET is_connected = 0, last_seen = datetime('now') WHERE username = ?",
            (username,)
        )
