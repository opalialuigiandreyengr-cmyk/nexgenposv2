/**
 * pages/table_management.js — V2 admin table/floor-plan CRUD module.
 * mount() / destroy() SPA contract (core/spa.js).
 *
 * Migrated from V1 inline <script> in table_management.html.
 * All V1 functions preserved for 100% parity. Changes:
 *   - Bootstrap modals → inline overlays (.tm-modal-overlay)
 *   - SweetAlert → core/modal.js confirm/alert
 *   - showToastNotification → core/toast.js notify
 *   - bare fetch → core/api.js api.get/post/del
 *   - CSS classes .tg-* → .tm-*
 *   - Module-scoped state, cleaned up on destroy()
 */

import api from '../core/api.js';
import { notify } from '../core/toast.js';
import * as modal from '../core/modal.js';
import {
  POS_TABLE_TEMPLATES,
  TABLE_SIZE_BY_TYPE,
  TABLE_COLOR_BY_SIZE,
  getTableColor,
  getTableSvg,
} from '../core/tables.js';

/* ─── Module state ─────────────────────────────────────────────────── */
let tables = [];
let zones = [];
let boxes = [];
let selectedTableIds = new Set();
let copiedTables = [];
let multiSelectMode = false;
let activeSectionFilter = 'Main';
let _keydownHandler = null;

/* ─── Helpers ──────────────────────────────────────────────────────── */
function escapeHtml(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  );
}

function normalizeSectionName(section) {
  return (section || '').trim() || 'Main';
}

function zoneIcon(zone) {
  const name = (zone.name || '').toLowerCase();
  const kind = zone.kind || '';
  if (kind === 'service') {
    if (name.includes('delivery')) return `<svg class="icon icon-truck" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="3" width="15" height="13"></rect><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"></polygon><circle cx="5.5" cy="18.5" r="2.5"></circle><circle cx="18.5" cy="18.5" r="2.5"></circle></svg>`;
    if (name.includes('pickup')) return `<svg class="icon icon-package" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="16.5" y1="9.4" x2="7.5" y2="4.21"></line><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>`;
    return `<svg class="icon icon-shopping-bag" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"></path><line x1="3" y1="6" x2="21" y2="6"></line><path d="M16 10a4 4 0 0 1-8 0"></path></svg>`;
  }
  if (kind === 'orphan' || name.includes('vip') || name.includes('tag')) {
    return `<svg class="icon icon-tag" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"></path><line x1="7" y1="7" x2="7.01" y2="7"></line></svg>`;
  }
  return `<svg class="icon icon-table" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="9"></line></svg>`;
}

function updateHeaderCount() {
  const el = document.getElementById('tmTableCount');
  if (el) el.textContent = `${tables.length} TABLE${tables.length !== 1 ? 'S' : ''} · ${zones.length} ZONE${zones.length !== 1 ? 'S' : ''}`;
}

/* ─── Inline modal helpers ────────────────────────────────────────── */
function openOverlay(id) {
  const overlay = document.getElementById(id);
  if (overlay) {
    overlay.classList.add('is-open');
    // Focus first input
    setTimeout(() => {
      const input = overlay.querySelector('input:not([type=hidden])');
      if (input) input.focus();
    }, 100);
  }
}
function closeOverlay(id) {
  const overlay = document.getElementById(id);
  if (overlay) overlay.classList.remove('is-open');
}

/* ─── Zone CRUD ───────────────────────────────────────────────────── */
async function loadZones() {
  try {
    const data = await api.get('/get_table_zones');
    if (!data.success) { notify.error('Error loading zones: ' + data.message); return; }
    zones = data.zones || [];
    if (!zones.some(z => z.name === activeSectionFilter)) {
      const mainZone = zones.find(z => z.name === 'Main') || zones[0];
      activeSectionFilter = mainZone ? mainZone.name : 'Main';
    }
    renderSectionTabs();
    renderZoneList();
    renderSectionSelect();
    renderFloorPlan();
    updateHeaderCount();
  } catch (err) {
    notify.error('Network error while loading zones');
  }
}

function switchSection(name) {
  activeSectionFilter = name;
  selectedTableIds.clear();
  renderSectionTabs();
  renderZoneList();
  renderFloorPlan();
}

