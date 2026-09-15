/* =============================================================================
   pages/home.js — role-tile Start board (clock + live status row)
   -----------------------------------------------------------------------------
   spa.js contract: default export { mount, destroy }. mount() starts the
   live clock, network listeners, printer probes and the RLC pending-file
   counter; destroy() tears all of them down so htmx swaps never leak timers.

   Endpoints are the same ones V1 script.js polled (backend untouched):
     /check_printer_status?target=cashier|kitchen   every 10s, 3.5s timeout
     /pending_rlc_count                             every 20s (when enabled)
   Network state is browser-level navigator.onLine, and it gates the kitchen
   printer probe exactly like V1 (cashier probe is USB-capable locally).
   ========================================================================== */
'use strict';

const PRINTER_REFRESH_MS = 10000;
const PRINTER_TIMEOUT_MS = 3500;
const PENDING_REFRESH_MS = 20000;
const OVERVIEW_REFRESH_MS = 60000;

const timers = [];
const listeners = [];
const inFlight = [];

function el(id) {
  return document.getElementById(id);
}

/* ---- Live clock (same formatting as V1) ---------------------------------- */
function updateClock() {
  const timeNode = el('homeClockTime');
  const dateNode = el('homeClockDate');
  const now = new Date();
  if (timeNode) {
    timeNode.textContent = now.toLocaleTimeString([], {
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
    });
  }
  if (dateNode) {
    dateNode.textContent = now.toLocaleDateString([], {
      weekday: 'long',
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  }
}

/* ---- Network (browser-level, green online / muted offline) --------------- */
function setNetworkStatus() {
  const container = el('homeNetworkStatusIndicator');
  const label = el('homeNetworkLabel');
  if (!container || !label) return;
  const isOnline = navigator.onLine;
  container.classList.remove('home-status-online', 'home-status-offline');
  container.classList.add(isOnline ? 'home-status-online' : 'home-status-offline');
  label.textContent = isOnline ? 'ON' : 'OFF';
  container.setAttribute('title', isOnline ? 'Network: Online' : 'Network: Offline');
}

/* ---- Printers (cashier + kitchen, server-cached probes) ------------------ */
const printerState = {
  cashier: { checking: false, lastCheckedAt: 0, lastStatus: '' },
  kitchen: { checking: false, lastCheckedAt: 0, lastStatus: '' },
};

function setPrinterState(role, state, titleText, connectionType) {
  const prefix = role === 'kitchen' ? 'K' : 'C';
  const container = el(`home${role === 'kitchen' ? 'Kitchen' : 'Cashier'}PrinterStatusIndicator`);
  const label = el(`home${role === 'kitchen' ? 'Kitchen' : 'Cashier'}PrinterLabel`);
  if (!container || !label) return;

  container.classList.remove('home-status-online', 'home-status-offline', 'home-status-checking');
  if (state === 'connected') {
    container.classList.add('home-status-online');
    label.textContent = connectionType === 'spooler'
      ? `${prefix}:WIN`
      : (connectionType === 'usb' ? `${prefix}:USB` : `${prefix}:ON`);
  } else if (state === 'checking') {
    container.classList.add('home-status-checking');
    label.textContent = `${prefix}:CHK`;
  } else {
    container.classList.add('home-status-offline');
    label.textContent = `${prefix}:OFF`;
  }
  container.setAttribute('title', titleText);
}

function pollPrinter(role) {
  const container = el(`home${role === 'kitchen' ? 'Kitchen' : 'Cashier'}PrinterStatusIndicator`);
  if (!container) return;

  const state = printerState[role];
  const now = Date.now();
  if (state.checking || now - state.lastCheckedAt < PRINTER_REFRESH_MS) return;
  state.checking = true;
  state.lastCheckedAt = now;

  // Kitchen probes need the network; cashier probe is USB-local. Skip on cloud web server.
  if (window.location.hostname.includes('pythonanywhere') || window.location.hostname.includes('.app')) {
    state.checking = false;
    setPrinterState(role, 'disconnected', `${role === 'kitchen' ? 'Kitchen' : 'Cashier'} Printer: Cloud Mode`);
    return;
  }

  if (!navigator.onLine && role !== 'cashier') {
    state.checking = false;
    setPrinterState(role, 'disconnected', 'Kitchen Printer: Network offline');
    return;
  }

  setPrinterState(role, 'checking', `${role === 'kitchen' ? 'Kitchen' : 'Cashier'} Printer: Checking...`);

  const controller = new AbortController();
  inFlight.push(controller);
  const timeoutId = window.setTimeout(() => controller.abort(), PRINTER_TIMEOUT_MS);

  fetch(`/check_printer_status?target=${role}`, {
    cache: 'no-store',
    signal: controller.signal,
  })
    .then((response) => response.json())
    .then((data) => {
      if (data && data.connected) {
        state.lastStatus = 'connected';
        setPrinterState(
          role,
          'connected',
          `${role === 'kitchen' ? 'Kitchen' : 'Cashier'} Printer: Connected (${data.message || 'Online'})`,
          data.connection_type || ''
        );
      } else {
        state.lastStatus = 'disconnected';
        setPrinterState(
          role,
          'disconnected',
          `${role === 'kitchen' ? 'Kitchen' : 'Cashier'} Printer: ${(data && data.message) ? data.message : 'Disconnected'}`
        );
      }
    })
    .catch((error) => {
      if (error && error.name !== 'AbortError') {
        console.warn('Printer status check unavailable:', error.message || error);
      }
      state.lastStatus = 'disconnected';
      setPrinterState(role, 'disconnected', `${role === 'kitchen' ? 'Kitchen' : 'Cashier'} Printer: Offline`);
    })
    .finally(() => {
      window.clearTimeout(timeoutId);
      const idx = inFlight.indexOf(controller);
      if (idx !== -1) inFlight.splice(idx, 1);
      state.checking = false;
    });
}

/* ---- RLC pending files (badge on the cloud-upload chip) ------------------ */
function pollPending() {
  const badge = el('homePendingBadge');
  const container = el('homePendingStatusIndicator');
  if (!badge || !container) return;

  fetch('/pending_rlc_count')
    .then((response) => response.json())
    .then((data) => {
      if (!data || !data.success) return;
      const count = data.count;
      if (count > 0) {
        badge.textContent = count;
        badge.style.display = 'inline-block';
        container.setAttribute('title', `RLC Pending Files: ${count} - Automatically retried every 20 seconds when internet is restored`);
      } else {
        badge.style.display = 'none';
        container.setAttribute('title', 'RLC Pending Files: 0 - Automatically retried every 20 seconds when internet is restored');
      }
    })
    .catch((error) => {
      console.error('Error checking pending RLC files:', error);
    });
}

/* ---- Today overview (stats + recent orders from /home/overview) ----------- */
function formatMoney(value) {
  return `₱${Number(value || 0).toLocaleString('en-PH', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatItems(value) {
  const n = Number(value || 0);
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

const STATUS_CHIP = {
  completed: 'home-chip--completed',
  pending: 'home-chip--pending',
  cancelled: 'home-chip--cancelled',
  void: 'home-chip--cancelled',
};

function renderRecent(recent) {
  const list = el('homeRecentOrders');
  if (!list) return;
  list.textContent = '';

  if (!recent.length) {
    const empty = document.createElement('li');
    empty.className = 'home-recent-empty';
    empty.textContent = 'No orders yet';
    list.appendChild(empty);
    return;
  }

  for (const order of recent) {
    const row = document.createElement('li');
    row.className = 'home-recent-row';

    const orderNo = document.createElement('span');
    orderNo.className = 'home-recent-mono';
    orderNo.textContent = order.order_no || '—';

    const customer = document.createElement('span');
    customer.className = 'home-recent-customer';
    customer.textContent = order.customer || 'Walk-in';

    const time = document.createElement('span');
    time.className = 'home-recent-mono home-recent-time';
    time.textContent = order.time
      ? new Date(order.time).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
      : '—';

    const total = document.createElement('span');
    total.className = 'home-recent-mono home-recent-total';
    total.textContent = formatMoney(order.total);

    const status = document.createElement('span');
    status.className = `home-chip ${STATUS_CHIP[order.status] || 'home-chip--default'}`;
    status.textContent = (order.status || 'unknown').replace(/_/g, ' ');

    row.append(orderNo, customer, time, total, status);
    list.appendChild(row);
  }
}

function fetchOverview() {
  const controller = new AbortController();
  inFlight.push(controller);

  fetch('/home/overview', { cache: 'no-store', signal: controller.signal })
    .then((response) => response.json())
    .then((data) => {
      if (!data || !data.success) return;
      const sales = el('homeStatSales');
      const orders = el('homeStatOrders');
      const items = el('homeStatItems');
      const products = el('homeStatProducts');
      if (sales) sales.textContent = formatMoney(data.sales);
      if (orders) orders.textContent = data.orders;
      if (items) items.textContent = formatItems(data.items);
      if (products) products.textContent = data.products;
      renderRecent(data.recent || []);
    })
    .catch((error) => {
      if (controller.signal.aborted || (error && (error.name === 'AbortError' || error.message?.includes('Failed to fetch')))) return;
      console.warn('Overview fetch notice:', error);
    })
    .finally(() => {
      const idx = inFlight.indexOf(controller);
      if (idx !== -1) inFlight.splice(idx, 1);
    });
}

/* ---- Greeting line (time-of-day prefix, real clock data) ------------------ */
function setGreeting() {
  const node = el('homeGreeting');
  if (!node) return;
  const hour = new Date().getHours();
  const part = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  node.textContent = `${part}, ${node.dataset.user}`;
}

export default {
  mount() {
    setGreeting();
    updateClock();
    timers.push(window.setInterval(updateClock, 1000));

    setNetworkStatus();
    const onNetwork = () => setNetworkStatus();
    window.addEventListener('online', onNetwork);
    window.addEventListener('offline', onNetwork);
    listeners.push(['online', onNetwork], ['offline', onNetwork]);

    pollPrinter('cashier');
    pollPrinter('kitchen');
    timers.push(window.setInterval(() => pollPrinter('cashier'), PRINTER_REFRESH_MS));
    timers.push(window.setInterval(() => pollPrinter('kitchen'), PRINTER_REFRESH_MS));

    if (el('homePendingStatusIndicator')) {
      pollPending();
      timers.push(window.setInterval(pollPending, PENDING_REFRESH_MS));
    }

    fetchOverview();
    timers.push(window.setInterval(fetchOverview, OVERVIEW_REFRESH_MS));
  },

  destroy() {
    timers.forEach((id) => window.clearInterval(id));
    timers.length = 0;
    listeners.forEach(([name, fn]) => window.removeEventListener(name, fn));
    listeners.length = 0;
    inFlight.forEach((controller) => controller.abort());
    inFlight.length = 0;
  },
};
