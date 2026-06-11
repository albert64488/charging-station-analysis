"""새 Turso HTTP 클라이언트 읽기/쓰기 검증."""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db, calculator as cal  # noqa: E402

db.init_db()
print("init_db(스키마) OK")

s, c = cal.summary(zcode="36")
print(f"읽기 OK — 세종 충전소 {len(s)}, 충전기 {len(c)}")

with db.get_conn() as conn:
    db.set_meta(conn, "_http_test", "ok")
    print("단일 쓰기(set_meta) OK, readback:", db.get_meta(conn, "_http_test"))
    # 배치(executemany) 테스트
    db.insert_intervals(conn, [("__TEST__", 2, "2026-01-01 00:00:00", "2026-01-01 01:00:00")])
    conn.execute("DELETE FROM state_intervals WHERE charger_key='__TEST__'")
    print("배치 쓰기(executemany) OK")