function renderSectionTabs() {
  const tabs = document.getElementById('sectionTabs');
  if (!tabs) return;
  tabs.innerHTML = '';
  zones.forEach(zone => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tm-tab' + (zone.name === activeSectionFilter ? ' active' : '');
    btn.dataset.section = zone.name;
    btn.innerHTML = `${zoneIcon(zone)} <span>${escapeHtml(zone.name)}</span>`;
    btn.addEventListener('click', () => switchSection(zone.name));
    tabs.appendChild(btn);
  });
  if (!zones.length) {
    tabs.innerHTML = '<span class="tm-tabs-empty">No zones yet — add one in the sidebar.</span>';
  }
}

function renderZoneList() {
  const list = document.getElementById('zoneList');
  if (!list) return;
  list.innerHTML = '';

  const editSvg = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>`;
  const trashSvg = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>`;

  zones.forEach(zone => {
    const count = tables.filter(t => normalizeSectionName(t.room_section) === zone.name).length;
    const row = document.createElement('div');
    row.className = 'tm-zone-item' + (zone.name === activeSectionFilter ? ' is-active' : '');
    const isManaged = zone.kind !== 'service';
    row.innerHTML = `
      <button type="button" class="tm-zone-main" title="Show ${escapeHtml(zone.name)}">
        <span>${zoneIcon(zone)}</span>
        <span class="tm-zone-name">${escapeHtml(zone.name)}</span>
        <span class="tm-zone-count">${count}</span>
      </button>
      <div class="tm-zone-actions">
        ${isManaged ? `
          <button type="button" class="tm-zone-action" title="Rename zone">${editSvg}</button>
          <button type="button" class="tm-zone-action tm-zone-action-del" title="Delete zone">${trashSvg}</button>
        ` : ''}
      </div>`;
    row.querySelector('.tm-zone-main').addEventListener('click', () => switchSection(zone.name));
    if (isManaged) {
      row.querySelector('.tm-zone-action')?.addEventListener('click', (e) => { e.stopPropagation(); renameZone(zone); });
      row.querySelector('.tm-zone-action-del')?.addEventListener('click', (e) => { e.stopPropagation(); deleteZone(zone); });
    }
    list.appendChild(row);
  });
}

function renderSectionSelect() {
  const select = document.getElementById('newTableSection');
  if (!select) return;
  select.innerHTML = '';
  zones.forEach(zone => {
    const opt = document.createElement('option');
    opt.value = zone.name;
    opt.textContent = zone.name + (zone.kind === 'service' ? ' (Service)' : '');
    select.appendChild(opt);
  });
  select.value = activeSectionFilter;
}

async function addZone() {
  const input = document.getElementById('newZoneName');
  const name = (input ? input.value.trim() : '');
  if (!name) { notify.error('Enter a zone name first'); return; }
  try {
    const data = await api.post('/add_table_zone', { name });
    if (data.success) {
      if (input) input.value = '';
      loadZones();
      notify.success(`Zone '${data.zone.name}' added`);
    } else {
      notify.error(data.message);
    }
  } catch {
    notify.error('Network error while adding zone');
  }
}

async function renameZone(zone) {
  const newName = await modal.prompt('Rename Floor Zone', '', {
    defaultValue: zone.name,
    confirmLabel: 'Save Name',
    cancelLabel: 'Cancel',
    maxLength: 60,
  });
  if (!newName || newName === zone.name) return;
  try {
    const data = await api.post('/update_table_zone', { id: zone.id, name: newName });
    if (data.success) {
      if (activeSectionFilter === zone.name) activeSectionFilter = newName;
      loadTables();
      loadZones();
      notify.success(`Zone renamed to '${newName}'`);
    } else {
      notify.error(data.message);
    }
  } catch {
    notify.error('Network error while renaming zone');
  }
}

async function deleteZone(zone) {
  const confirmed = await modal.confirm(`Delete zone "${zone.name}"?`, 'Only empty zones can be deleted.', {
    confirmLabel: 'Delete',
    cancelLabel: 'Cancel',
    danger: true,
  });
  if (!confirmed) return;
  try {
    const data = await api.post('/delete_table_zone', { id: zone.id });
    if (data.success) {
      if (activeSectionFilter === zone.name) activeSectionFilter = 'Main';
      loadZones();
      notify.success(data.message);
    } else {
      notify.error(data.message);
    }
  } catch {
    notify.error('Network error while deleting zone');
  }
}

/* ─── Table CRUD ──────────────────────────────────────────────────── */
async function loadTables() {
  try {
    const data = await api.get('/get_restaurant_tables');
    if (data.success) {
      tables = data.tables;
      renderFloorPlan();
      updateHeaderCount();
    } else {
      notify.error('Error loading tables: ' + data.message);
    }
  } catch {
    notify.error('Network error while loading tables');
  }
}

