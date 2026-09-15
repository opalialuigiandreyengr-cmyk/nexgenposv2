"""Read-only diagnostic: inspect current order locks + user existence."""
import sqlite3
from datetime import datetime, timedelta

import os
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance", "pos.db")
now = datetime.now()
print("local now:", now.isoformat(timespec="seconds"))

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

cols = [r["name"] for r in cur.execute('PRAGMA table_info("order")')]
lock_cols = [c for c in cols if c.startswith("processing_")]
print("lock columns:", lock_cols)

rows = cur.execute(
    'SELECT id, order_no, status, processing_user_id, processing_started_at, '
    'processing_last_seen_at FROM "order" WHERE processing_user_id IS NOT NULL'
).fetchall()

for r in rows:
    seen = r["processing_last_seen_at"] or r["processing_started_at"]
    age = None
    future = None
    if seen:
        try:
            seen_dt = datetime.fromisoformat(seen)
            delta = (now - seen_dt).total_seconds()
            age = round(delta, 1)
            future = delta < 0
        except Exception as exc:
            age = f"parse-error: {exc}"
    uid = r["processing_user_id"]
    u = cur.execute("SELECT id, username, role FROM user WHERE id = ?", (uid,)).fetchone()
    holder = u["username"] if u else "USER-DELETED"
    stale = (age is not None and isinstance(age, float) and age > 15)
    print(
        f"order {r['id']} #{r['order_no']} status={r['status']} holder={holder} "
        f"(uid={uid}) seen_at={seen} age_s={age}{' FUTURE' if future else ''}"
        f"{' STALE->auto-clears' if stale else ' ACTIVE'}"
    )

if not rows:
    print("no active locks in DB")

users = cur.execute("SELECT id, username, role FROM user ORDER BY id").fetchall()
print("users:", [(u["id"], u["username"], u["role"]) for u in users])
con.close()
