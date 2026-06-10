"""get_meta 동작 확인 (Turso 경로)."""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db  # noqa: E402

print("get_meta 존재:", hasattr(db, "get_meta"))
with db.get_conn() as conn:
    print("last_poll_at:", db.get_meta(conn, "last_poll_at"))
    print("last_refresh_at:", db.get_meta(conn, "last_refresh_at", "없음"))
