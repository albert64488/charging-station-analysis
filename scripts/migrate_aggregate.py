"""집계 저장 방식으로 마이그레이션.

1) state_intervals 테이블 삭제 (291MB 즉시 회수 → Neon 용량 해제)
2) charger_stats 테이블 생성 (init_db)
3) observation_start_at 을 now-24h 로 리셋 (카운터와 관측창을 함께 새 출발)

이용률 = (누적 충전 + 현재 진행분) / 관측창. 카운터는 0에서 시작해
이후 refresh/poll 의 상태 전이로 누적된다(현재상태 기반 초기 추정 + 점진 정밀화).
"""
import sys
import io
import os
import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db, util  # noqa: E402

with db.get_conn() as conn:
    conn.execute("DROP TABLE IF EXISTS state_intervals")
    conn.commit()
    print("1) state_intervals 삭제 → 공간 회수")

db.init_db()
print("2) charger_stats 생성 (init_db)")

with db.get_conn() as conn:
    ts = (util.now_dt() - datetime.timedelta(hours=24)).strftime(util.FMT)
    db.set_meta(conn, "observation_start_at", ts)
    conn.commit()
    print(f"3) observation_start_at = {ts}")
    print("   현황:", db.stats(conn))
