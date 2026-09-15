/* =============================================================================
   pages/orders.js — live order board (templates/orders.html)
   -----------------------------------------------------------------------------
   spa.js contract: default export { mount, destroy }. Ports the legacy
   static/js/orders.js board 1:1 (see .ulpi/design/pos-cashier-core.md):
   dine-in floor plan with the exact V1 table math (fit scale, DB absolute
   positions on desktop, grid-snapped admin drag), room-section sidebar with
   pending counts, order type picker gated by service_types_str, the
   transfer/reserve/join modes, the presence lock (/order_presence), the
   new-order modal queue (shownOrders_<type> localStorage contract), and the
   SSE-driven realtime refresh via core/sse.js (shell-wired at Step 5).
   Modals use core/modal.js and the njX inline-overlay idiom; the live chip
   mirrors the store 'sse' slice. All POSTs go through core/api.js so the
   X-CSRFToken header is sent (V1 relied on session middleware).
   ========================================================================== */
'use strict';

import { api } from '../core/api.js';
import modal from '../core/modal.js';
import { notify } from '../core/toast.js';
import { onMessage as onSseMessage } from '../core/sse.js';
import store from '../core/store.js';

const REALTIME_REFRESH_MIN_GAP_MS = 1800;
const TRANSFER_SECTION_HOVER_DELAY_MS = 450;

/* ---- Inline SVG set mirroring partials/_icons.html for JS-rendered rows. ---- */
function iconSvg(name, size = 14) {
  const SPECS = {
    'user': '<circle cx="12" cy="7" r="4"/><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>',
    'user-check': '<path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><polyline points="17 11 19 13 23 9"/>',
    'lock': '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    'table': '<path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9"/>',
    'layers': '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
    'search': '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    'x': '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    'bell': '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
  };
  return `<svg class="icon icon-${name}" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SPECS[name] || ''}</svg>`;
}

/* ---- Board state (reset per mount) ------------------------------------------ */
let refs = {};
let selectedOrderType = 'dinein';
let selectedRoomSection = '';
let transferMode = false;
let reserveMode = false;
let joinMode = false;
let selectedSourceTable = null;
let selectedTransferSource = null;
let activeTransferDrag = null;
let selectedJoinOrders = [];
let loadTablesInFlight = null;
let loadTablesInFlightKey = '';
let loadTablesRequestSeq = 0;
let refreshOrdersInFlight = null;
let updateCountsInFlight = false;
let realtimeRefreshTimer = null;
let lastRealtimeRefreshAt = 0;
let modalQueue = [];
let isModalShowing = false;
let pageLoaded = false;
let serverSelectUsers = [];
let canSettleOrders = true;
let canAdjustTableLayout = false;
let openOverlays = [];
let overlaySeq = 0;
let timers = { fitResize: null, actionsAutoClose: null, banner: null, modalChain: null };

import {
  POS_TABLE_TEMPLATES,
  TABLE_SIZE_BY_TYPE,
  TABLE_COLOR_BY_SIZE,
  getTableColor,
  getTableSvg,
} from '../core/tables.js';

function money(value) {
  return '₱' + Number(value || 0).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/* Escape DB-derived strings before they enter innerHTML templates. */
function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ---- Inline overlay helpers (njX lib-modal-overlay idiom) -------------------- */
function modalHost() {
  let el = document.getElementById('modal-host');
  if (!el) {
    el = document.createElement('div');
    el.id = 'modal-host';
    document.body.appendChild(el);
  }
  return el;
}

function openInlineOverlay(title, bodyHtml) {
  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay';
  overlay.id = 'modal-pick-' + (++overlaySeq);
  const box = document.createElement('div');
  box.className = 'lib-modal-box';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'lib-modal-close';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.textContent = '\u2715';
  if (title) {
    const h = document.createElement('h3');
    h.className = 'lib-modal-title';
    h.textContent = title;
    box.appendChild(h);
  }
  const body = document.createElement('div');
  body.className = 'lib-modal-text orders-pick-body';
  body.innerHTML = bodyHtml;
  box.appendChild(body);
  box.appendChild(closeBtn);
  overlay.appendChild(box);
  modalHost().appendChild(overlay);
  openOverlays.push(overlay);

  const close = () => {
    const i = openOverlays.indexOf(overlay);
    if (i >= 0) openOverlays.splice(i, 1);
    window.closeModal(overlay);
    overlay.remove();
  };
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  closeBtn.addEventListener('click', close);
  window.openModal(overlay.id);
  return { overlay, close };
}

function closeOpenOverlays() {
  openOverlays.slice().forEach((overlay) => {
    window.closeModal(overlay);
    overlay.remove();
  });
  openOverlays = [];
}

/* ---- Normalization helpers ---------------------------------------------------- */
function normalizeRoomSection(value) {
  const section = String(value || '').trim();
  if (!section) return 'Main';
  const normalized = section.toLowerCase();
  if (normalized === 'pickup station' || normalized === 'takeout') return 'Takeout';
  if (normalized === 'pickup') return 'Pickup';
  if (normalized === 'delivery') return 'Delivery';
  return section;
}

function getTableLabel(tableEl) {
  return tableEl?.querySelector('.dinein-pos-table-label')?.textContent?.trim() || '';
}

function getTableRoomSection(tableEl) {
  return normalizeRoomSection(tableEl?.dataset?.roomSection || selectedRoomSection);
}

/* ---- Server select ------------------------------------------------------------ */
function getServerSelectUsers() {
  if (serverSelectUsers.length) return serverSelectUsers;
  try {
    const bridge = document.getElementById('ordersUsersData');
    const raw = bridge ? JSON.parse(bridge.textContent) : [];
    serverSelectUsers = (raw || [])
      .map((u) => ({ id: String(u.id || ''), username: String(u.username || ''), role: String(u.role || 'user') }))
      .sort((a, b) => a.username.localeCompare(b.username));
  } catch (_) {
    serverSelectUsers = [];
  }
  return serverSelectUsers;
}

function renderServerSelectList(listEl, query, letter) {
  if (!listEl) return;
  const q = (query || '').trim().toLowerCase();
  const ch = (letter || '').trim().toUpperCase();
  const users = getServerSelectUsers();
  let filtered = users;
  if (ch) {
    filtered = filtered.filter((u) => (u.username || '')[0].toUpperCase() === ch);
  }
  if (q) {
    filtered = filtered.filter((u) =>
        u.username.toLowerCase().includes(q) || u.role.toLowerCase().includes(q));
  }

  /* Toggle single-column layout when 2 or fewer results */
  listEl.classList.toggle('orders-pick-list--single-col', filtered.length <= 2);

  if (!filtered.length) {
    listEl.innerHTML = '<div class="orders-pick-empty">No user found</div>';
    return;
  }
  listEl.innerHTML = filtered.map((u) => {
    const initial = (u.username || '?')[0].toUpperCase();
    const roleCls = u.role === 'admin' ? ' orders-pick-role--admin'
      : u.role === 'cashier' ? ' orders-pick-role--cashier'
      : u.role === 'crew' ? ' orders-pick-role--crew' : '';
    return `
    <button type="button" class="orders-pick-btn server-user-pick-btn"
      data-user-id="${escapeHtml(u.id)}" data-username="${escapeHtml(u.username)}">
      <span class="orders-pick-avatar">${initial}</span>
      <span class="orders-pick-main">
        <span class="orders-pick-username">${escapeHtml(u.username)}</span>
        <span class="orders-pick-role${roleCls}">${escapeHtml(u.role)}</span>
      </span>
    </button>`;
  }).join('');
}

function openServerUserSelectModal(tableNum, orderType) {
  /* Build A-Z letter buttons. */
  const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');
  const azHtml = `<button type="button" class="orders-pick-az-btn orders-pick-az-btn--active" data-letter="">ALL</button>` + letters.map((ch) => `<button type="button" class="orders-pick-az-btn" data-letter="${ch}">${ch}</button>`).join('');

  const pick = openInlineOverlay(`Table ${tableNum} \u2014 Select User`, `
    <div class="orders-pick-az">${azHtml}</div>
    <div class="orders-pick-list"></div>
  `);
  const list = pick.overlay.querySelector('.orders-pick-list');
  const azWrap = pick.overlay.querySelector('.orders-pick-az');
  let activeLetter = '';

  function syncAZButtons() {
    const users = getServerSelectUsers();
    const present = new Set(users.map((u) => (u.username || '')[0].toUpperCase()));
    azWrap.querySelectorAll('.orders-pick-az-btn').forEach((btn) => {
      btn.classList.toggle('orders-pick-az-btn--disabled', !present.has(btn.dataset.letter));
    });
  }

  function applyFilter() {
    renderServerSelectList(list, '', activeLetter);
  }

  renderServerSelectList(list, '');
  syncAZButtons();

  /* A-Z letter click + ALL button */
  azWrap.addEventListener('click', (e) => {
    const btn = e.target.closest('.orders-pick-az-btn');
    if (!btn || btn.classList.contains('orders-pick-az-btn--disabled')) return;
    const letter = btn.dataset.letter;
    azWrap.querySelectorAll('.orders-pick-az-btn--active').forEach((b) => b.classList.remove('orders-pick-az-btn--active'));
    if (letter === '' || activeLetter === letter) {
      /* Click ALL or deselect current letter → show all */
      activeLetter = '';
      azWrap.querySelector('[data-letter=""]').classList.add('orders-pick-az-btn--active');
    } else {
      activeLetter = letter;
      btn.classList.add('orders-pick-az-btn--active');
    }
    applyFilter();
  });

  list.addEventListener('click', (event) => {
    const btn = event.target.closest('.server-user-pick-btn');
    if (!btn) return;
    const userId = btn.dataset.userId;
    const username = btn.dataset.username;
    if (!userId) return;
    pick.close();
    const next = '/pos?order_type=' + (orderType || selectedOrderType) +
      '&table_number=' + encodeURIComponent(tableNum) +
      '&server_id=' + encodeURIComponent(userId) +
      '&server_name=' + encodeURIComponent(username);
    document.documentElement.dataset.nextHref = next;
    window.location.href = next;
  });
  setTimeout(() => input && input.focus(), 120);
  return true;
}

/* ---- Table position save + admin drag ----------------------------------------- */
function saveTablePosition(tableId, xPos, yPos) {
  return api.post('/update_table_position', { id: tableId, x_pos: xPos, y_pos: yPos })
    .catch((error) => {
      console.error('Failed to save table position:', error);
    });
}

function enableAdminTableDragging(target, dimensions) {
  if (!canAdjustTableLayout || !target) return;

  const GRID_SIZE = 10;
  const TABLE_BOX_SIZE = 82;
  const TABLE_LABEL_SPACE = 24;
  const maxX = Math.max(0, Number(dimensions.width || 0) - TABLE_BOX_SIZE);
  const maxY = Math.max(0, Number(dimensions.height || 0) - TABLE_BOX_SIZE - TABLE_LABEL_SPACE);

  target.querySelectorAll('.dinein-pos-table').forEach((tableEl) => {
    tableEl.classList.add('is-draggable');

    tableEl.onmousedown = (event) => {
      if (event.button !== 0) return;

      const tableId = Number(tableEl.dataset.id || 0);
      if (!tableId) return;

      const startMouseX = event.clientX;
      const startMouseY = event.clientY;
      const startLeft = parseFloat(tableEl.style.left || '0') || 0;
      const startTop = parseFloat(tableEl.style.top || '0') || 0;
      let moved = false;

      const onMouseMove = (moveEvent) => {
        const dx = moveEvent.clientX - startMouseX;
        const dy = moveEvent.clientY - startMouseY;
        if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved = true;

        let newX = Math.round((startLeft + dx) / GRID_SIZE) * GRID_SIZE;
        let newY = Math.round((startTop + dy) / GRID_SIZE) * GRID_SIZE;
        newX = Math.max(0, Math.min(newX, maxX));
        newY = Math.max(0, Math.min(newY, maxY));

        tableEl.style.left = `${newX}px`;
        tableEl.style.top = `${newY}px`;
      };

      const onMouseUp = () => {
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);

        if (moved) {
          // Mark that a drag occurred so the click handler knows to skip
          tableEl.dataset.wasDragged = 'true';
          const finalX = parseInt(tableEl.style.left, 10) || 0;
          const finalY = parseInt(tableEl.style.top, 10) || 0;
          saveTablePosition(tableId, finalX, finalY);
        }
      };

      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', onMouseUp);
    };
  });
}

