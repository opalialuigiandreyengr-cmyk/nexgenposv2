/* =============================================================================
   js/pages/data_retention.js — Data Retention (Phase 5, Milestone 5.4)
   =============================================================================
   SPA contract: default export { mount, destroy }.
   ========================================================================== */
'use strict';

let rootEl = null;

export function mount(el) {
  rootEl = el || document.querySelector('#view-root') || document;
}

export function destroy() {
  rootEl = null;
}

export default { mount, destroy };
