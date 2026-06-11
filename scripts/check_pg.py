"""Neon(Postgres) 어댑터 읽기/쓰기 검증."""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db  # noqa: E402

db.init_db()
print("init_db(스키마 생성) OK")

with db.get_conn() as conn:
    db.upsert_stations(conn, [(
        "TEST01", "테스트충전소", "테스트주소", 37.5, 127.0, "B", "테스트운영사", "11",
        "2026-06-10 00:00:00")])
    db.upsert_chargers(conn, [(
        "TEST01-01", "TEST01", "01", "04", 1, 100.0, "테스트운영사", "2026-06-10 00:00:00")])
    db.upsert_current_states(conn, [(
        "TEST01-01", 3, "2026-06-10 00:00:00", "2026-06-10 00:00:00", "2026-06-10 00:00:00")])
    db.insert_intervals(conn, [("TEST01-01", 2, "2026-06-09 00:00:00", "2026-06-10 00:00:00")])
    db.set_meta(conn, "_pg_test", "ok")
    conn.commit()
    print("쓰기(upsert/insert/batch/meta) OK")
    print("stats:", db.stats(conn))
    print("meta readback:", db.get_meta(conn, "_pg_test"))

    for t in ("state_intervals", "current_state", "chargers"):
        conn.execute(f"DELETE FROM {t} WHERE charger_key='TEST01-01'")
    conn.execute("DELETE FROM stations WHERE stat_id='TEST01'")
    conn.commit()
    print("테스트 데이터 정리 OK")