/* ---- Room section toolbar buttons (rendered into #ordersRoomList) -------------- */
function renderRoomSectionSidebar(tables) {
  const ordersRoomList = refs.ordersRoomList;
  if (!ordersRoomList) return;

  let sections = Array.from(
    new Set((tables || []).map((table) => normalizeRoomSection(table.room_section)))
  ).sort((a, b) => {
    const aLower = a.toLowerCase();
    const bLower = b.toLowerCase();
    if (aLower === 'main') return -1;
    if (bLower === 'main') return 1;
    return a.localeCompare(b);
  });

  if (selectedOrderType === 'dinein') {
    sections = sections.filter((section) => !['Takeout', 'Pickup', 'Delivery'].includes(section));
  } else if (selectedOrderType === 'takeout') {
    sections = sections.filter((section) => section === 'Takeout');
  } else if (selectedOrderType === 'pickup') {
    sections = sections.filter((section) => section === 'Pickup');
  } else if (selectedOrderType === 'delivery') {
    sections = sections.filter((section) => section === 'Delivery');
  }

  if (!sections.length) {
    selectedRoomSection = '';
    ordersRoomList.innerHTML = '';
    return;
  }

  if (!selectedRoomSection || !sections.includes(selectedRoomSection)) {
    selectedRoomSection = sections[0];
  }

  const renderItems = (counts) => {
    ordersRoomList.innerHTML = sections.map((section) => {
      const count = (counts && counts[section]) || 0;
      const itemText = count === 1 ? 'item' : 'items';
      return `
        <button class="orders-room-item ${selectedRoomSection === section ? 'is-active' : ''}"
          type="button" data-section="${escapeHtml(section)}">
          <span class="room-section-name">${escapeHtml(section)}</span>
          <span class="room-section-count">${count} ${itemText}</span>
        </button>
      `;
    }).join('');

    ordersRoomList.querySelectorAll('.orders-room-item').forEach((item) => {
      item.addEventListener('click', () => {
        const nextSection = item.dataset.section || 'all';
        if (nextSection === selectedRoomSection) return;
        selectedRoomSection = nextSection;
        loadDineInTables();
      });
    });
    bindTransferSectionHover();
  };

  // Fetch order counts for each section (cache-busted so counts refresh on every render)
  api.get('/get_table_order_counts', { t: Date.now() })
    .then((counts) => renderItems(counts || {}))
    .catch((error) => {
      console.error('Error fetching section counts:', error);
      renderItems({}); // Fallback without counts
    });
}

function bindTransferSectionHover() {
  const ordersRoomList = refs.ordersRoomList;
  if (!ordersRoomList) return;
  ordersRoomList.querySelectorAll('.orders-room-item').forEach((item) => {
    item.addEventListener('pointerleave', () => {
      if (!activeTransferDrag) item.classList.remove('transfer-section-hover');
    });
  });
}

/* ---- Order type selection -------------------------------------------------------- */
function setOrderTypeSelection(orderType) {
  selectedOrderType = orderType;

  refs.orderTypeBoxes.forEach((box) => {
    box.classList.toggle('active', box.dataset.type === orderType);
    box.setAttribute('aria-pressed', box.dataset.type === orderType ? 'true' : 'false');
  });

  const showTableBoard = Array.from(refs.orderTypeBoxes).some((box) => box.dataset.type === orderType);
  if (refs.dineInTableBoard) {
    refs.dineInTableBoard.style.display = showTableBoard ? '' : 'none';
  }

  if (orderType === 'takeout') {
    selectedRoomSection = 'Takeout';
    loadDineInTables();
  } else if (orderType === 'pickup') {
    selectedRoomSection = 'Pickup';
    loadDineInTables();
  } else if (orderType === 'delivery') {
    selectedRoomSection = 'Delivery';
    loadDineInTables();
  } else if (orderType === 'dinein') {
    if (['Takeout', 'Pickup', 'Delivery'].includes(selectedRoomSection)) {
      selectedRoomSection = '';
    }
    loadDineInTables();
  }

  refreshOrders();
}

