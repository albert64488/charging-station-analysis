"""충전소명/주소 일부로 폭넓게 검색 (우리 DB)."""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db  # noqa: E402

term = sys.argv[1] if len(sys.argv) > 1 else "길동"
with db.get_conn() as conn:
    rows = db.fetch_df(
        conn,
        "SELECT stat_id, stat_nm, busi_nm, addr FROM stations "
        "WHERE stat_nm LIKE ? OR addr LIKE ? ORDER BY stat_nm LIMIT 40",
        [f"%{term}%", f"%{term}%"],
    )
print(f"'{term}' 매칭 {len(rows)}건:")
print(rows.to_string(index=False) if len(rows) else "  없음")
