/* =============================================================================
   js/pages/cashier_accountability.js — Cashier Accountability (Phase 6, Milestone 6.2)
   =============================================================================
   SPA contract: default export { mount, destroy }.
   ========================================================================== */
'use strict';

import { notify } from '../core/toast.js';

let rootEl = null;
let destroyFns = [];

function toISO(d) {
  if (!d) return '';
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

export function mount(el) {
  rootEl = el || document.querySelector('#view-root') || document;
  destroyFns = [];

  const dateInput = rootEl.querySelector('#caReportDate');
  const btnToday = rootEl.querySelector('#btnShowToday');
  const btnPrint = rootEl.querySelector('#btnPrintAccountability');

  if (dateInput) {
    const handleDateChange = () => {
      const val = dateInput.value;
      if (val) {
        window.location.href = `/cashier_accountability?date=${encodeURIComponent(val)}`;
      }
    };
    dateInput.addEventListener('change', handleDateChange);
    destroyFns.push(() => dateInput.removeEventListener('change', handleDateChange));
  }

  if (btnToday) {
    const handleToday = () => {
      const todayStr = toISO(new Date());
      window.location.href = `/cashier_accountability?date=${encodeURIComponent(todayStr)}`;
    };
    btnToday.addEventListener('click', handleToday);
    destroyFns.push(() => btnToday.removeEventListener('click', handleToday));
  }

  if (btnPrint) {
    const handlePrint = async () => {
      const reportDate = dateInput ? dateInput.value : toISO(new Date());
      btnPrint.disabled = true;
      const originalHtml = btnPrint.innerHTML;
      btnPrint.innerHTML = '<span>Sending to Printer...</span>';

      try {
        const formData = new FormData();
        formData.append('date', reportDate);

        // Get CSRF token if present
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
        const headers = {};
        if (csrfToken) headers['X-CSRFToken'] = csrfToken;

        const res = await fetch('/print_cashier_accountability', {
          method: 'POST',
          headers,
          body: formData,
        });

        if (res.ok) {
          notify.success('Accountability report sent to thermal receipt printer.');
        } else {
          const errData = await res.json().catch(() => ({}));
          notify.error(errData.message || 'Failed to print accountability report. Check printer connection.');
        }
      } catch (err) {
        notify.error('Network error while requesting print operation.');
      } finally {
        btnPrint.disabled = false;
        btnPrint.innerHTML = originalHtml;
      }
    };

    btnPrint.addEventListener('click', handlePrint);
    destroyFns.push(() => btnPrint.removeEventListener('click', handlePrint));
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