/* ---- Floor plan ------------------------------------------------------------ */
function getFloorPlanFitScale(baseWidth, baseHeight) {
  const floorPlanHost = refs.dineInTableGrid;
  if (!floorPlanHost) return 1;

  const availableWidth = Math.max(1, floorPlanHost.clientWidth - 8);
  const fallbackHeight = Math.max(280, Math.floor(window.innerHeight * 0.45));
  const availableHeight = Math.max(1, (floorPlanHost.clientHeight ? floorPlanHost.clientHeight - 8 : fallbackHeight));

  const widthScale = availableWidth / Math.max(1, baseWidth);
  const heightScale = availableHeight / Math.max(1, baseHeight);
  const fitScale = Math.min(widthScale, heightScale, 1);

  return Math.max(0.35, fitScale);
}

async function loadDineInTables() {
  if (!refs.dineInTableGrid) return;
  const requestOrderType = selectedOrderType;
  const requestRoomSection = selectedRoomSection;
  const requestKey = `${requestOrderType}|${requestRoomSection}`;
  if (loadTablesInFlight && loadTablesInFlightKey === requestKey) return loadTablesInFlight;
  const requestSeq = ++loadTablesRequestSeq;
  loadTablesInFlightKey = requestKey;

  const floorPlanCanvas = document.getElementById('dineInFloorPlanCanvas');
  const target = floorPlanCanvas || refs.dineInTableGrid;

  // Only show loading if tables are not already rendered (prevent flash on refresh)
  const hasExistingContent = target.querySelector('.dinein-pos-table, .dinein-table-svg');
  if (!hasExistingContent) {
    target.innerHTML = `<div class="orders-empty">${iconSvg('table', 16)}Loading tables...</div>`;
  }

  loadTablesInFlight = (async () => {
    try {
      const [tablesData, occupiedData] = await Promise.all([
        api.get('/get_restaurant_tables'),
        api.get('/get_occupied_tables', {
          order_type: requestOrderType,
          room_section: requestRoomSection,
          t: Date.now(),
        }),
      ]);

      if (requestSeq !== loadTablesRequestSeq ||
          requestOrderType !== selectedOrderType ||
          requestRoomSection !== selectedRoomSection) {
        return;
      }

      if (!tablesData.success) {
        target.innerHTML = `<div class="orders-empty">${iconSvg('alert', 16)}Unable to load tables.</div>`;
        return;
      }

      const occupiedSet = new Set((occupiedData.occupied_tables || []).map((n) => String(n)));
      // Store table to order ID mapping for redirecting to settlement page
      const tableToOrderMap = occupiedData.table_orders || {};

      const tables = (tablesData.tables || []).slice().sort((a, b) =>
        String(a.table_number).localeCompare(String(b.table_number)));
      renderRoomSectionSidebar(tables);
      const visibleTables = tables.filter((table) =>
        normalizeRoomSection(table.room_section) === selectedRoomSection);

      if (!tables.length) {
        target.innerHTML = `<div class="orders-empty">${iconSvg('table', 16)}No tables configured yet.</div>`;
        return;
      }

      if (!visibleTables.length) {
        target.style.width = '100%';
        target.style.height = '100%';
        target.innerHTML = `<div class="orders-empty">${iconSvg('layers', 16)}No tables in ${selectedRoomSection}.</div>`;
        return;
      }

      const TABLE_BOX_SIZE = 86;
      const TABLE_LABEL_SPACE = 24;
      const SAFE_PADDING = 20;
      const xValues = visibleTables.map((t) => Number(t.x_pos || 0));
      const yValues = visibleTables.map((t) => Number(t.y_pos || 0));
      const minX = Math.min(...xValues);
      const maxX = Math.max(...xValues);
      const minY = Math.min(...yValues);
      const maxY = Math.max(...yValues);
      const preserveDbAbsolutePositions = window.matchMedia('(min-width: 992px)').matches;

      let canvasWidth;
      let canvasHeight;
      if (preserveDbAbsolutePositions) {
        // Desktop: keep DB coordinates exactly as authored.
        canvasWidth = Math.max(620, maxX + TABLE_BOX_SIZE + SAFE_PADDING);
        canvasHeight = Math.max(360, maxY + TABLE_BOX_SIZE + TABLE_LABEL_SPACE + SAFE_PADDING);
      } else {
        const contentWidth = (maxX - minX) + TABLE_BOX_SIZE + (SAFE_PADDING * 2);
        const contentHeight = (maxY - minY) + TABLE_BOX_SIZE + TABLE_LABEL_SPACE + (SAFE_PADDING * 2);
        canvasWidth = Math.max(320, contentWidth);
        canvasHeight = Math.max(220, contentHeight);
      }
      const fitScale = preserveDbAbsolutePositions ? 1 : getFloorPlanFitScale(canvasWidth, canvasHeight);
      const scaledCanvasWidth = Math.max(320, Math.round(canvasWidth * fitScale));
      const scaledCanvasHeight = Math.max(220, Math.round(canvasHeight * fitScale));
      const scaledTableSize = Math.max(48, Math.round(120 * fitScale));

      target.style.width = `${scaledCanvasWidth}px`;
      target.style.height = `${scaledCanvasHeight}px`;

      target.innerHTML = visibleTables.map((table) => {
        const tableNo = String(table.table_number || '');
        // Use table_number directly as it now contains the full display name (e.g., "VIP 1")
        const tableLabel = tableNo;
        // Mark as occupied based on current order type
        const isOccupied = occupiedSet.has(tableNo);
        const template = POS_TABLE_TEMPLATES[table.table_type] || POS_TABLE_TEMPLATES.square_small;
        const tableSize = TABLE_SIZE_BY_TYPE[table.table_type] || 2;
        const tableColor = TABLE_COLOR_BY_SIZE[tableSize] || '#6FA8FF';
        const tableSvg = template.svg.replace(/{COLOR}/g, tableColor);
        const tableRoomSection = normalizeRoomSection(table.room_section);
        const tableLabelEsc = escapeHtml(tableLabel);
        const rawX = Number(table.x_pos || 0);
        const rawY = Number(table.y_pos || 0);
        const normalizedX = rawX - minX + SAFE_PADDING;
        const normalizedY = rawY - minY + SAFE_PADDING;
        const renderX = preserveDbAbsolutePositions ? rawX : normalizedX;
        const renderY = preserveDbAbsolutePositions ? rawY : normalizedY;
        const left = Math.round(renderX * fitScale);
        const top = Math.round(renderY * fitScale);
        const rotation = Number(table.rotation || 0);

        return `
          <div
            class="dinein-pos-table ${isOccupied ? 'is-occupied' : ''} ${!isOccupied && table.status === 'reserved' ? 'is-reserved' : ''}"
            data-id="${table.id || ''}"
            data-type="${table.table_type || ''}"
            data-room-section="${tableRoomSection}"
            title="${tableLabelEsc}${isOccupied ? ' - Pending order' : (table.status === 'reserved' ? ' - Reserved' : '')}${canAdjustTableLayout ? ' - Drag to move' : ''}"
            style="left:${left}px; top:${top}px; width:${scaledTableSize}px; height:${scaledTableSize}px; --tablet-fit-scale:${fitScale}; transform: rotate(${rotation}deg);"
          >
            ${tableSvg}
            <span class="dinein-pos-table-label">${tableLabelEsc}</span>
            ${isOccupied ? `<span class="table-occupied-dot"><span class="person-icon-wrapper"><img src="/assets/img/307730.svg" alt="" class="person-silhouette-img" draggable="false"/></span></span>` : ''}
            ${!isOccupied && table.status === 'reserved' ? `<span class="table-reserved-badge">${iconSvg('lock', 10)}Reserved</span>` : ''}
          </div>
        `;
      }).join('');

      enableAdminTableDragging(target, { width: scaledCanvasWidth, height: scaledCanvasHeight });

      // Add click handler on tables to redirect to POS or Settlement
      target.querySelectorAll('.dinein-pos-table').forEach((tableEl) => {
        tableEl.addEventListener('click', async function (e) {
          // Skip if in transfer mode (person icon is the transfer drag handle there)
          if (transferMode) return;

          // Skip if admin was dragging the table (drag handler sets wasDragged flag)
          if (tableEl.dataset.wasDragged === 'true') {
            delete tableEl.dataset.wasDragged;
            return;
          }

          const tableLabel = tableEl.querySelector('.dinein-pos-table-label');
          if (!tableLabel) return;

          // Use the full table label as the table number (supports string names like "VIP 1")
          const tableNum = tableLabel.textContent.trim();
          if (!tableNum) return;

          if (reserveMode) {
            await toggleTableReservation(tableEl, tableNum);
            return;
          }

          if (joinMode) {
            await handleJoinTableClick(tableEl, tableNum, tableToOrderMap);
            return;
          }

          // If table is occupied, redirect to settlement page for that order
          if (tableEl.classList.contains('is-occupied')) {
            const destinationBasePath = canSettleOrders ? '/settle_order/' : '/order/';
            const rawEntry = tableToOrderMap[tableNum];
            // Normalize: new format = array of objects, legacy = single ID
            const tableOrders = Array.isArray(rawEntry)
              ? rawEntry
              : (rawEntry ? [{ id: rawEntry, order_no: String(rawEntry), customer_name: '', total: 0, item_count: 0 }] : []);
            if (!tableOrders.length) return;

            // Single order → go straight to settle
            if (tableOrders.length === 1) {
              await openOrderRoute(destinationBasePath + tableOrders[0].id, tableOrders[0].id);
              return;
            }

            // Multiple orders (split bill) → show picker modal
            openTableOrderPicker(tableNum, tableOrders, destinationBasePath);
            return;
          }

          if (tableEl.classList.contains('is-reserved')) {
            modal.alert('Table Reserved',
              `Table ${tableNum} is reserved and cannot be ordered. Use Reserve mode to release it first.`);
            return;
          }

          // For unoccupied tables, show simple user selection modal (no keyboard)
          if (!openServerUserSelectModal(tableNum, selectedOrderType)) {
            // Fallback: redirect directly to POS with current user
            const next = '/pos?order_type=' + selectedOrderType + '&table_number=' + encodeURIComponent(tableNum);
            document.documentElement.dataset.nextHref = next;
            window.location.href = next;
          }
        });
      });
    } catch (error) {
      console.error('Failed to load dine-in tables:', error);
      target.innerHTML = `<div class="orders-empty">${iconSvg('alert', 16)}Network error loading tables.</div>`;
    } finally {
      if (requestSeq === loadTablesRequestSeq) {
        loadTablesInFlight = null;
        loadTablesInFlightKey = '';
      }
    }
  })();

  return loadTablesInFlight;
}

