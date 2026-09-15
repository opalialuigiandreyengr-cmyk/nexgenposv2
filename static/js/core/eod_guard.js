/* =============================================================================
   core/eod_guard.js — Cashier End of Day Required Guard
   -----------------------------------------------------------------------------
   Checks for unclosed prior business dates via /check_missing_eod_dates.
   If any prior business dates require an End of Day (Z-Reading), this guard
   immediately displays the "End of Day Required" modal to keep operations
   BIR- and SFTP-compliant before active orders are processed.
   ========================================================================== */
import { api } from './api.js';
import modal from './modal.js';

let isChecking = false;
let isModalOpen = false;

export async function checkEodRequired() {
  if (isChecking || isModalOpen) return;

  // Do not pop up over the End of Day page itself
  if (window.location.pathname.startsWith('/end_of_day')) return;

  // Only run for cashier, crew, and non-admin operational roles
  const userRole = (document.body.dataset.userRole || '').toLowerCase();
  if (userRole === 'admin' || userRole === 'bir_guest') return;

  isChecking = true;
  try {
    const res = await api.get('/check_missing_eod_dates');
    const missingDates = (res && Array.isArray(res.missing_eod_dates)) ? res.missing_eod_dates : [];
    if (res && res.has_missing_dates && missingDates.length > 0) {
      isModalOpen = true;
      await modal.eodRequiredModal(missingDates);
      isModalOpen = false;
    }
  } catch (err) {
    // Network or silent error - do not disrupt UI
  } finally {
    isChecking = false;
  }
}

if (typeof window !== 'undefined') {
  window.checkEodRequired = checkEodRequired;
}

export default { checkEodRequired };
