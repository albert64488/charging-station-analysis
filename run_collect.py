"""수집 엔트리포인트 (이벤트 기반).

  python run_collect.py refresh               # 전수 시드/보정 (.env 지역)
  python run_collect.py refresh --nationwide  # 전국 전수 (~52콜)
  python run_collect.py poll                  # 델타 폴링 1회 (.env 지역)
  python run_collect.py poll --nationwide     # 전국 델타 (10분마다 권장)
  python run_collect.py seed-sample --days 7  # API 키 없이 샘플
"""
import argparse

from src import collector, db


def _region(args):
    """--nationwide면 (None, None), 아니면 .env의 ZCODE/ZSCODE."""
    import config
    if getattr(args, "nationwide", False):
        return None, None
    return (config.ZCODE or None), (config.ZSCODE or None)


def main():
    p = argparse.ArgumentParser(description="충전소 상태 수집 (이벤트 기반)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("refresh", help="전수 조회로 메타+현재상태 시드/보정")
    pr.add_argument("--nationwide", action="store_true")

    pp = sub.add_parser("poll", help="델타 조회로 변경분 구간 기록")
    pp.add_argument("--nationwide", action="store_true")
    pp.add_argument("--period", type=int, default=10, help="조회 기간(분, 최대 10)")

    ps = sub.add_parser("seed-sample", help="샘플 상태구간 생성(오프라인)")
    ps.add_argument("--days", type=int, default=7)

    args = p.parse_args()
    db.init_db()

    if args.cmd == "refresh":
        zcode, zscode = _region(args)
        r = collector.refresh_full(zcode=zcode, zscode=zscode)
        print(f"[refresh] source={r['source']} 조회 {r.get('fetched','?')}건 / "
              f"신규메타 {r.get('new_meta', 0)} / 신규구간 {r['new_intervals']}")
    elif args.cmd == "poll":
        zcode, zscode = _region(args)
        r = collector.poll_changes(period=args.period, zcode=zcode, zscode=zscode)
        print(f"[poll] period={r['period']} 변경수신 {r['fetched']} / 구간적립 {r['changed']} / "
              f"미편입(메타없음) {r['skipped_unknown']}")
    elif args.cmd == "seed-sample":
        r = collector.seed_sample(days=args.days)
        print(f"[seed-sample] 충전기 {r['chargers']} / 신규구간 {r['new_intervals']}")

    s = r["stats"]
    print(f"[현황] 충전소 {s['stations']} / 충전기 {s['chargers']} / "
          f"현재상태 {s['current_state']} / 누적구간 {s['intervals']}")


if __name__ == "__main__":
    main()