/* ---- Split-bill order picker ------------------------------------------------------ */
function openTableOrderPicker(tableNum, tableOrders, destinationBasePath) {
  const rows = tableOrders.map((o, idx) => `
    <button type="button" class="orders-pick-btn table-order-pick-btn"
      data-order-id="${o.id}" data-destination="${destinationBasePath}${o.id}">
      <span class="orders-pick-main">
        <span style="font-size:0.66rem;font-weight:600;text-transform:uppercase;letter-spacing:0.07em;color:var(--text-secondary);">Order ${idx + 1}</span>
        <div style="font-size:0.95rem;font-weight:700;">#${escapeHtml(o.order_no)}</div>
        ${o.customer_name ? `<div class="orders-pick-sub">${iconSvg('user', 12)}${escapeHtml(o.customer_name)}</div>` : ''}
        <div class="orders-pick-sub">${iconSvg('layers', 12)}${o.item_count} item${o.item_count !== 1 ? 's' : ''}</div>
      </span>
      <span class="orders-pick-amount">${money(o.total || 0)}<div style="font-size:0.7rem;font-weight:500;color:#22c55e;margin-top:0.2rem;">${canSettleOrders ? 'Settle' : 'View'}</div></span>
    </button>
  `).join('');

  const pick = openInlineOverlay(`Table ${tableNum} — Select Order`, `
    <div class="orders-pick-list">${rows}</div>
  `);
  pick.overlay.querySelectorAll('.table-order-pick-btn').forEach((btn) => {
    btn.addEventListener('click', async function () {
      const oid = this.getAttribute('data-order-id');
      const destination = this.getAttribute('data-destination');
      const ok = await canOpenOrder(oid);
      if (!ok) return;
      pick.close();
      document.documentElement.dataset.nextHref = destination;
      window.location.href = destination;
    });
  });
}

/* ---- Presence lock ---------------------------------------------------------------- */
function showOrderLockConflictModal(message) {
  const text = message || 'Order is currently being processed by another user. Please wait until they finish.';
  modal.alert('Order In Use', text, { confirmLabel: 'OK' });
}

async function canOpenOrder(orderId) {
  try {
    await api.post(`/order_presence/${orderId}`);
    return true;
  } catch (err) {
    if (err.status === 409 || (err.detail && err.detail.lock_conflict)) {
      showOrderLockConflictModal(err.message);
      return false;
    }
    return true;
  }
}

async function openOrderRoute(destinationPath, orderId) {
  const allowed = await canOpenOrder(orderId);
  if (!allowed) return;
  document.documentElement.dataset.nextHref = destinationPath;
  window.location.href = destinationPath;
}

async function handleSettleOrder(orderId) {
  if (!canSettleOrders) {
    const view = await modal.alert(
      'Settlement Not Allowed',
      'Crew accounts can manage orders but cannot settle payments.',
      { confirmLabel: 'View Order' }
    );
    await openOrderRoute(`/order/${orderId}`, orderId);
    return;
  }
  await openOrderRoute(`/settle_order/${orderId}`, orderId);
}

/* ---- New order modal queue --------------------------------------------------------- */
function addToModalQueue(order) {
  // Check if order is older than 30 minutes
  const orderTime = new Date(order.timestamp);
  const currentTime = new Date();
  const timeDifferenceMinutes = (currentTime - orderTime) / (1000 * 60);

  // Only show modal if order is less than 30 minutes old
  if (timeDifferenceMinutes > 30) {
    console.log(`Order ${order.order_no} is older than 30 minutes, skipping modal notification`);
    return;
  }

  // Check if this order is already in the queue to prevent duplicates
  const alreadyInQueue = modalQueue.some((queuedOrder) => queuedOrder.id === order.id);
  if (alreadyInQueue) {
    console.log(`Order ${order.order_no} is already in queue, skipping`);
    return;
  }

  modalQueue.push(order);

  if (!isModalShowing) {
    showNextModal();
  }
}

function showNextModal() {
  if (modalQueue.length === 0) {
    isModalShowing = false;
    return;
  }

  isModalShowing = true;
  const order = modalQueue.shift();
  showOrderPlacedModal(order);
}

function formatOrderPlacedText(order) {
  let tableText = 'N/A';
  if (order.tables) {
    if (Array.isArray(order.tables) && order.tables.length > 0) {
      tableText = order.tables.join(', ');
    } else if (typeof order.tables === 'string' && order.tables.trim()) {
      tableText = order.tables.trim();
    }
  }

  const orderTypeLabel = order.order_type === 'dinein' ? 'Dine-in'
    : order.order_type === 'takeout' ? 'Takeout'
    : order.order_type === 'pickup' ? 'Pickup'
    : order.order_type === 'delivery' ? 'Delivery'
    : (order.order_type || 'N/A');

  let formattedTime = '';
  if (order.timestamp) {
    try {
      const date = new Date(order.timestamp);
      const formattedDate = date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      });
      const formattedTimeStr = date.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
      });
      formattedTime = `${formattedDate} ${formattedTimeStr}`;
    } catch (_) {}
  }
  if (!formattedTime) {
    const now = new Date();
    formattedTime = `${now.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })} ${now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`;
  }

  let text = `#${order.order_no}\nTable #: ${tableText}\nCustomer: ${order.customer_name || 'Walk-in Customer'}\nType: ${orderTypeLabel}\nDate: ${formattedTime}`;

  if (order.printed !== undefined) {
    text += `\nPrint Status: ${order.printed ? 'Printed' : 'Not Printed'}`;
  }

  return text;
}

