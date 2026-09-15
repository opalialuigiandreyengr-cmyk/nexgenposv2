"""Cloud Synchronization Engine for POS_V2 & Supabase.

Handles background batch flushing of local transaction mutations (SyncQueue)
to Supabase PostgreSQL cloud database, with automatic offline queueing
and internet reconnection recovery.
"""

import json
import logging
import threading
import time
from datetime import datetime, date
from typing import Dict, Any, Optional

from supabase_client import get_supabase_client, is_supabase_configured

logger = logging.getLogger(__name__)

_worker_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
SYNC_INTERVAL_SECONDS = 10.0


def model_to_dict(obj) -> Dict[str, Any]:
    """Convert an SQLAlchemy model instance into a JSON-serializable dictionary."""
    if obj is None:
        return {}
    res = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.name)
        if isinstance(val, (datetime, date)):
            val = val.isoformat()
        res[col.name] = val
    return res


def enqueue_sync_event(table_name: str, record_id: int, action: str, payload_dict: Dict[str, Any]) -> bool:
    """Enqueue a transaction mutation event into local SQLite SyncQueue table."""
    try:
        from website import db
        from website.models import SyncQueue

        queue_item = SyncQueue(
            table_name=table_name,
            record_id=record_id,
            action=action.upper(),
            payload=json.dumps(payload_dict, default=str),
            status='pending',
            created_at=datetime.utcnow()
        )
        db.session.add(queue_item)
        db.session.commit()
        logger.debug(f"[SyncQueue] Enqueued {action} on {table_name}#{record_id}")
        return True
    except Exception as exc:
        logger.error(f"[SyncQueue] Failed to enqueue sync event: {exc}")
        return False


def enqueue_order_completed(order, settlement=None) -> bool:
    """Enqueue a completed order, its line items, and settlement into SyncQueue."""
    try:
        order_dict = model_to_dict(order)
        for k in ['processing_user_id', 'processing_started_at', 'processing_last_seen_at']:
            order_dict.pop(k, None)

        order_items = [model_to_dict(item) for item in getattr(order, 'items', [])]
        order_dict['items'] = order_items

        if settlement:
            order_dict['settlement'] = model_to_dict(settlement)

        return enqueue_sync_event('orders', order.id, 'INSERT', order_dict)
    except Exception as exc:
        logger.error(f"[SyncQueue] Failed to enqueue completed order: {exc}")
        return False


def process_sync_queue_batch(app=None, limit: int = 50) -> Dict[str, Any]:
    """Flush a batch of pending SyncQueue records to Supabase."""
    if not is_supabase_configured():
        return {"success": False, "reason": "Supabase not configured"}

    client = get_supabase_client()
    if not client:
        return {"success": False, "reason": "Supabase client unavailable"}

    def _do_flush():
        from website import db
        from website.models import SyncQueue

        pending_items = SyncQueue.query.filter_by(status='pending').order_by(SyncQueue.id.asc()).limit(limit).all()
        if not pending_items:
            return {"success": True, "processed": 0}

        synced_count = 0
        failed_count = 0

        for item in pending_items:
            try:
                payload = json.loads(item.payload) if item.payload else {}
                
                # Execute upsert to Supabase table
                client.table(item.table_name).upsert(payload).execute()
                
                item.status = 'synced'
                item.synced_at = datetime.utcnow()
                item.error_message = None
                synced_count += 1
            except Exception as exc:
                item.retry_count += 1
                item.error_message = str(exc)
                if item.retry_count >= 10:
                    item.status = 'failed'
                failed_count += 1
                logger.warning(f"[CloudSync] Sync failed for {item.table_name}#{item.record_id}: {exc}")

        db.session.commit()
        return {"success": True, "processed": len(pending_items), "synced": synced_count, "failed": failed_count}

    if app:
        with app.app_context():
            return _do_flush()
    else:
        return _do_flush()


def _worker_loop(app):
    """Background worker daemon loop."""
    logger.info("[CloudSync] Background sync worker thread started.")
    while not _stop_event.is_set():
        try:
            process_sync_queue_batch(app=app)
        except Exception as exc:
            logger.error(f"[CloudSync] Worker loop error: {exc}")
        
        _stop_event.wait(SYNC_INTERVAL_SECONDS)


def start_cloud_sync_worker(app) -> None:
    """Start the cloud sync daemon thread if not already running."""
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return

    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=_worker_loop,
        args=(app,),
        daemon=True,
        name="CloudSyncWorker"
    )
    _worker_thread.start()
    logger.info("[CloudSync] Cloud sync background thread launched.")


def stop_cloud_sync_worker() -> None:
    """Signal background thread to terminate."""
    _stop_event.set()