async function openAddTableModal() {
  const modalFn = window.modal?.addTableModal || modal.addTableModal;
  const activeSec = activeSectionFilter || 'Main';
  const sectionsList = zones.length ? zones.map(z => z.name) : ['Main'];

  const result = await modalFn(sectionsList, activeSec);
  if (!result) return;

  const tableNumber = result.table_number;
  const section = result.room_section;
  const tableType = result.table_type;

  const secTables = tables.filter(t => normalizeSectionName(t.room_section) === section);
  const targetX = (secTables.length % 5) * 110 + 40;
  const targetY = Math.floor(secTables.length / 5) * 110 + 40;

  try {
    const data = await api.post('/add_restaurant_table', {
      table_number: tableNumber,
      table_type: tableType,
      room_section: section,
      x_pos: targetX,
      y_pos: targetY,
    });
    if (data.success) {
      activeSectionFilter = section;
      await loadTables();
      await loadZones();

      const created = tables.find(t => normalizeSectionName(t.room_section) === section && String(t.table_number) === String(tableNumber));
      if (created) {
        created.x_pos = targetX;
        created.y_pos = targetY;
        updatePosition(created.id, targetX, targetY);
        selectedTableIds.clear();
        selectedTableIds.add(created.id);
        renderFloorPlan();
      }
      notify.success(`Table #${tableNumber} added successfully`);
    } else {
      notify.error(data.message);
    }
  } catch {
    notify.error('Network error while adding table');
  }
}

function updatePosition(id, x, y) {
  api.post('/update_table_position', { id, x_pos: x, y_pos: y }).catch(() => {});
}

function updateRotation(id, rotation) {
  api.post('/update_table_rotation', { id, rotation }).catch(() => {});
}

function rotateTable(table) {
  const newRotation = ((table.rotation || 0) + 90) % 360;
  table.rotation = newRotation;
  const tableItem = document.querySelector(`.tm-table-item[data-id="${table.id}"]`);
  if (tableItem) tableItem.style.transform = `rotate(${newRotation}deg)`;
  updateRotation(table.id, newRotation);
}

async function resetLayout() {
  const confirmed = await modal.confirm('Reset Layout?', 'All table positions will be arranged into a grid layout.', {
    confirmLabel: 'Yes, reset',
    cancelLabel: 'Cancel',
  });
  if (!confirmed) return;

  const floorPlan = document.getElementById('floorPlan');
  const floorWidth = floorPlan?.clientWidth || 900;
  const box = 86;
  const gap = 14;
  const offset = 24;
  const usableWidth = Math.max(1, floorWidth - offset * 2);
  const cols = Math.max(1, Math.floor((usableWidth + gap) / (box + gap)));

  tables.forEach((table, index) => {
    const col = index % cols;
    const row = Math.floor(index / cols);
    table.x_pos = offset + col * (box + gap);
    table.y_pos = offset + row * (box + gap);
    table.rotation = 0;
    updatePosition(table.id, table.x_pos, table.y_pos);
    updateRotation(table.id, 0);
  });
  renderFloorPlan();
  notify.success('Layout reset successfully');
}

async function showTableDetails(table) {
  let orderData = null;
  try {
    const data = await api.get(`/get_occupied_tables?tables=${encodeURIComponent(table.table_number)}`);
    if (data.success && data.orders && data.orders.length) {
      orderData = data.orders[0];
    }
  } catch {}

  const modalFn = window.modal?.tableDetailsModal || modal.tableDetailsModal;
  const action = await modalFn(table, orderData);

  if (action === 'view' && orderData?.id) {
    window.location.href = `/order_details/${orderData.id}`;
  } else if (action === 'release') {
    releaseTable(table);
  } else if (action === 'rotate') {
    rotateTable(table);
  } else if (action === 'delete') {
    const confirmed = await modal.confirm(`Delete Table ${table.table_number}?`, 'This action cannot be undone.', {
      confirmLabel: 'Delete',
      cancelLabel: 'Cancel',
      danger: true,
    });
    if (!confirmed) return;
    try {
      const res = await api.remove(`/delete_table/${table.id}`);
      if (res.success) {
        selectedTableIds.delete(table.id);
        loadTables();
        loadZones();
        notify.info(`Table ${table.table_number} deleted`);
      } else {
        notify.error(res.message);
      }
    } catch {
      notify.error('Error deleting table');
    }
  }
}