function showOrderPlacedModal(order) {
  const text = formatOrderPlacedText(order);
  return modal.success('Order Has Been Placed', text, {
    confirmLabel: 'OK',
    timer: 6000,
  }).then(() => {
    if (timers.modalChain) clearTimeout(timers.modalChain);
    timers.modalChain = setTimeout(showNextModal, 500);
  });
}

/* ---- Orders refresh ---------------------------------------------------------------- */
function refreshOrders() {
  if (refreshOrdersInFlight) return refreshOrdersInFlight;

  refreshOrdersInFlight = api.get('/get_orders_data', selectedOrderType ? { type: selectedOrderType } : undefined)
    .then((data) => {
      // Only show modal for new orders that match the current order type
      if (data.orders.length > 0 && pageLoaded) {
        const shownOrdersKey = 'shownOrders_' + (selectedOrderType || 'all');
        let shownOrders = [];
        try {
          shownOrders = JSON.parse(localStorage.getItem(shownOrdersKey) || '[]');
        } catch (_) {}

        let updated = false;
        data.orders.forEach((order) => {
          if (!shownOrders.includes(order.id)) {
            const orderTime = new Date(order.timestamp);
            const currentTime = new Date();
            const ageInSeconds = (currentTime.getTime() - orderTime.getTime()) / 1000;

            // Allow up to 10 minutes (600s) difference to handle PC/server clock skew
            if (Math.abs(ageInSeconds) < 600 && (!selectedOrderType || order.order_type === selectedOrderType)) {
              addToModalQueue(order);
              shownOrders.push(order.id);
              updated = true;
            }
          }
        });

        if (updated) {
          if (shownOrders.length > 50) {
            shownOrders = shownOrders.slice(-50);
          }
          localStorage.setItem(shownOrdersKey, JSON.stringify(shownOrders));
        }
      }
    })
    .catch((error) => console.error('Error refreshing orders:', error))
    .finally(() => {
      refreshOrdersInFlight = null;
    });

  // Fetch counts for all order types
  updateOrderTypeCounts();
  return refreshOrdersInFlight;
}

function refreshRealtimeOrderState() {
  if (document.hidden) return;
  lastRealtimeRefreshAt = Date.now();
  const doc = document.documentElement;
  doc.dataset.boardRefresh = String((Number(doc.dataset.boardRefresh || 0) + 1));
  loadDineInTables();
  refreshOrders();
}

function scheduleRealtimeOrderStateRefresh(delay = 250) {
  if (document.hidden) return;
  if (realtimeRefreshTimer) return;

  const elapsed = Date.now() - lastRealtimeRefreshAt;
  const wait = Math.max(delay, REALTIME_REFRESH_MIN_GAP_MS - elapsed, 0);
  realtimeRefreshTimer = setTimeout(() => {
    realtimeRefreshTimer = null;
    refreshRealtimeOrderState();
  }, wait);
}

function updateOrderTypeCounts() {
  if (updateCountsInFlight) return;
  updateCountsInFlight = true;
  const orderTypes = Array.from(refs.orderTypeBoxes).map((box) => box.dataset.type);

  Promise.all(orderTypes.map((type) =>
    api.get('/get_orders_data', { type, status: 'pending' })
      .then((data) => {
        const countElement = document.getElementById(`count-${type}`);
        if (countElement) {
          const count = data.orders ? data.orders.length : 0;
          countElement.textContent = count;
          countElement.style.display = count > 0 ? 'flex' : 'none';
        }
      })
      .catch((error) => console.error(`Error fetching count for ${type}:`, error))
  )).finally(() => {
    updateCountsInFlight = false;
  });
}

/* ---- Transfer mode ----------------------------------------------------------------- */
function applyTransferTargetHighlights() {
  document.querySelectorAll('.dinein-pos-table:not(.is-occupied):not(.is-reserved)').forEach((table) => {
    table.classList.add('transfer-target');
  });
}

function clearTransferSectionHover() {
  if (activeTransferDrag?.sectionHoverTimer) {
    clearTimeout(activeTransferDrag.sectionHoverTimer);
    activeTransferDrag.sectionHoverTimer = null;
  }
  if (refs.ordersRoomList) {
    refs.ordersRoomList.querySelectorAll('.orders-room-item').forEach((item) => {
      item.classList.remove('transfer-section-hover');
    });
  }
  if (activeTransferDrag) activeTransferDrag.pendingSection = '';
}

function updateTransferSectionHover(clientX, clientY) {
  if (!activeTransferDrag || !refs.ordersRoomList) return;

  const sectionItem = document.elementFromPoint(clientX, clientY)?.closest('.orders-room-item');
  const nextSection = sectionItem?.dataset?.section || '';

  refs.ordersRoomList.querySelectorAll('.orders-room-item').forEach((item) => {
    item.classList.toggle('transfer-section-hover', !!nextSection && item.dataset.section === nextSection);
  });

  if (!nextSection || nextSection === selectedRoomSection) {
    if (activeTransferDrag.pendingSection && activeTransferDrag.pendingSection !== nextSection) {
      clearTransferSectionHover();
    }
    return;
  }

  if (activeTransferDrag.pendingSection === nextSection) return;

  if (activeTransferDrag.sectionHoverTimer) {
    clearTimeout(activeTransferDrag.sectionHoverTimer);
  }
  activeTransferDrag.pendingSection = nextSection;
  activeTransferDrag.sectionHoverTimer = setTimeout(() => {
    if (!activeTransferDrag || activeTransferDrag.pendingSection !== nextSection || selectedRoomSection === nextSection) return;
    selectedRoomSection = nextSection;
    activeTransferDrag.pendingSection = '';
    loadDineInTables();
  }, TRANSFER_SECTION_HOVER_DELAY_MS);
}

function clearTransferMode() {
  transferMode = false;
  selectedSourceTable = null;
  selectedTransferSource = null;
  clearTransferSectionHover();
  activeTransferDrag = null;
  if (refs.btnTransferTables) refs.btnTransferTables.classList.remove('active');
  if (refs.dineInTableBoard) refs.dineInTableBoard.classList.remove('transfer-mode-active');
  document.querySelectorAll('.dinein-pos-table').forEach((table) => {
    table.style.cursor = '';
    table.classList.remove('transfer-source', 'transfer-target', 'transfer-hover');
  });
  document.querySelectorAll('.transfer-drag-ghost').forEach((g) => g.remove());
}

function clearReserveMode() {
  reserveMode = false;
  if (refs.btnReserveTables) refs.btnReserveTables.classList.remove('active');
  if (refs.dineInTableBoard) refs.dineInTableBoard.classList.remove('reserve-mode-active');
  document.querySelectorAll('.dinein-pos-table').forEach((table) => {
    table.classList.remove('reserve-target');
  });
}

function clearJoinMode() {
  joinMode = false;
  selectedJoinOrders = [];
  if (refs.btnJoinTables) refs.btnJoinTables.classList.remove('active');
  if (refs.dineInTableBoard) refs.dineInTableBoard.classList.remove('join-mode-active');
  document.querySelectorAll('.dinein-pos-table').forEach((table) => {
    table.classList.remove('join-source');
  });
}

function enableTransferDrag() {
  // Use event delegation on the floor plan canvas for better touch handling
  const floorPlan = document.getElementById('dineInFloorPlanCanvas');
  if (!floorPlan) return;

  // Remove existing listeners to prevent duplicates
  floorPlan.removeEventListener('pointerdown', handlePointerDown);
  floorPlan.addEventListener('pointerdown', handlePointerDown);
}

