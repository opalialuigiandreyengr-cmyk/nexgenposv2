"""Maintenance: clear stale order processing locks in the POS DB."""
import sqlite3

conn = sqlite3.connect("instance/pos.db")
cur = conn.cursor()

cur.execute('SELECT id, order_no, status, processing_user_id FROM "order" WHERE processing_user_id IS NOT NULL')
rows = cur.fetchall()
print(f"{len(rows)} locked order(s) before cleanup:")
for r in rows:
    print(" ", r)

cur.execute(
    'UPDATE "order" SET processing_user_id = NULL, processing_started_at = NULL, '
    "processing_last_seen_at = NULL WHERE processing_user_id IS NOT NULL"
)
print("cleared locks:", cur.rowcount)

conn.commit()
conn.close()
