"""특정 시·도(zcode)만 refresh. .env의 ZSCODE 영향 없이 직접 호출.

사용: python scripts/refresh_region.py 43
"""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import collector  # noqa: E402

zcode = sys.argv[1] if len(sys.argv) > 1 else "43"
res = collector.refresh_full(zcode=zcode, zscode=None)
print(f"[refresh {zcode}] 조회 {res.get('fetched')} / 신규메타 {res.get('new_meta')} / "
      f"신규구간 {res.get('new_intervals')}")
print("현황:", res.get("stats"))
