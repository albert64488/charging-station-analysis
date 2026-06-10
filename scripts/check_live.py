"""반포써밋아파트 실시간 상태 검증."""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db, calculator as cal  # noqa: E402

term = sys.argv[1] if len(sys.argv) > 1 else "반포써밋"
with db.get_conn() as conn:
    r = db.fetch_df(
        conn, "SELECT stat_id, stat_nm FROM stations WHERE stat_nm LIKE ?", [f"%{term}%"])

print("찾은 충전소:", r.values.tolist())
if len(r):
    info, live = cal.live_status(r.iloc[0]["stat_id"])
    print(f"\n{info['stat_nm']} | 운영사: {info['busi_nm']} | {info['addr']}\n")
    print(live.to_string(index=False))
