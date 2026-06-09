"""에바(EVAR) 계열 충전기 진단 — 이름 변형/타입/급속분류 확인."""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db  # noqa: E402

with db.get_conn() as conn:
    print("=== 에바/EVAR 포함 운영사별 급속/완속 수 ===")
    rows = conn.execute(
        "SELECT busi_nm, is_fast, COUNT(charger_key) "
        "FROM chargers WHERE busi_nm LIKE '%에바%' OR UPPER(busi_nm) LIKE '%EVAR%' "
        "GROUP BY busi_nm, is_fast ORDER BY busi_nm, is_fast"
    ).fetchall()
    for nm, isf, n in rows:
        print(f"  {nm:12s} | {'급속' if isf == 1 else '완속'} | {n}")

    print("\n=== 에바 계열 충전기타입(chgerType) 분포 + 우리 분류 ===")
    rows2 = conn.execute(
        "SELECT chger_type, is_fast, COUNT(charger_key) "
        "FROM chargers WHERE busi_nm LIKE '%에바%' OR UPPER(busi_nm) LIKE '%EVAR%' "
        "GROUP BY chger_type, is_fast ORDER BY chger_type"
    ).fetchall()
    for t, isf, n in rows2:
        print(f"  타입 {t!r} → {'급속' if isf == 1 else '완속'} | {n}개")

    print("\n=== 에바 계열 합계 ===")
    tot = conn.execute(
        "SELECT is_fast, COUNT(charger_key) FROM chargers "
        "WHERE busi_nm LIKE '%에바%' OR UPPER(busi_nm) LIKE '%EVAR%' GROUP BY is_fast"
    ).fetchall()
    for isf, n in tot:
        print(f"  {'급속' if isf == 1 else '완속'}: {n}개")