async function releaseTable(table) {
  try {
    const data = await api.post('/set_table_reservation', { table_id: table.id, status: 'available' });
    if (data.success) {
      loadTables();
      notify.success(`Table ${table.table_number} released`);
    } else {
      notify.error(data.message);
    }
  } catch {
    notify.error('Error releasing table');
  }
}

/* ─── Selection + keyboard shortcuts ─────────────────────────────── */
function selectedTablesArr() { return tables.filter(t => selectedTableIds.has(t.id)); }

function selectTable(table, item, additive, toggle) {
  if (additive) {
    if (toggle) {
      if (selectedTableIds.has(table.id)) {
        selectedTableIds.delete(table.id);
        if (item) item.classList.remove('tm-selected');
      } else {
        selectedTableIds.add(table.id);
        if (item) item.classList.add('tm-selected');
      }
    } else if (!selectedTableIds.has(table.id)) {
      selectedTableIds.add(table.id);
      if (item) item.classList.add('tm-selected');
    }
    return;
  }
  if (selectedTableIds.has(table.id)) return;
  selectedTableIds.clear();
  selectedTableIds.add(table.id);
  document.querySelectorAll('.tm-table-item.tm-selected').forEach(el => el.classList.remove('tm-selected'));
  if (item) item.classList.add('tm-selected');
}

function copySelectedTable() {
  const sel = selectedTablesArr();
  if (!sel.length) { notify.info('Click a table first, then press Ctrl+C to copy it'); return; }
  copiedTables = sel.map(t => ({
    table_number: t.table_number,
    table_type: t.table_type,
    x_pos: t.x_pos,
    y_pos: t.y_pos
  }));
  notify.success(`${sel.length} table${sel.length > 1 ? 's' : ''} copied — press Ctrl+V to paste`);
}

function nextAvailableTableName(copiedName, used) {
  const match = String(copiedName || '').match(/^(.*?)(\d+)$/);
  const base = match ? match[1].trim() : String(copiedName || '').trim();
  let num = match ? (parseInt(match[2], 10) + 1) : 2;
  let candidate;
  do {
    candidate = base ? (base + ' ' + num) : String(num);
    num++;
  } while (used.has(candidate) && num < 10000);
  return candidate;
}

async function pasteTable() {
  if (!copiedTables || !copiedTables.length) {
    notify.info('Nothing copied yet — press Ctrl+C on a table first');
    return;
  }
  const used = new Set(
    tables.filter(t => normalizeSectionName(t.room_section) === activeSectionFilter)
      .map(t => String(t.table_number))
  );
  const planned = copiedTables.map((c, idx) => {
    const name = nextAvailableTableName(c.table_number, used);
    used.add(name);
    const targetX = Math.max(30, Math.min((c.x_pos != null ? c.x_pos : 50) + 35 + (idx * 15), 750));
    const targetY = Math.max(30, Math.min((c.y_pos != null ? c.y_pos : 50) + 35 + (idx * 15), 450));
    return {
      table_number: name,
      table_type: c.table_type,
      x_pos: targetX,
      y_pos: targetY
    };
  });

  const createdPacks = [];
  for (const p of planned) {
    try {
      const data = await api.post('/add_restaurant_table', {
        table_number: p.table_number,
        table_type: p.table_type,
        room_section: activeSectionFilter,
      });
      if (data.success) {
        createdPacks.push(p);
      } else notify.error(data.message);
    } catch { /* skip */ }
  }

  await loadTables();
  await loadZones();

  // Position newly created tables visibly and auto-select them
  const newIds = [];
  for (const p of createdPacks) {
    const found = tables.find(t =>
      normalizeSectionName(t.room_section) === activeSectionFilter &&
      String(t.table_number) === String(p.table_number)
    );
    if (found) {
      found.x_pos = p.x_pos;
      found.y_pos = p.y_pos;
      updatePosition(found.id, p.x_pos, p.y_pos);
      newIds.push(found.id);
    }
  }

  if (newIds.length) {
    selectedTableIds.clear();
    newIds.forEach(id => selectedTableIds.add(id));
    renderFloorPlan();
    notify.success(`Pasted ${newIds.length} table${newIds.length > 1 ? 's' : ''}: ${createdPacks.map(c => c.table_number).join(', ')}`);
  }
}

async function deleteSelectedTable() {
  const sel = selectedTablesArr();
  if (!sel.length) return;
  let deleted = 0;
  for (const t of sel) {
    try {
      const data = await api.remove(`/delete_table/${t.id}`);
      if (data && data.success) deleted++;
      else if (data && data.message) notify.error(data.message);
    } catch (err) {
      console.error('Error deleting table:', err);
    }
  }
  selectedTableIds.clear();
  await loadTables();
  await loadZones();
  if (deleted > 0) {
    notify.info(`${deleted} table${deleted !== 1 ? 's' : ''} deleted`);
  }
}

