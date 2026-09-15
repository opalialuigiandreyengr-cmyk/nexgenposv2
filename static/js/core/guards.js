/* =============================================================================
   core/guards.js — printer connection guard
   -----------------------------------------------------------------------------
   guardPrinter() -> Promise<boolean>. A 10s cache keeps recent successful
   checks from re-blocking within one settle/receipt flow. Directly invokes
   modal.error() on failure, matching the clean Insufficient Amount modal.
   ========================================================================== */
'use strict';

import { api } from './api.js';
import modal from './modal.js';

const PRINTER_GUARD_CACHE_MS = 1000;
let printerGuardLastConnectedAt = 0;

export function guardPrinter() {
  return new Promise((resolve) => {
    if (Date.now() - printerGuardLastConnectedAt < PRINTER_GUARD_CACHE_MS) {
      resolve(true);
      return;
    }

    api
      .get('/check_printer_status')
      .then((data) => {
        if (data && (data.connected || data.dev_mode || data.printer_required === false)) {
          printerGuardLastConnectedAt = Date.now();
          resolve(true);
          return;
        }
        modal
          .error(
            'Printer Connection Required',
            (data && data.message) || 'Please ensure your printer is connected and ready.',
            { confirmLabel: 'I understand' }
          )
          .then(() => resolve(false));
      })
      .catch((error) => {
        if (error && error.name !== 'AbortError') {
          console.warn('Printer connection check unavailable:', error.message || error);
        }
        modal
          .error('Printer Connection Required', 'Please ensure your printer is connected and ready.', {
            confirmLabel: 'I understand',
          })
          .then(() => resolve(false));
      });
  });
}

export default guardPrinter;