function handlePointerDown(e) {
  if (!transferMode) return;

  // Check if clicking on person icon or its wrapper
  const icon = e.target.closest('.table-occupied-dot') || e.target.closest('.person-icon-wrapper')?.closest('.table-occupied-dot');
  if (!icon) return;

  const tableEl = icon.closest('.dinein-pos-table');
  if (!tableEl || !tableEl.classList.contains('is-occupied')) return;

  // Prevent default browser behaviors
  e.preventDefault();
  e.stopPropagation();

  // Capture pointer for drag
  if (icon.setPointerCapture) {
    icon.setPointerCapture(e.pointerId);
  }

  startTransferDrag(icon, tableEl, e.clientX, e.clientY);
}

// Shared drag logic for both mouse and touch
function startTransferDrag(icon, tableEl, startX, startY) {
  selectedSourceTable = tableEl;
  selectedTransferSource = {
    tableNum: getTableLabel(tableEl),
    roomSection: getTableRoomSection(tableEl),
  };
  if (!selectedTransferSource.tableNum) return;
  tableEl.classList.add('transfer-source');

  // Create a visual ghost element that follows the cursor
  const ghost = document.createElement('div');
  ghost.className = 'transfer-drag-ghost';
  ghost.innerHTML = icon.innerHTML;
  document.body.appendChild(ghost);

  // Position ghost at cursor
  const iconRect = icon.getBoundingClientRect();
  const offsetX = startX - iconRect.left;
  const offsetY = startY - iconRect.top;
  ghost.style.left = (startX - offsetX) + 'px';
  ghost.style.top = (startY - offsetY) + 'px';
  activeTransferDrag = {
    ghost,
    source: selectedTransferSource,
    pendingSection: '',
    sectionHoverTimer: null,
  };

  // Dim the original icon
  icon.style.opacity = '0.3';

  // Highlight available (non-occupied) tables as potential targets
  applyTransferTargetHighlights();

  let currentHoverTarget = null;

  const onMouseMove = (e) => {
    // Move ghost with cursor
    ghost.style.left = (e.clientX - offsetX) + 'px';
    ghost.style.top = (e.clientY - offsetY) + 'px';

    // Detect hover over target tables
    ghost.style.display = 'none';
    const elementBelow = document.elementFromPoint(e.clientX, e.clientY);
    ghost.style.display = '';

    const hoverTable = elementBelow?.closest('.dinein-pos-table');

    // Remove previous hover highlight
    if (currentHoverTarget && currentHoverTarget !== hoverTable) {
      currentHoverTarget.classList.remove('transfer-hover');
    }

    if (hoverTable && hoverTable !== selectedSourceTable && !hoverTable.classList.contains('is-occupied') && !hoverTable.classList.contains('is-reserved')) {
      hoverTable.classList.add('transfer-hover');
      currentHoverTarget = hoverTable;
    } else {
      currentHoverTarget = null;
    }
    updateTransferSectionHover(e.clientX, e.clientY);
  };

  const onTouchMove = (e) => {
    e.preventDefault();
    const touch = e.touches[0];

    // Move ghost with touch
    ghost.style.left = (touch.clientX - offsetX) + 'px';
    ghost.style.top = (touch.clientY - offsetY) + 'px';

    // Detect hover over target tables
    ghost.style.display = 'none';
    const elementBelow = document.elementFromPoint(touch.clientX, touch.clientY);
    ghost.style.display = '';

    const hoverTable = elementBelow?.closest('.dinein-pos-table');

    // Remove previous hover highlight
    if (currentHoverTarget && currentHoverTarget !== hoverTable) {
      currentHoverTarget.classList.remove('transfer-hover');
    }

    if (hoverTable && hoverTable !== selectedSourceTable && !hoverTable.classList.contains('is-occupied') && !hoverTable.classList.contains('is-reserved')) {
      hoverTable.classList.add('transfer-hover');
      currentHoverTarget = hoverTable;
    } else {
      currentHoverTarget = null;
    }
    updateTransferSectionHover(touch.clientX, touch.clientY);
  };

  const endDrag = (clientX, clientY) => {
    const source = activeTransferDrag?.source || selectedTransferSource;
    clearTransferSectionHover();

    // Remove ghost
    ghost.remove();

    // Restore original icon opacity
    icon.style.opacity = '1';

    // Remove all transfer highlights
    document.querySelectorAll('.dinein-pos-table').forEach((t) => {
      t.classList.remove('transfer-target', 'transfer-hover', 'transfer-source');
    });

    // Re-apply transfer-source class if still in transfer mode
    if (transferMode) {
      document.querySelectorAll('.dinein-pos-table.is-occupied').forEach((t) => {
        t.classList.add('transfer-source');
      });
    }

    // Find target table at drop position
    const targetTable = document.elementFromPoint(clientX, clientY)?.closest('.dinein-pos-table');

    if (targetTable && targetTable !== selectedSourceTable && !targetTable.classList.contains('is-occupied') && !targetTable.classList.contains('is-reserved')) {
      const targetTableNum = getTableLabel(targetTable);
      const targetSection = getTableRoomSection(targetTable);
      const isSameTable = source &&
        source.tableNum === targetTableNum &&
        source.roomSection === targetSection;

      if (source?.tableNum && targetTableNum && !isSameTable) {
        transferTableOrder(source.tableNum, targetTableNum, source.roomSection, targetSection, selectedSourceTable, targetTable);
      }
    }

    selectedSourceTable = null;
    selectedTransferSource = null;
    activeTransferDrag = null;
  };

  const onMouseUp = (e) => {
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
    endDrag(e.clientX, e.clientY);
  };

  const onTouchEnd = (e) => {
    document.removeEventListener('touchmove', onTouchMove);
    document.removeEventListener('touchend', onTouchEnd);
    document.removeEventListener('touchcancel', onTouchEnd);

    const touch = e.changedTouches[0];
    endDrag(touch.clientX, touch.clientY);
  };

  document.addEventListener('mousemove', onMouseMove);
  document.addEventListener('mouseup', onMouseUp);
  document.addEventListener('touchmove', onTouchMove, { passive: false });
  document.addEventListener('touchend', onTouchEnd);
  document.addEventListener('touchcancel', onTouchEnd);
}

// Function to transfer table order via backend
function transferTableOrder(sourceTableNum, targetTableNum, sourceSection, targetSection) {
  api.post('/transfer_table_order', {
    source_table: sourceTableNum,
    target_table: targetTableNum,
    source_section: sourceSection || selectedRoomSection,
    target_section: targetSection || selectedRoomSection,
    order_type: 'dinein',
    room_section: targetSection || selectedRoomSection,
  })
    .then((data) => {
      if (data.success) {
        // Exit transfer mode
        clearTransferMode();

        // Reload tables so user can click the occupied table to go to settlement
        if (targetSection) selectedRoomSection = targetSection;
        loadDineInTables();
      } else {
        modal.alert('Transfer Failed', data.message || 'Failed to transfer table');
      }
    })
    .catch((error) => {
      console.error('Error transferring table:', error);
      modal.alert('Error', error.message || 'Error transferring table');
    });
}

/* ---- Reserve + join modes ----------------------------------------------------------- */
function getSingleTableOrder(tableNum, tableToOrderMap) {
  const rawEntry = tableToOrderMap[tableNum];
  const tableOrders = Array.isArray(rawEntry)
    ? rawEntry
    : (rawEntry ? [{ id: rawEntry, order_no: String(rawEntry), customer_name: '', total: 0, item_count: 0 }] : []);
  if (tableOrders.length !== 1) return { order: null, count: tableOrders.length };
  return { order: tableOrders[0], count: 1 };
}

async function handleJoinTableClick(tableEl, tableNum, tableToOrderMap) {
  if (!tableEl.classList.contains('is-occupied')) {
    modal.alert('No Order', 'Join can only merge tables that already have orders.');
    return;
  }

  const lookup = getSingleTableOrder(tableNum, tableToOrderMap);
  if (!lookup.order) {
    modal.alert('Choose Order First',
      lookup.count > 1
        ? 'This table has multiple orders. Open the table and settle or manage one order first.'
        : 'No pending order was found for this table.');
    return;
  }

  const existingIndex = selectedJoinOrders.findIndex((entry) => entry.order.id === lookup.order.id);
  if (existingIndex >= 0) {
    selectedJoinOrders.splice(existingIndex, 1);
    tableEl.classList.remove('join-source');
    return;
  }

  selectedJoinOrders.push({ tableNum, order: lookup.order });
  tableEl.classList.add('join-source');
}