/* ─── Floor boxes ────────────────────────────────────────────────── */
async function loadBoxes() {
  try {
    const data = await api.get('/get_floor_boxes');
    if (data.success) {
      boxes = data.boxes || [];
      renderFloorPlan();
    }
  } catch { /* silent */ }
}

function renderZoneBoxes(floorPlan, maxX, maxY) {
  boxes.filter(b => normalizeSectionName(b.zone_name) === activeSectionFilter).forEach(box => {
    const el = document.createElement('div');
    el.className = 'tm-zone-box';
    el.style.left = Math.max(0, Math.min(box.x_pos, maxX)) + 'px';
    el.style.top = Math.max(0, Math.min(box.y_pos, maxY)) + 'px';
    el.style.width = Math.max(40, Math.min(box.width, floorPlan.clientWidth)) + 'px';
    el.style.height = Math.max(40, Math.min(box.height, floorPlan.clientHeight)) + 'px';
    el.dataset.id = box.id;
    el.innerHTML = `
      <span class="tm-zone-box-label">${escapeHtml(box.zone_name || '')}</span>
      <div class="tm-zone-box-corner c-nw" title="Resize"></div>
      <div class="tm-zone-box-corner c-ne" title="Resize"></div>
      <div class="tm-zone-box-corner c-sw" title="Resize"></div>
      <div class="tm-zone-box-corner c-se" title="Resize"></div>
      <button type="button" class="tm-zone-box-del" title="Delete box">✕</button>`;

    const delBtn = el.querySelector('.tm-zone-box-del');
    delBtn.onpointerdown = e => e.stopPropagation();
    delBtn.onclick = e => { e.stopPropagation(); deleteZoneBox(box); };

    // Drag to move box
    el.onpointerdown = function(e) {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      if (e.target.closest('.tm-zone-box-del') || e.target.closest('.tm-zone-box-corner')) return;
      e.preventDefault();
      const startX = e.clientX, startY = e.clientY;
      const origLeft = parseInt(el.style.left, 10), origTop = parseInt(el.style.top, 10);
      let moved = false;
      try { el.setPointerCapture(e.pointerId); } catch {}
      const onMove = ev => {
        const dx = ev.clientX - startX, dy = ev.clientY - startY;
        if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved = true;
        el.style.left = Math.max(0, Math.min(origLeft + dx, maxX)) + 'px';
        el.style.top = Math.max(0, Math.min(origTop + dy, maxY)) + 'px';
      };
      const onUp = ev => {
        el.removeEventListener('pointermove', onMove);
        el.removeEventListener('pointerup', onUp);
        el.removeEventListener('pointercancel', onUp);
        try { el.releasePointerCapture(ev.pointerId); } catch {}
        if (moved) {
          box.x_pos = parseInt(el.style.left, 10);
          box.y_pos = parseInt(el.style.top, 10);
          updateZoneBox(box);
        }
      };
      el.addEventListener('pointermove', onMove);
      el.addEventListener('pointerup', onUp);
      el.addEventListener('pointercancel', onUp);
    };

    // Corner resize handles
    const corners = { 'c-nw': { hx: -1, hy: -1 }, 'c-ne': { hx: 1, hy: -1 }, 'c-sw': { hx: -1, hy: 1 }, 'c-se': { hx: 1, hy: 1 } };
    Object.entries(corners).forEach(([cls, c]) => {
      const handle = el.querySelector('.' + cls);
      handle.onpointerdown = function(e) {
        if (e.pointerType === 'mouse' && e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        const startX = e.clientX, startY = e.clientY;
        const origL = parseInt(el.style.left, 10), origT = parseInt(el.style.top, 10);
        const origW = box.width, origH = box.height;
        let moved = false;
        try { handle.setPointerCapture(e.pointerId); } catch {}
        const onMove = ev => {
          const dx = ev.clientX - startX, dy = ev.clientY - startY;
          if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved = true;
          let nl = origL, nt = origT, nw = origW, nh = origH;
          if (c.hx === 1) nw = Math.max(40, Math.min(origW + dx, floorPlan.clientWidth - nl));
          if (c.hx === -1) { nw = Math.max(40, Math.min(origW - dx, nl + origW)); nl = nl + (origW - nw); }
          if (c.hy === 1) nh = Math.max(40, Math.min(origH + dy, floorPlan.clientHeight - nt));
          if (c.hy === -1) { nh = Math.max(40, Math.min(origH - dy, nt + origH)); nt = nt + (origH - nh); }
          el.style.left = nl + 'px'; el.style.top = nt + 'px';
          el.style.width = nw + 'px'; el.style.height = nh + 'px';
        };
        const onUp = ev => {
          handle.removeEventListener('pointermove', onMove);
          handle.removeEventListener('pointerup', onUp);
          handle.removeEventListener('pointercancel', onUp);
          try { handle.releasePointerCapture(ev.pointerId); } catch {}
          if (moved) {
            box.x_pos = parseInt(el.style.left, 10);
            box.y_pos = parseInt(el.style.top, 10);
            box.width = parseInt(el.style.width, 10);
            box.height = parseInt(el.style.height, 10);
            updateZoneBox(box);
          }
        };
        handle.addEventListener('pointermove', onMove);
        handle.addEventListener('pointerup', onUp);
        handle.addEventListener('pointercancel', onUp);
      };
    });

    floorPlan.appendChild(el);
  });
}

function updateZoneBox(box) {
  api.post('/update_floor_box', { id: box.id, x_pos: box.x_pos, y_pos: box.y_pos, width: box.width, height: box.height }).catch(() => {});
}

async function addZoneBox() {
  try {
    const data = await api.post('/add_floor_box', { zone_name: activeSectionFilter });
    if (data.success) {
      loadBoxes();
      notify.success(`Zone box added to '${activeSectionFilter}'`);
    } else {
      notify.error(data.message);
    }
  } catch {
    notify.error('Network error while adding zone box');
  }
}

async function deleteZoneBox(box) {
  const confirmed = await modal.confirm('Delete this zone box?', 'The box outline will be removed from the floor plan.', {
    confirmLabel: 'Delete',
    cancelLabel: 'Cancel',
    danger: true,
  });
  if (!confirmed) return;
  try {
    const data = await api.post('/delete_floor_box', { id: box.id });
    if (data.success) { loadBoxes(); notify.success('Zone box deleted'); }
    else notify.error(data.message);
  } catch {
    notify.error('Network error while deleting zone box');
  }
}

/* ─── Render floor plan ──────────────────────────────────────────── */
function renderFloorPlan() {
  const floorPlan = document.getElementById('floorPlan');
  const emptyState = document.getElementById('emptyState');
  if (!floorPlan) return;

  floorPlan.querySelectorAll('.tm-table-item').forEach(el => el.remove());
  floorPlan.querySelectorAll('.tm-zone-box').forEach(el => el.remove());

  const GRID_SIZE = 10;
  const TABLE_BOX_SIZE = 86;
  const floorWidth = floorPlan.clientWidth || 0;
  const floorHeight = floorPlan.clientHeight || 0;
  const maxX = Math.max(0, floorWidth - TABLE_BOX_SIZE);
  const maxY = Math.max(0, floorHeight - TABLE_BOX_SIZE);

  renderZoneBoxes(floorPlan, maxX, maxY);

  if (!tables || tables.length === 0) {
    if (emptyState) emptyState.classList.add('is-visible');
    return;
  }
  if (emptyState) emptyState.classList.remove('is-visible');

  const visibleTables = tables.filter(table =>
    normalizeSectionName(table.room_section) === activeSectionFilter
  );

  if (visibleTables.length === 0) {
    if (emptyState) {
      const h4 = emptyState.querySelector('h4');
      const p = emptyState.querySelector('p');
      if (h4) h4.textContent = `No Tables in ${escapeHtml(activeSectionFilter)}`;
      if (p) p.innerHTML = `Click <strong>"Add Table"</strong> to place a table in this section.`;
      emptyState.classList.add('is-visible');
    }
    return;
  }

  visibleTables.forEach((table, idx) => {
    const status = table.status || 'available';
    const tableItem = document.createElement('div');
    tableItem.className = `tm-table-item tm-status-${status} tm-placing`;
    setTimeout(() => tableItem.classList.remove('tm-placing'), 350);

    let rawX = table.x_pos != null ? table.x_pos : ((idx % 6) * 110 + 40);
    let rawY = table.y_pos != null ? table.y_pos : (Math.floor(idx / 6) * 110 + 40);

    if (maxY > 100 && rawY > maxY) {
      rawY = (idx % 4) * 100 + 40;
      table.y_pos = rawY;
      updatePosition(table.id, rawX, rawY);
    }
    if (maxX > 100 && rawX > maxX) {
      rawX = (idx % 6) * 100 + 40;
      table.x_pos = rawX;
      updatePosition(table.id, rawX, rawY);
    }

    let left = Math.max(0, Math.round(rawX / GRID_SIZE) * GRID_SIZE);
    let top = Math.max(0, Math.round(rawY / GRID_SIZE) * GRID_SIZE);

    tableItem.style.left = `${left}px`;
    tableItem.style.top = `${top}px`;
    tableItem.style.transform = `rotate(${table.rotation || 0}deg)`;
    tableItem.dataset.id = table.id;
    tableItem.dataset.type = table.table_type;
    if (selectedTableIds.has(table.id)) tableItem.classList.add('tm-selected');

    const tableSvg = getTableSvg(table.table_type);

    tableItem.innerHTML = `
      <div class="tm-table-glow"></div>
      <div class="tm-table-svg-wrap">
        ${tableSvg}
        <span class="dinein-pos-table-label">${escapeHtml(table.table_number)}</span>
        <div class="tm-rotate-handle" title="Rotate 90°">↻</div>
      </div>`;

    const rotateBtn = tableItem.querySelector('.tm-rotate-handle');
    rotateBtn.onpointerdown = e => e.stopPropagation();
    rotateBtn.onclick = e => { e.stopPropagation(); rotateTable(table); };

    // Pointer drag
    tableItem.onpointerdown = function(e) {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      if (e.target.closest('.tm-rotate-handle')) return;
      e.preventDefault();
      const toggle = e.shiftKey || e.ctrlKey || e.metaKey;
      const additive = multiSelectMode || toggle;
      selectTable(table, this, additive, toggle);

      const group = selectedTablesArr().map(t => {
        const el = document.querySelector(`.tm-table-item[data-id="${t.id}"]`);
        return el ? { t, el } : null;
      }).filter(Boolean);
      if (!group.some(g => g.t.id === table.id)) group.push({ t: table, el: tableItem });

      let startX = e.clientX, startY = e.clientY;
      let moved = false;
      this.classList.add('tm-dragging');
      tableItem.dataset.dragged = '0';
      try { this.setPointerCapture(e.pointerId); } catch {}

      const onPointerMove = ev => {
        const dx = ev.clientX - startX, dy = ev.clientY - startY;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
          moved = true;
          tableItem.dataset.dragged = '1';
          const curWidth = floorPlan.clientWidth || 0;
          const curHeight = floorPlan.clientHeight || 0;
          const curMaxX = Math.max(0, curWidth - TABLE_BOX_SIZE);
          const curMaxY = Math.max(0, curHeight - TABLE_BOX_SIZE);

          group.forEach(({ t, el }) => {
            let newX = Math.round((t.x_pos + dx) / GRID_SIZE) * GRID_SIZE;
            let newY = Math.round((t.y_pos + dy) / GRID_SIZE) * GRID_SIZE;
            // Strict canvas boundaries:
            // Left: 0 (screen left edge)
            // Top: 0 (directly under tm-board-head)
            // Right: curMaxX (ends right at tm-sidebar)
            // Bottom: curMaxY (ends at screen bottom)
            newX = Math.max(0, Math.min(newX, curMaxX));
            newY = Math.max(0, Math.min(newY, curMaxY));
            el.style.left = newX + 'px';
            el.style.top = newY + 'px';
          });
        }
      };
      const onPointerUp = ev => {
        tableItem.removeEventListener('pointermove', onPointerMove);
        tableItem.removeEventListener('pointerup', onPointerUp);
        tableItem.removeEventListener('pointercancel', onPointerUp);
        try { tableItem.releasePointerCapture(ev.pointerId); } catch {}
        tableItem.classList.remove('tm-dragging');
        if (moved) {
          group.forEach(({ t, el }) => {
            const finalX = parseInt(el.style.left, 10);
            const finalY = parseInt(el.style.top, 10);
            if (finalX !== t.x_pos || finalY !== t.y_pos) {
              t.x_pos = finalX;
              t.y_pos = finalY;
              updatePosition(t.id, finalX, finalY);
            }
          });
        }
      };
      tableItem.addEventListener('pointermove', onPointerMove);
      tableItem.addEventListener('pointerup', onPointerUp);
      tableItem.addEventListener('pointercancel', onPointerUp);
    };

    // Double-click opens details
    tableItem.addEventListener('dblclick', function(e) {
      if (tableItem.dataset.dragged === '1') return;
      e.preventDefault();
      showTableDetails(table);
    });

    // Click empty canvas clears selection
    floorPlan.onpointerdown = function(e) {
      if (e.target.closest('.tm-table-item') || e.target.closest('.tm-zone-box')) return;
      if (selectedTableIds.size) {
        selectedTableIds.clear();
        document.querySelectorAll('.tm-table-item.tm-selected').forEach(el => el.classList.remove('tm-selected'));
      }
    };

    floorPlan.appendChild(tableItem);
  });
}

