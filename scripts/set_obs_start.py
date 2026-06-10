"""관측 시작 시각(observation_start_at)을 시드 시점(MIN updated_at)으로 설정."""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db  # noqa: E402

with db.get_conn() as conn:
    existing = db.get_meta(conn, "observation_start_at")
    seed = conn.execute("SELECT MIN(updated_at) FROM current_state").fetchone()[0]
    print("기존 observation_start_at:", existing)
    print("시드 추정시각(MIN updated_at):", seed)
    if seed:
        db.set_meta(conn, "observation_start_at", seed)
        conn.commit()
        print("→ observation_start_at 설정 완료:", seed)