async function confirmSelectedJoinTables() {
  if (selectedJoinOrders.length < 2) {
    modal.alert('Select Tables', 'Select at least two occupied tables, then press Join again.');
    return;
  }

  const mainTable = selectedJoinOrders[0].tableNum;
  const joinedTables = selectedJoinOrders.slice(1).map((entry) => entry.tableNum).join(', ');
  const confirmed = await modal.confirm('Join Tables?', `Merge ${joinedTables} into Table ${mainTable}?`, {
    confirmLabel: 'Join Tables',
    cancelLabel: 'Cancel',
  });
  if (!confirmed) return;

  try {
    const data = await api.post('/join_table_orders', {
      order_ids: selectedJoinOrders.map((entry) => entry.order.id),
    });
    if (!data.success) {
      throw new Error(data.message || 'Failed to join tables');
    }

    clearJoinMode();
    loadDineInTables();
    refreshOrders();
    notify.success(data.message || 'Orders were merged successfully.');
  } catch (error) {
    console.error('Error joining tables:', error);
    modal.alert('Join Failed', error.message || 'Failed to join tables');
  }
}

async function toggleTableReservation(tableEl, tableNum) {
  if (!tableEl || tableEl.classList.contains('is-occupied')) {
    modal.alert('Table Has Order', 'Occupied tables cannot be reserved.');
    return;
  }

  const tableId = tableEl.dataset.id || '';
  const nextStatus = tableEl.classList.contains('is-reserved') ? 'available' : 'reserved';
  const actionLabel = nextStatus === 'reserved' ? 'reserve' : 'release';

  try {
    const confirmed = await modal.confirm(
      `${nextStatus === 'reserved' ? 'Reserve' : 'Release'} Table ${tableNum}?`,
      nextStatus === 'reserved'
        ? 'This table will be blocked from new orders.'
        : 'This table will become available for ordering.',
      { confirmLabel: nextStatus === 'reserved' ? 'Reserve' : 'Release', cancelLabel: 'Cancel' }
    );
    if (!confirmed) return;

    const data = await api.post('/set_table_reservation', {
      table_id: tableId,
      table_number: tableNum,
      room_section: selectedRoomSection,
      status: nextStatus,
    });
    if (!data.success) {
      throw new Error(data.message || `Could not ${actionLabel} table`);
    }

    clearReserveMode();
    loadDineInTables();
    notify.success(data.message || `Table ${tableNum} updated.`);
  } catch (error) {
    console.error('Error updating table reservation:', error);
    modal.alert('Reservation Failed', error.message || `Could not ${actionLabel} table`);
  }
}

/* ---- Mount / destroy ---------------------------------------------------------------- */
function resetState() {
  selectedOrderType = 'dinein';
  selectedRoomSection = '';
  transferMode = false;
  reserveMode = false;
  joinMode = false;
  selectedSourceTable = null;
  selectedTransferSource = null;
  activeTransferDrag = null;
  selectedJoinOrders = [];
  loadTablesInFlight = null;
  loadTablesInFlightKey = '';
  loadTablesRequestSeq = 0;
  refreshOrdersInFlight = null;
  updateCountsInFlight = false;
  realtimeRefreshTimer = null;
  lastRealtimeRefreshAt = 0;
  modalQueue = [];
  isModalShowing = false;
  pageLoaded = false;
  serverSelectUsers = [];
  openOverlays = [];
}

function onSseEvent(event) {
  const payload = event.detail;
  if (!payload) return;

  // Handle updates that can change table occupancy immediately without throttling delay.
  if (typeof payload === 'object' && payload.type &&
      ['table_transfer', 'order_created', 'order_status', 'order_lock', 'update'].includes(payload.type)) {
    refreshRealtimeOrderState();
    return;
  }

  // Handle simple string messages (backward compatibility)
  if (payload === 'update') {
    refreshRealtimeOrderState();
  }
}

function updateLiveChip(state) {
  const chip = document.getElementById('ordersLiveChip');
  const text = document.getElementById('ordersLiveText');
  if (!chip || !text) return;
  const labels = { connected: 'Live', connecting: 'Connecting…', retry: 'Retry', off: 'Off' };
  chip.dataset.state = state;
  text.textContent = labels[state] || state;
}