/* ─── Type selector ──────────────────────────────────────────────── */
function setupTypeSelector() {
  const cards = document.querySelectorAll('.tm-type-card');
  cards.forEach(card => {
    card.addEventListener('click', function() {
      cards.forEach(c => c.classList.remove('selected'));
      this.classList.add('selected');
      const typeSelect = document.getElementById('newTableType');
      if (typeSelect) typeSelect.value = this.dataset.type;
    });
  });
}

/* ─── Mount / Destroy (SPA lifecycle) ────────────────────────────── */
export function mount() {
  document.getElementById('view-root')?.classList.add('tm-view-root');

  // Load data
  loadTables();
  loadZones();
  loadBoxes();

  // Type selector
  setupTypeSelector();

  // Add table modal trigger
  document.getElementById('sidebarAddTableBtn')?.addEventListener('click', openAddTableModal);

  // Add zone
  document.getElementById('addZoneBtn')?.addEventListener('click', addZone);
  document.getElementById('newZoneName')?.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addZone(); } });

  // Sidebar center-arrow toggle dynamically attached directly to .shell-side border line
  const sidebar = document.querySelector('.shell-side') || document.getElementById('side-nav');
  if (sidebar) {
    sidebar.querySelector('.tm-sidebar-toggle-btn')?.remove();

    // Remember collapsed state across page refresh
    if (localStorage.getItem('tm_sidebar_collapsed') === 'true') {
      document.body.classList.add('tm-side-collapsed');
    }

    const toggleBtn = document.createElement('button');
    toggleBtn.type = 'button';
    toggleBtn.className = 'tm-sidebar-toggle-btn';
    toggleBtn.id = 'tmSidebarToggleBtn';
    toggleBtn.title = 'Hide/Show Main Sidebar';
    toggleBtn.setAttribute('aria-label', 'Toggle navigation sidebar');
    toggleBtn.innerHTML = `<svg class="icon icon-chevron-left" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>`;
    toggleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isCollapsed = document.body.classList.toggle('tm-side-collapsed');
      localStorage.setItem('tm_sidebar_collapsed', isCollapsed ? 'true' : 'false');
      setTimeout(renderFloorPlan, 260);
    });
    sidebar.appendChild(toggleBtn);
  }

  // Keyboard shortcuts
  _keydownHandler = function(e) {
    if (e.key === 'Escape') {
      closeOverlay('addTableOverlay');
      closeOverlay('tableDetailsOverlay');
      if (selectedTableIds.size) {
        selectedTableIds.clear();
        document.querySelectorAll('.tm-table-item.tm-selected').forEach(el => el.classList.remove('tm-selected'));
        renderFloorPlan();
      }
      return;
    }

    const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';
    const isEditingInput = activeTag === 'input' || activeTag === 'textarea' || activeTag === 'select' || document.activeElement?.isContentEditable;
    if (isEditingInput) return;

    if (e.key === 'Backspace' || e.key === 'Delete') {
      if (selectedTableIds.size > 0) {
        e.preventDefault();
        deleteSelectedTable();
        return;
      }
    }

    const mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    const key = e.key.toLowerCase();
    if (key === 'c') { e.preventDefault(); copySelectedTable(); }
    else if (key === 'v') { e.preventDefault(); pasteTable(); }
  };
  document.addEventListener('keydown', _keydownHandler);
}

export function destroy() {
  document.getElementById('view-root')?.classList.remove('tm-view-root');
  document.body.classList.remove('tm-side-collapsed');
  const sidebar = document.querySelector('.shell-side') || document.getElementById('side-nav');
  sidebar?.querySelector('.tm-sidebar-toggle-btn')?.remove();
  if (_keydownHandler) {
    document.removeEventListener('keydown', _keydownHandler);
    _keydownHandler = null;
  }
  // Reset state
  tables = [];
  zones = [];
  boxes = [];
  selectedTableIds.clear();
  copiedTables = [];
  multiSelectMode = false;
  activeSectionFilter = 'Main';
}

export default { mount, destroy };

