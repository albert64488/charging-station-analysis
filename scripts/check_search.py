"""검색 + 단일 충전소 이용률 검증."""
import sys
import io
import os
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import calculator as cal  # noqa: E402

term = sys.argv[1] if len(sys.argv) > 1 else "스타필드"
t = time.time()
r = cal.search_stations(term)
print(f"검색 '{term}': {len(r)}곳, {time.time()-t:.1f}초")
print(r[["충전소명", "운영사", "주소"]].head(6).to_string(index=False))

if len(r):
    sid = r.iloc[0]["stat_id"]
    t = time.time()
    c = cal.load_durations(stat_id=sid)
    print(f"\n단일충전소 이용률 계산: 충전기 {len(c)}행, {time.time()-t:.1f}초")
    if len(c):
        print(c[["chger_id", "이용률", "장애율"]].to_string(index=False))
