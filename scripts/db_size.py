"""DB 테이블별 용량 확인 (Postgres)."""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import psycopg  # noqa: E402

conn = psycopg.connect(config.DATABASE_URL)
cur = conn.cursor()
cur.execute("""
    SELECT relname, pg_size_pretty(pg_total_relation_size(C.oid)) AS total
    FROM pg_class C JOIN pg_namespace N ON N.oid = C.relnamespace
    WHERE nspname = 'public' AND relkind = 'r'
    ORDER BY pg_total_relation_size(C.oid) DESC
""")
print("테이블별 용량(인덱스 포함):")
for r in cur.fetchall():
    print(f"  {r[0]:20s} {r[1]}")
cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
print("DB 전체:", cur.fetchone()[0])
cur.execute("SELECT count(*) FROM charger_stats")
print("charger_stats 행수:", cur.fetchone()[0])
conn.close()
