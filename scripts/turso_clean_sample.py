"""Turso에서 샘플(SMPL) 데이터 제거 + 현황 출력.

사용: TURSO_DATABASE_URL, TURSO_AUTH_TOKEN 환경변수 설정 후 실행.
"""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db  # noqa: E402

with db.get_conn() as conn:
    for t in ("state_intervals", "current_state", "chargers"):
        conn.execute(f"DELETE FROM {t} WHERE charger_key LIKE 'SMPL%'")
    conn.execute("DELETE FROM stations WHERE stat_id LIKE 'SMPL%'")
    conn.commit()
    s = db.stats(conn)

print("샘플 정리 완료. 현재 Turso 현황:")
print(s)
