/* =============================================================================
   core/store.js — tiny pub/sub store (framework-free)
   -----------------------------------------------------------------------------
   One shared store for the whole shell; page modules subscribe to slices
   instead of reaching into each other's DOM. Immutable replace semantics:
   set(key, value) swaps the value and notifies subscribers with
   (value, previousValue). subscribe() returns an unsubscribe function.
   ========================================================================== */
export function createStore(seed) {
  const state = Object.assign({}, seed || {});
  const listeners = {};

  return {
    get(key) {
      return state[key];
    },

    set(key, value) {
      const previous = state[key];
      if (previous === value) return;
      state[key] = value;
      (listeners[key] || []).forEach((fn) => {
        try {
          fn(value, previous);
        } catch (error) {
          console.error('[store] subscriber failed for', key, error);
        }
      });
    },

    subscribe(key, fn) {
      (listeners[key] = listeners[key] || []).push(fn);
      return () => {
        listeners[key] = (listeners[key] || []).filter((f) => f !== fn);
      };
    },
  };
}

/* Shared shell store. Slice contract (consumed by the navbar, pages, sse):
   - area:    'admin' | 'pos'   (current view area, set by core/spa.js)
   - session: { user, role } | null   (populated by the login flow)
   - sse:     'off' | 'connecting' | 'connected' | 'retry'
   - printer: null | { name, online }  (checked by the shell)              */
const store = createStore({
  area: 'admin',
  session: null,
  sse: 'off',
  printer: null,
});

export default store;
