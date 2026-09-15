/* =============================================================================
   core/flash.js — server flash messages -> toast queue (shell boot)
   -----------------------------------------------------------------------------
   shell.html embeds get_flashed_messages() as JSON in
   <script type="application/json" id="flash-data">. This module drains it
   once at load (full page loads only — fragment responses do not carry
   flashes yet; see Architecture §4.2) and maps legacy categories onto the
   toast variants.
   ========================================================================== */
import { notify } from './toast.js';

const MAPPING = {
  success: 'success',
  danger: 'error',
  error: 'error',
  warning: 'warning',
  info: 'info',
  dark: 'dark',
  primary: 'info',
  secondary: 'info',
};

export function drain() {
  const bridge = document.getElementById('flash-data');
  if (!bridge) return;
  let flashes = [];
  try {
    flashes = JSON.parse(bridge.textContent || '[]');
  } catch (error) {
    console.warn('[flash] malformed flash-data bridge', error);
  }
  bridge.remove();
  flashes.forEach((entry) => {
    if (!Array.isArray(entry) || entry.length < 2) return;
    const variant = MAPPING[entry[0]] || 'info';
    const show = notify[variant] || notify.info;
    show(entry[1]);
  });
}

drain();

export default { drain };
