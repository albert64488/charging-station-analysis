"""observation_start_at을 'N시간 전'으로 설정 (기본 24h). 초기 이용률 추정값이 보이도록."""
import sys
import io
import os
import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db, util  # noqa: E402

hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
ts = (util.now_dt() - datetime.timedelta(hours=hours)).strftime(util.FMT)
with db.get_conn() as conn:
    db.set_meta(conn, "observation_start_at", ts)
    conn.commit()
print(f"observation_start_at = {ts} (현재로부터 {hours}시간 전)")
