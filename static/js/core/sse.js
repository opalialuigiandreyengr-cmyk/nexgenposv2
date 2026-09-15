/* =============================================================================
   core/sse.js — EventSource wrapper for /stream_orders (V1 parity)
   -----------------------------------------------------------------------------
   V1 contract (website/orders.py stream_orders): plain `data:` lines with a
   1s heartbeat and an initial "connected" ping. JSON payloads are typed:
       table_transfer | order_created | order_status | order_lock | update
   EventSource reconnects on its own; we mirror the state into the store
   ('connecting' | 'connected' | 'retry') and forward every parsed event as
   a CustomEvent `pos:sse` on document, so page modules subscribe without
   touching the socket. Call connect() once from the shell (Step 5).
   ========================================================================== */
import store from './store.js';

const STREAM_URL = '/stream_orders';
const EVT = 'pos:sse';
const IGNORED_PINGS = new Set(['connected', 'heartbeat']);

let source = null;

function dispatch(payload) {
  document.dispatchEvent(new CustomEvent(EVT, { detail: payload }));
}

export function connect() {
  if (source) return source;
  source = new EventSource(STREAM_URL);
  store.set('sse', 'connecting');

  source.onopen = () => store.set('sse', 'connected');

  source.onmessage = (event) => {
    const data = typeof event.data === 'string' ? event.data : '';
    if (!data || IGNORED_PINGS.has(data)) return;

    if (data.startsWith('{')) {
      try {
        dispatch(JSON.parse(data));
        return;
      } catch (_) {
        /* fall through: forward malformed lines as strings for debugging */
      }
    }
    dispatch(data);
  };

  /* EventSource auto-reconnects; 'retry' just surfaces the state. */
  source.onerror = () => store.set('sse', 'retry');
  return source;
}

export function disconnect() {
  if (source) {
    source.close();
    source = null;
    store.set('sse', 'off');
  }
}

/* Subscribe to order events. Returns an unsubscribe function. */
export function onMessage(handler) {
  document.addEventListener(EVT, handler);
  return () => document.removeEventListener(EVT, handler);
}

export default { connect, disconnect, onMessage };
