"""수집 데이터 점검용 임시 스크립트."""
import sqlite3
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
c = sqlite3.connect("data/charging.db")

# 1) 전국 CPO 수
ncpo = c.execute("SELECT COUNT(DISTINCT busi_nm) FROM chargers").fetchone()[0]
print("전국 CPO 수:", ncpo)

# 2) 에바/EVA 검색
rows = c.execute(
    "SELECT busi_nm, COUNT(charger_key) n FROM chargers "
    "WHERE busi_nm LIKE '%에바%' OR UPPER(busi_nm) LIKE '%EVA%' "
    "GROUP BY busi_nm ORDER BY n DESC"
).fetchall()
print("\n[에바/EVA 포함 CPO]")
print(rows if rows else "  없음")

# 3) 충전기 상위 CPO 10
print("\n[충전기 보유 상위 CPO]")
for nm, n in c.execute(
    "SELECT busi_nm, COUNT(charger_key) n FROM chargers GROUP BY busi_nm ORDER BY n DESC LIMIT 10"
):
    print(f"  {nm}: {n:,}")

# 4) 지역(zcode) 커버리지
print("\n[지역별 충전소 수]")
for z, n in c.execute(
    "SELECT zcode, COUNT(stat_id) n FROM stations GROUP BY zcode ORDER BY n DESC"
):
    print(f"  {z}: {n:,}")

c.close()
