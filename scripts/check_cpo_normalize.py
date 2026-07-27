"""CPO명 정규화 결과 검증 리포트.

로컬 charging.db의 busi_nm을 config.normalize_cpo로 정규화한 뒤,
2개 이상의 원본 이름이 하나로 합쳐진 '병합 클러스터'를 충전기 수 순으로 출력한다.
정규화가 잘못 합치는 게 없는지 눈으로 확인하는 용도.

실행: python scripts/check_cpo_normalize.py
"""
import io
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "charging.db")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cpo_normalize_report.txt")


def main():
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT busi_nm, COUNT(*) n FROM chargers GROUP BY busi_nm"
    ).fetchall()
    con.close()

    clusters = {}          # 대표명 -> {raw: count}
    for raw, n in rows:
        canon = config.normalize_cpo(raw)
        clusters.setdefault(canon, {})[raw or "(빈값)"] = n

    merged = {c: v for c, v in clusters.items() if len(v) > 1}
    singles = {c: v for c, v in clusters.items() if len(v) == 1}

    def total(v):
        return sum(v.values())

    lines = []
    lines.append(f"원본 CPO 이름 수 : {len(rows)}")
    lines.append(f"정규화 후 CPO 수 : {len(clusters)}")
    lines.append(f"병합된 클러스터  : {len(merged)}개 (2개 이상 원본이 합쳐진 것)")
    lines.append("")
    lines.append("=" * 60)
    lines.append("병합 클러스터 (충전기 수 순) — 잘못 합쳐진 게 없는지 확인")
    lines.append("=" * 60)
    for canon in sorted(merged, key=lambda c: total(merged[c]), reverse=True):
        v = merged[canon]
        lines.append(f"\n▶ {canon}  (총 {total(v):,}기, {len(v)}개 원본)")
        for raw, n in sorted(v.items(), key=lambda x: x[1], reverse=True):
            mark = "  ← 대표명과 동일" if raw == canon else ""
            lines.append(f"    {n:>6,}  {raw!r}{mark}")

    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"작성됨: {OUT}  (병합 {len(merged)}개, 단독 {len(singles)}개)")


if __name__ == "__main__":
    main()
