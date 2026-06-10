"""특정 충전소에 대해 data.go.kr이 '지금' 보고하는 상태를 직접 조회.

사용: python scripts/check_live_api.py 41 판교창조경제
"""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from src import api_client  # noqa: E402

zcode = sys.argv[1] if len(sys.argv) > 1 else "41"
needle = sys.argv[2] if len(sys.argv) > 2 else "판교창조경제"

print(f"data.go.kr getChargerInfo zcode={zcode} 조회 중...")
items = api_client.fetch_charger_info(zcode=zcode)
matches = [it for it in items if needle in (it.get("statNm") or "")]
print(f"'{needle}' 매칭 충전기 {len(matches)}개 — data.go.kr이 지금 보고하는 값:\n")
print(f"{'충전소':20} {'충전기':4} {'상태':12} {'갱신일시':16}")
for it in sorted(matches, key=lambda x: (x.get("statNm", ""), x.get("chgerId", ""))):
    stat = config.STATUS_NAMES.get(int(it.get("stat") or 9), it.get("stat"))
    print(f"{it.get('statNm',''):20} {it.get('chgerId',''):4} {stat:12} {it.get('statUpdDt','')}")