export function mount() {
  resetState();

  refs = {
    orderTypeBoxes: document.querySelectorAll('.order-type-box'),
    dineInTableBoard: document.getElementById('dineInTableBoard'),
    dineInTableGrid: document.getElementById('dineInTableGrid'),
    ordersRoomList: document.getElementById('ordersRoomList'),
    btnTransferTables: document.getElementById('btnTransferTables'),
    btnReserveTables: document.getElementById('btnReserveTables'),
    btnJoinTables: document.getElementById('btnJoinTables'),
    btnManualDrawer: document.getElementById('btnManualDrawer'),
  };

  const currentUserRole = (document.body?.dataset?.userRole || '').toLowerCase();
  canSettleOrders = currentUserRole !== 'crew';
  canAdjustTableLayout = currentUserRole === 'admin';

  // Live chip mirrors the shell's SSE store.
  const unsubscribeStore = store.subscribe('sse', updateLiveChip);
  updateLiveChip(store.get('sse') || 'connecting');

  // SSE events → debounced board refresh (core/sse.js dispatches pos:sse).
  const unsubscribeSse = onSseMessage(onSseEvent);

  // Order type picker.
  refs.orderTypeBoxes.forEach((box) => {
    box.addEventListener('click', function () {
      const nextType = this.dataset.type;
      if (!nextType) return;
      setOrderTypeSelection(nextType);
    });
  });

  // Table actions toggle (draggable, auto-closes after 1 minute, remembers position).
  const tableActionsToggle = document.getElementById('tableActionsToggle');
  const tableActionsStack = document.getElementById('tableActionsStack');
  if (tableActionsToggle && tableActionsStack) {
    const STORAGE_KEY = 'pos_tableActionsPos';

    // Restore saved position
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (saved && typeof saved.left === 'number' && typeof saved.top === 'number') {
        tableActionsToggle.style.left = saved.left + 'px';
        tableActionsToggle.style.top = saved.top + 'px';
        tableActionsStack.style.left = saved.left + 'px';
        tableActionsStack.style.top = (saved.top + 52) + 'px';
      }
    } catch (e) { /* ignore */ }

    // Draggable logic
    let isDragging = false;
    let offsetX = 0;
    let offsetY = 0;
    let hasDragged = false;
    let startLeft = 0;
    let startTop = 0;

    const savePosition = () => {
      try {
        const left = parseFloat(tableActionsToggle.style.left) || 0;
        const top = parseFloat(tableActionsToggle.style.top) || 0;
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ left, top }));
      } catch (e) { /* ignore */ }
    };

    const onPointerDown = (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      isDragging = true;
      hasDragged = false;
      const rect = tableActionsToggle.getBoundingClientRect();
      offsetX = e.clientX - rect.left;
      offsetY = e.clientY - rect.top;
      startLeft = rect.left;
      startTop = rect.top;
      tableActionsToggle.setPointerCapture(e.pointerId);
      e.preventDefault();
    };

    const onPointerMove = (e) => {
      if (!isDragging) return;
      const newX = e.clientX - offsetX;
      const newY = e.clientY - offsetY;
      if (Math.abs(newX - startLeft) > 4 || Math.abs(newY - startTop) > 4) hasDragged = true;
      tableActionsToggle.style.left = newX + 'px';
      tableActionsToggle.style.top = newY + 'px';
      // Position stack below toggle
      tableActionsStack.style.left = newX + 'px';
      tableActionsStack.style.top = (newY + 52) + 'px';
    };

    const onPointerUp = (e) => {
      if (!isDragging) return;
      isDragging = false;
      tableActionsToggle.releasePointerCapture(e.pointerId);
      savePosition();
    };

    tableActionsToggle.addEventListener('pointerdown', onPointerDown);
    tableActionsToggle.addEventListener('pointermove', onPointerMove);
    tableActionsToggle.addEventListener('pointerup', onPointerUp);
    tableActionsToggle.addEventListener('pointercancel', onPointerUp);

    const closeTableActions = () => {
      tableActionsStack.classList.remove('is-open');
      tableActionsToggle.setAttribute('aria-expanded', 'false');
      if (timers.actionsAutoClose) {
        clearTimeout(timers.actionsAutoClose);
        timers.actionsAutoClose = null;
      }
    };
    const openTableActions = () => {
      tableActionsStack.classList.add('is-open');
      tableActionsToggle.setAttribute('aria-expanded', 'true');
      if (timers.actionsAutoClose) clearTimeout(timers.actionsAutoClose);
      timers.actionsAutoClose = setTimeout(closeTableActions, 60000);
    };
    tableActionsToggle.addEventListener('click', (e) => {
      if (hasDragged) { hasDragged = false; return; }
      if (tableActionsStack.classList.contains('is-open')) closeTableActions();
      else openTableActions();
    });
  }

  // Manual Cash Drawer button functionality.
  if (refs.btnManualDrawer) {
    refs.btnManualDrawer.addEventListener('click', function () {
      api.post('/open_cash_drawer')
        .then((data) => {
          if (data.success) {
            // Show brief success feedback
            this.style.background = '#28a745';
            setTimeout(() => { this.style.background = ''; }, 500);
          } else {
            modal.alert('Cash Drawer Error', data.message || 'Failed to open cash drawer');
          }
        })
        .catch((error) => {
          console.error('Error opening cash drawer:', error);
          modal.alert('Error', error.message || 'Failed to open cash drawer. Please check printer connection.');
        });
    });
  }

  // Transfer button functionality
  if (refs.btnTransferTables) {
    refs.btnTransferTables.addEventListener('click', function () {
      if (!transferMode) clearReserveMode();
      if (!transferMode) clearJoinMode();
      transferMode = !transferMode;
      selectedSourceTable = null;
      selectedTransferSource = null;

      if (transferMode) {
        refs.btnTransferTables.classList.add('active');
        // Add transfer mode class to container for CSS touch prevention
        if (refs.dineInTableBoard) refs.dineInTableBoard.classList.add('transfer-mode-active');
        // Add transfer mode styling to tables
        document.querySelectorAll('.dinein-pos-table.is-occupied').forEach((table) => {
          table.style.cursor = 'pointer';
          table.classList.add('transfer-source');
        });
        // Show visual hint - briefly highlight available tables
        document.querySelectorAll('.dinein-pos-table:not(.is-occupied):not(.is-reserved)').forEach((table) => {
          table.classList.add('transfer-target');
          setTimeout(() => {
            if (!transferMode) return;
            table.classList.remove('transfer-target');
          }, 1500);
        });
        // Re-enable drag on person icons
        enableTransferDrag();
      } else {
        clearTransferMode();
      }
    });
  }

  if (refs.btnReserveTables) {
    refs.btnReserveTables.addEventListener('click', function () {
      if (!reserveMode) clearTransferMode();
      if (!reserveMode) clearJoinMode();
      reserveMode = !reserveMode;

      if (reserveMode) {
        refs.btnReserveTables.classList.add('active');
        if (refs.dineInTableBoard) refs.dineInTableBoard.classList.add('reserve-mode-active');
        document.querySelectorAll('.dinein-pos-table:not(.is-occupied)').forEach((table) => {
          table.classList.add('reserve-target');
        });
      } else {
        clearReserveMode();
      }
    });
  }

  if (refs.btnJoinTables) {
    refs.btnJoinTables.addEventListener('click', async function () {
      if (!joinMode) {
        clearTransferMode();
        clearReserveMode();
        joinMode = true;
        selectedJoinOrders = [];
        refs.btnJoinTables.classList.add('active');
        if (refs.dineInTableBoard) refs.dineInTableBoard.classList.add('join-mode-active');
        notify.info('Join Tables: Click every occupied table to join, then press Join again.', 1700);
      } else {
        await confirmSelectedJoinTables();
      }
    });
  }

  // Press Escape to cancel transfer mode
  const onKeydown = (e) => {
    if (e.key === 'Escape' && (transferMode || reserveMode || joinMode)) {
      clearTransferMode();
      clearReserveMode();
      clearJoinMode();
    }
  };
  document.addEventListener('keydown', onKeydown);

  // LAN/browser EventSource can be interrupted by sleep or WiFi hiccups.
  // Keep a light fallback poll so table occupancy still updates without navigation.
  const pollTimer = setInterval(() => scheduleRealtimeOrderStateRefresh(250), 15000);
  const onVisibilityChange = () => {
    if (!document.hidden) scheduleRealtimeOrderStateRefresh(100);
  };
  document.addEventListener('visibilitychange', onVisibilityChange);

  // Refit floor plan on tablet resize/orientation changes.
  const onResize = () => {
    clearTimeout(timers.fitResize);
    timers.fitResize = setTimeout(() => {
      if (!refs.dineInTableBoard || refs.dineInTableBoard.style.display === 'none') return;
      if (!window.matchMedia('(min-width: 600px) and (max-width: 1199.98px)').matches) return;
      loadDineInTables();
    }, 160);
  };
  window.addEventListener('resize', onResize);

  // Success modal: show when redirected from order creation (?order_success=true)
  const successParams = new URLSearchParams(window.location.search);
  if (successParams.get('order_success') === 'true') {
    const orderNo = successParams.get('order_no') || '';
    const orderType = successParams.get('order_type') || '';
    const printed = successParams.get('printed') === 'true';
    const tablesStr = successParams.get('tables') || '';

    // Scrub URL params right away so a refresh doesn't re-show the modal
    const cleanUrl = window.location.pathname;
    window.history.replaceState({}, '', cleanUrl);

    // Save order_no into shownOrders so SSE real-time refresh won't double-trigger
    const shownOrdersKey = 'shownOrders_' + (orderType || 'all');
    let shownOrders = [];
    try {
      shownOrders = JSON.parse(localStorage.getItem(shownOrdersKey) || '[]');
    } catch (_) {}
    if (orderNo && !shownOrders.includes(orderNo)) {
      shownOrders.push(orderNo);
      localStorage.setItem(shownOrdersKey, JSON.stringify(shownOrders));
    }

    const orderObj = {
      order_no: orderNo,
      order_type: orderType,
      tables: tablesStr ? tablesStr.split(',').map((s) => s.trim()).filter(Boolean) : [],
      printed: printed,
      customer_name: 'Walk-in Customer',
    };

    // Show after a tiny delay so the page renders first
    setTimeout(() => showOrderPlacedModal(orderObj), 200);
  }

  // Initial load: honor the order_type URL param, then scrub the URL (V1 parity).
  const initialOrderType = new URLSearchParams(window.location.search).get('order_type');
  const hasTypeBox = Array.from(refs.orderTypeBoxes).some((box) => box.dataset.type === initialOrderType);
  if (hasTypeBox) {
    setOrderTypeSelection(initialOrderType);
  } else {
    if (refs.dineInTableBoard) {
      loadDineInTables();
    }
    refreshOrders();
  }
  updateOrderTypeCounts();
  pageLoaded = true;

  return function destroy() {
    unsubscribeSse();
    unsubscribeStore();
    clearInterval(pollTimer);
    document.removeEventListener('keydown', onKeydown);
    document.removeEventListener('visibilitychange', onVisibilityChange);
    window.removeEventListener('resize', onResize);
    if (realtimeRefreshTimer) clearTimeout(realtimeRefreshTimer);
    if (timers.fitResize) clearTimeout(timers.fitResize);
    if (timers.actionsAutoClose) clearTimeout(timers.actionsAutoClose);
    if (timers.banner) clearTimeout(timers.banner);
    if (timers.modalChain) clearTimeout(timers.modalChain);
    clearTransferMode();
    closeOpenOverlays();
    // Cancel any in-flight drag ghost listeners by detaching the ghost.
    document.querySelectorAll('.transfer-drag-ghost').forEach((g) => g.remove());
    delete document.documentElement.dataset.nextHref;
  };
}

export default { mount };
