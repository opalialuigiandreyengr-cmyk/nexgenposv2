/* =============================================================================
   js/pages/audit_reports.js — Audit Reports (Phase 6, Milestone 6.4)
   =============================================================================
   SPA contract: default export { mount, destroy }.
   ========================================================================== */
'use strict';

import { notify } from '../core/toast.js';

let rootEl = null;
let destroyFns = [];

export function mount(el) {
  rootEl = el || document.querySelector('#view-root') || document;
  destroyFns = [];

  const fromInput = rootEl.querySelector('#arFromDate');
  const toInput = rootEl.querySelector('#arToDate');
  const btnFilter = rootEl.querySelector('#btnFilterDates');

  if (btnFilter && fromInput && toInput) {
    const handleFilter = () => {
      const fromVal = fromInput.value;
      const toVal = toInput.value;

      if (!fromVal || !toVal) {
        notify.error('Please select both From Date and To Date.');
        return;
      }

      const currentPath = window.location.pathname;
      window.location.href = `${currentPath}?from_date=${encodeURIComponent(fromVal)}&to_date=${encodeURIComponent(toVal)}`;
    };

    btnFilter.addEventListener('click', handleFilter);
    destroyFns.push(() => btnFilter.removeEventListener('click', handleFilter));
  }
}

export function destroy() {
  destroyFns.forEach((fn) => {
    try { fn(); } catch (e) {}
  });
  destroyFns = [];
  rootEl = null;
}

export default { mount, destroy };
