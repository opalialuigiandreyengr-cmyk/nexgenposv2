#!/usr/bin/env python3
"""Historical Data Migration Tool — Upload all local SQLite orders to Supabase Cloud.

Streams historical transactions (orders, items, settlements) from local pos.db
to online Supabase PostgreSQL in 1,000-record batch chunks.
"""

import sys
import os
import json
import time
from datetime import datetime, date

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from website import create_app, db
from website.models import Order, OrderItem, Settlement
from supabase_client import get_supabase_client, is_supabase_configured

def serialize_model(obj):
    """Convert an SQLAlchemy model instance into a JSON-serializable dictionary."""
    if obj is None:
        return None
    res = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.name)
        if isinstance(val, (datetime, date)):
            val = val.isoformat()
        res[col.name] = val
    return res

def upload_historical_data(batch_size=500):
    if not is_supabase_configured():
        print("[ERROR] Supabase credentials missing in .env file.")
        return

    client = get_supabase_client()
    if not client:
        print("[ERROR] Could not connect to Supabase client.")
        return

    app = create_app()
    with app.app_context():
        print("\n====================================================")
        print("  Nexgen POS_V2 — Supabase Historical Bulk Sync Tool")
        print("====================================================")

        total_orders = Order.query.count()
        print(f"[*] Found {total_orders:,} historical orders in local SQLite pos.db.")

        # Pre-fetch settlements mapped by order_id
        print("[*] Indexing settlements...")
        settlements_by_order = {}
        all_settlements = Settlement.query.all()
        for s in all_settlements:
            if s.order_id:
                settlements_by_order[s.order_id] = serialize_model(s)

        # Pre-fetch order items mapped by order_id
        print("[*] Indexing order items...")
        items_by_order = {}
        all_items = OrderItem.query.all()
        for item in all_items:
            items_by_order.setdefault(item.order_id, []).append(serialize_model(item))

        print(f"[*] Uploading orders in batches of {batch_size}...")

        offset = 0
        uploaded_count = 0
        failed_count = 0
        start_time = time.time()

        while offset < total_orders:
            orders_batch = Order.query.order_by(Order.id.asc()).offset(offset).limit(batch_size).all()
            if not orders_batch:
                break

            batch_payload = []
            for o in orders_batch:
                o_dict = serialize_model(o)
                # Remove transient local processing lock fields not present in Supabase schema
                for k in ['processing_user_id', 'processing_started_at', 'processing_last_seen_at']:
                    o_dict.pop(k, None)

                o_dict['items'] = items_by_order.get(o.id, [])
                o_dict['settlement'] = settlements_by_order.get(o.id, None)
                o_dict['synced_at'] = datetime.utcnow().isoformat()
                batch_payload.append(o_dict)

            try:
                client.table('orders').upsert(batch_payload).execute()
                uploaded_count += len(batch_payload)
                print(f"  [OK] Synced {uploaded_count:,} / {total_orders:,} orders ({uploaded_count / total_orders * 100:.1f}%)")
            except Exception as exc:
                failed_count += len(batch_payload)
                print(f"  [WARN] Batch error at offset {offset}: {exc}")

            offset += batch_size

        elapsed = time.time() - start_time
        print("\n====================================================")
        print(f"  Bulk Upload Complete in {elapsed:.2f} seconds!")
        print(f"  Successfully Synced : {uploaded_count:,} orders")
        if failed_count > 0:
            print(f"  Failed Batches      : {failed_count:,} orders")
        print("====================================================\n")

if __name__ == '__main__':
    upload_historical_data()
