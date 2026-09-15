/* ============================================================================
   transfer_history.js — RLC SFTP Transfer History Controller (POS_V2 Overhaul)
   ============================================================================ */

import { api } from '../core/api.js';
import { notify } from '../core/toast.js';
import modal from '../core/modal.js';

let pollTimer = null;
let currentPage = 1;
let itemsPerPage = 20;
let currentFilters = {
  status: 'all',
  search: '',
  date: '',
};
let isCompact = localStorage.getItem('rlcTransferHistoryCompact') === 'true';
let minBusinessDate = null;
let validEodDatesSet = new Set();

const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

let dpTrigger = null;
let dpPopover = null;
let dpHidden = null;
let dpLabel = null;
let dpCalTitle = null;
let dpCalDays = null;
let dpPrevBtn = null;
let dpNextBtn = null;
let dpCalYear = new Date().getFullYear();
let dpCalMonth = new Date().getMonth();
let dpOutsideClickListener = null;

export function mount(rootEl) {
  const container = rootEl.querySelector('#rlcHistoryRoot');
  if (!container) return;

  // Default date limits
  const localToday = new Date().toISOString().split('T')[0];
  const dateFilterInput = container.querySelector('#rlcDateFilter');

  if (dateFilterInput) {
    dateFilterInput.setAttribute('max', localToday);
    dateFilterInput.addEventListener('input', () => {
      const currentMin = dateFilterInput.getAttribute('min') || minBusinessDate;
      if (currentMin && dateFilterInput.value < currentMin) {
        dateFilterInput.value = currentMin;
        notify.warning(`Filter date cannot be before initial business date (${currentMin}).`);
      }
    });
  }

  // Setup Custom Date Picker
  setupCustomDatePicker(container);

  // Ensure table is in compact mode
  applyCompactState(container);

  // Initial load
  loadTransferHistory(container);

  // Poll logs every 15 seconds
  pollTimer = setInterval(() => {
    loadTransferHistory(container, true);
  }, 15000);

  // Wire Status Tabs
  const statusTabs = container.querySelectorAll('.rlc-status-tab');
  statusTabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const st = tab.dataset.status || 'all';
      setStatusFilter(container, st);
    });
  });

  // Metric Card clicks for quick filter
  const cardCompleted = container.querySelector('#cardCompleted');
  const cardPending = container.querySelector('#cardPending');
  const cardFailed = container.querySelector('#cardFailed');
  const cardTotal = container.querySelector('#cardTotal');
  if (cardCompleted) cardCompleted.addEventListener('click', () => setStatusFilter(container, 'completed'));
  if (cardPending) cardPending.addEventListener('click', () => setStatusFilter(container, 'pending'));
  if (cardFailed) cardFailed.addEventListener('click', () => setStatusFilter(container, 'failed'));
  if (cardTotal) cardTotal.addEventListener('click', () => setStatusFilter(container, 'all'));

  // Search Filter
  const searchFilter = container.querySelector('#rlcSearchFilter');
  const btnClearSearch = container.querySelector('#btnClearSearch');
  if (searchFilter) {
    searchFilter.addEventListener('input', (e) => {
      currentFilters.search = e.target.value.trim().toLowerCase();
      if (btnClearSearch) btnClearSearch.hidden = !e.target.value;
      updateClearFilterButton(container);
      currentPage = 1;
      loadTransferHistory(container);
    });
  }
  if (btnClearSearch) {
    btnClearSearch.addEventListener('click', () => {
      if (searchFilter) searchFilter.value = '';
      currentFilters.search = '';
      btnClearSearch.hidden = true;
      updateClearFilterButton(container);
      currentPage = 1;
      loadTransferHistory(container);
    });
  }

  // Date Filter
  if (dateFilterInput) {
    dateFilterInput.addEventListener('change', (e) => {
      currentFilters.date = e.target.value;
      updateClearFilterButton(container);
      currentPage = 1;
      loadTransferHistory(container);
    });
  }

  // Reset / Clear Filters button
  const btnReset = container.querySelector('#btnResetFilters');
  if (btnReset) {
    btnReset.addEventListener('click', () => {
      setStatusFilter(container, 'all');
      if (searchFilter) searchFilter.value = '';
      if (btnClearSearch) btnClearSearch.hidden = true;
      if (dateFilterInput) dateFilterInput.value = '';
      currentFilters = { status: 'all', search: '', date: '' };
      updateClearFilterButton(container);
      currentPage = 1;
      loadTransferHistory(container);
    });
  }

  // Generate & Transmit button
  const genBtn = container.querySelector('#btnGenerateSendRlc');
  if (genBtn) {
    genBtn.addEventListener('click', () => handleGenerateRlc(container, genBtn));
  }

  // Pagination buttons
  const prevBtn = container.querySelector('#btnPrevPage');
  const nextBtn = container.querySelector('#btnNextPage');
  if (prevBtn) {
    prevBtn.addEventListener('click', () => {
      if (currentPage > 1) {
        currentPage--;
        loadTransferHistory(container);
      }
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      currentPage++;
      loadTransferHistory(container);
    });
  }
}

export function destroy() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (dpOutsideClickListener) {
    document.removeEventListener('click', dpOutsideClickListener);
    dpOutsideClickListener = null;
  }
}

function setupCustomDatePicker(container) {
  dpTrigger = container.querySelector('#rlcReportDateTrigger');
  dpPopover = container.querySelector('#rlcDatePickerPopover');
  dpHidden = container.querySelector('#rlcReportDate');
  dpLabel = container.querySelector('#rlcReportDateLabel');
  dpCalTitle = container.querySelector('#rlcCalTitle');
  dpCalDays = container.querySelector('#rlcCalDays');
  dpPrevBtn = container.querySelector('#rlcCalPrev');
  dpNextBtn = container.querySelector('#rlcCalNext');

  if (!dpTrigger || !dpPopover) return;

  const initialVal = dpHidden ? dpHidden.value : new Date().toISOString().split('T')[0];
  if (initialVal) {
    const d = new Date(`${initialVal}T00:00:00`);
    if (!isNaN(d.getTime())) {
      dpCalYear = d.getFullYear();
      dpCalMonth = d.getMonth();
    }
  }

  dpTrigger.addEventListener('click', (e) => {
    e.stopPropagation();
    const isHidden = dpPopover.hidden;
    dpPopover.hidden = !isHidden;
    dpTrigger.setAttribute('aria-expanded', String(isHidden));
    if (isHidden) {
      renderCalendar();
    }
  });

  if (dpPrevBtn) {
    dpPrevBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      dpCalMonth--;
      if (dpCalMonth < 0) {
        dpCalMonth = 11;
        dpCalYear--;
      }
      renderCalendar();
    });
  }

  if (dpNextBtn) {
    dpNextBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      dpCalMonth++;
      if (dpCalMonth > 11) {
        dpCalMonth = 0;
        dpCalYear++;
      }
      renderCalendar();
    });
  }

  if (dpCalDays) {
    dpCalDays.addEventListener('click', (e) => {
      const btn = e.target.closest('.rlc-day');
      if (!btn || btn.disabled) return;
      const iso = btn.dataset.iso;
      if (!iso) return;

      selectDate(iso);
      closeDatePicker();
    });
  }

  dpOutsideClickListener = (e) => {
    if (!dpPopover.hidden && !container.querySelector('#rlcDatePicker')?.contains(e.target)) {
      closeDatePicker();
    }
  };
  document.addEventListener('click', dpOutsideClickListener);
}

function closeDatePicker() {
  if (dpPopover) dpPopover.hidden = true;
  if (dpTrigger) dpTrigger.setAttribute('aria-expanded', 'false');
}

function selectDate(iso) {
  if (dpHidden) dpHidden.value = iso;
  if (dpLabel) dpLabel.textContent = iso;
}

function renderCalendar() {
  if (!dpCalTitle || !dpCalDays) return;

  dpCalTitle.textContent = `${MONTH_NAMES[dpCalMonth]} ${dpCalYear}`;

  const firstDay = new Date(dpCalYear, dpCalMonth, 1).getDay();
  const daysInMonth = new Date(dpCalYear, dpCalMonth + 1, 0).getDate();
  const localToday = new Date().toISOString().split('T')[0];
  const selectedVal = dpHidden ? dpHidden.value : localToday;

  const minISO = minBusinessDate;
  const maxISO = localToday;

  let html = '';

  // Previous month padding days
  const prevDays = new Date(dpCalYear, dpCalMonth, 0).getDate();
  for (let i = firstDay - 1; i >= 0; i--) {
    const d = prevDays - i;
    html += `<button type="button" class="rlc-day rlc-day--other-month" disabled>${d}</button>`;
  }

  // Current month days
  for (let d = 1; d <= daysInMonth; d++) {
    const iso = `${dpCalYear}-${String(dpCalMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const isBeforeMin = minISO && iso < minISO;
    const isAfterMax = maxISO && iso > maxISO;
    const hasEod = validEodDatesSet.size === 0 || validEodDatesSet.has(iso);
    const isDisabled = isBeforeMin || isAfterMax || !hasEod;

    const isSelected = iso === selectedVal;
    const isToday = iso === localToday;

    const classes = ['rlc-day'];
    if (isSelected) classes.push('rlc-day--selected');
    if (isToday) classes.push('rlc-day--today');
    if (isDisabled) classes.push('rlc-day--disabled');

    const disAttr = isDisabled ? ' disabled' : '';
    let titleAttr = '';
    if (isBeforeMin) titleAttr = ` title="Before initial business date (${minISO})"`;
    else if (isAfterMax) titleAttr = ` title="Future date not allowed"`;
    else if (!hasEod) titleAttr = ` title="No End of Day (EOD) performed for ${iso}"`;

    html += `<button type="button" class="${classes.join(' ')}" data-iso="${iso}"${disAttr}${titleAttr}>${d}</button>`;
  }

  // Next month padding to fill row
  const totalCells = firstDay + daysInMonth;
  const remainder = totalCells % 7;
  if (remainder > 0) {
    const fill = 7 - remainder;
    for (let i = 1; i <= fill; i++) {
      html += `<button type="button" class="rlc-day rlc-day--other-month" disabled>${i}</button>`;
    }
  }

  dpCalDays.innerHTML = html;
}

function setStatusFilter(container, st) {
  currentFilters.status = st;
  const statusTabs = container.querySelectorAll('.rlc-status-tab');
  statusTabs.forEach((tab) => {
    if (tab.dataset.status === st) tab.classList.add('active');
    else tab.classList.remove('active');
  });
  updateClearFilterButton(container);
  currentPage = 1;
  loadTransferHistory(container);
}

function updateClearFilterButton(container) {
  const btnReset = container.querySelector('#btnResetFilters');
  if (!btnReset) return;
  const hasActive = currentFilters.status !== 'all' || !!currentFilters.search || !!currentFilters.date;
  btnReset.style.display = hasActive ? 'inline-flex' : 'none';
}

function applyCompactState(container) {
  const table = container.querySelector('#rlcMainTable');
  if (table) {
    table.classList.add('compact-mode');
  }
}

async function loadTransferHistory(container, isBackground = false) {
  const tbody = container.querySelector('#rlcLogsTbody');
  const cntCompleted = container.querySelector('#cntCompleted');
  const cntPending = container.querySelector('#cntPending');
  const cntFailed = container.querySelector('#cntFailed');
  const cntTotal = container.querySelector('#cntTotal');

  if (!isBackground && tbody) {
    tbody.innerHTML = '<tr><td colspan="7" class="rlc-loading-cell">Loading transfer logs...</td></tr>';
  }

  try {
    const res = await api.get(`/get_transfer_history?page=${currentPage}&per_page=${itemsPerPage}`);
    if (!res || !res.success) {
      if (!isBackground && tbody) {
        tbody.innerHTML = `<tr><td colspan="7" class="rlc-error-cell">Failed to load transfer history: ${(res && res.error) || 'Unknown error'}</td></tr>`;
      }
      return;
    }

    if (res && res.valid_eod_dates && Array.isArray(res.valid_eod_dates) && res.valid_eod_dates.length > 0) {
      validEodDatesSet = new Set(res.valid_eod_dates);
      const latestValidDate = res.default_report_date || res.valid_eod_dates[res.valid_eod_dates.length - 1];
      const currentVal = dpHidden ? dpHidden.value : '';
      if (!currentVal || !validEodDatesSet.has(currentVal)) {
        selectDate(latestValidDate);
        if (latestValidDate) {
          const d = new Date(`${latestValidDate}T00:00:00`);
          if (!isNaN(d.getTime())) {
            dpCalYear = d.getFullYear();
            dpCalMonth = d.getMonth();
          }
        }
      }
    }

    if (res && res.min_date) {
      minBusinessDate = res.min_date;
      const dateFilterInput = container.querySelector('#rlcDateFilter');
      if (dateFilterInput) dateFilterInput.setAttribute('min', res.min_date);
    }

    const rawHistory = res.history || [];
    const pag = res.pagination || {};

    // Calculate Summary Metrics across retrieved records
    let nComp = 0, nPend = 0, nFail = 0;
    rawHistory.forEach((f) => {
      const st = (f.status || '').toLowerCase();
      if (st.includes('completed') || st.includes('success')) nComp++;
      else if (st.includes('failed') || st.includes('error')) nFail++;
      else nPend++;
    });

    if (cntCompleted) cntCompleted.textContent = nComp;
    if (cntPending) cntPending.textContent = nPend;
    if (cntFailed) cntFailed.textContent = nFail;
    if (cntTotal) cntTotal.textContent = pag.total !== undefined ? pag.total : rawHistory.length;

    // Apply local client filters (status, search, date)
    let filtered = rawHistory.filter((item) => {
      if (currentFilters.status !== 'all') {
        const st = (item.status || '').toLowerCase();
        if (currentFilters.status === 'completed' && !st.includes('completed')) return false;
        if (currentFilters.status === 'pending' && (st.includes('completed') || st.includes('failed'))) return false;
        if (currentFilters.status === 'failed' && !st.includes('failed')) return false;
      }
      if (currentFilters.search) {
        const fname = (item.filename || '').toLowerCase();
        if (!fname.includes(currentFilters.search)) return false;
      }
      if (currentFilters.date) {
        if (item.date !== currentFilters.date) return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      if (tbody) {
        tbody.innerHTML = '<tr><td colspan="7" class="rlc-empty-cell">No transfer records match current filters.</td></tr>';
      }
      updatePagination(container, pag);
      return;
    }

    // Render Table Rows
    if (tbody) {
      tbody.innerHTML = filtered.map((f) => {
        const st = (f.status || 'pending').toLowerCase();
        let badgeClass = 'rlc-badge-info';
        let badgeText = 'Pending';
        let badgeTitle = 'Pending files are automatically retried every 20 seconds when online';

        if (st.includes('completed') || st.includes('success')) {
          badgeClass = 'rlc-badge-success';
          badgeText = 'Completed';
          badgeTitle = 'SFTP transmission successful';
        } else if (st.includes('failed') || st.includes('error')) {
          badgeClass = 'rlc-badge-danger';
          badgeText = 'Failed';
          badgeTitle = 'Transmission error - click retry or regenerate';
        }

        const formattedGross = f.gross_sales
          ? '₱ ' + Number(f.gross_sales).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : '-';

        const createdStr = formatTimestamp(f.timestamp);
        const transferredStr = formatTimestamp(f.date_transferred);

        const escapedDate = escapeAttr(f.date || '');
        const escapedFilename = escapeAttr(f.filename || '');

        let retryBtn = '';
        if (st !== 'completed') {
          retryBtn = `<button type="button" class="rlc-btn rlc-btn-xs rlc-btn-warning btn-retry-item" data-date="${escapedDate}" data-filename="${escapedFilename}" title="Retry SFTP transmission">Retry</button>`;
        }

        const regenBtn = `<button type="button" class="rlc-btn rlc-btn-xs rlc-btn-secondary btn-regen-item" data-date="${escapedDate}" data-filename="${escapedFilename}" title="Regenerate next batch for this date">Regenerate</button>`;

        return `
          <tr>
            <td>
              <div class="rlc-file-cell">
                <strong class="rlc-filename">${escapeHtml(f.filename || '-')}</strong>
                <span class="rlc-subtext">Business date: ${escapeHtml(f.date || '-')}</span>
              </div>
            </td>
            <td>
              <span class="rlc-badge ${badgeClass}" title="${escapeAttr(badgeTitle)}">${badgeText}</span>
            </td>
            <td style="text-align: center;">
              <span class="rlc-batch-pill">${escapeHtml(f.batch || '-')}</span>
            </td>
            <td style="text-align: right; font-weight: 600; color: var(--text-main);">
              ${formattedGross}
            </td>
            <td class="rlc-time-cell">${createdStr}</td>
            <td class="rlc-time-cell">${transferredStr}</td>
            <td style="text-align: right;">
              <div class="rlc-actions-cell">
                ${retryBtn}
                ${regenBtn}
              </div>
            </td>
          </tr>
        `;
      }).join('');

      // Wire Row Actions
      tbody.querySelectorAll('.btn-retry-item').forEach((btn) => {
        btn.addEventListener('click', () => handleRetryFile(container, btn));
      });
      tbody.querySelectorAll('.btn-regen-item').forEach((btn) => {
        btn.addEventListener('click', () => handleRegenFile(container, btn));
      });
    }

    // Render Pagination Controls
    updatePagination(container, pag);

  } catch (err) {
    console.error('[transfer_history] load error:', err);
    if (!isBackground && tbody) {
      tbody.innerHTML = '<tr><td colspan="7" class="rlc-error-cell">Error connecting to server.</td></tr>';
    }
  }
}

function updatePagination(container, pag) {
  const wrap = container.querySelector('#rlcPagination');
  const info = container.querySelector('#rlcPagInfo');
  const pages = container.querySelector('#rlcPagPages');
  const prevBtn = container.querySelector('#btnPrevPage');
  const nextBtn = container.querySelector('#btnNextPage');

  if (!wrap) return;
  if (!pag || !pag.total) {
    wrap.style.display = 'none';
    return;
  }

  wrap.style.display = 'flex';
  const total = pag.total || 0;
  const page = pag.page || 1;
  const numPages = pag.pages || 1;

  if (info) info.textContent = `Total ${total} file records`;
  if (pages) pages.textContent = `Page ${page} of ${numPages}`;

  if (prevBtn) prevBtn.disabled = !pag.has_prev;
  if (nextBtn) nextBtn.disabled = !pag.has_next;
}

async function handleGenerateRlc(container, btn) {
  const dateInput = container.querySelector('#rlcReportDate');
  const selectedDate = dateInput ? dateInput.value : '';
  const localToday = new Date().toISOString().split('T')[0];

  if (!selectedDate) {
    notify.error('Please select a report business date.');
    return;
  }
  if (selectedDate > localToday) {
    notify.error('Future business date is not allowed.');
    return;
  }
  if (minBusinessDate && selectedDate < minBusinessDate) {
    notify.error(`Selected date is earlier than initial business date (${minBusinessDate}).`);
    return;
  }
  if (validEodDatesSet.size > 0 && !validEodDatesSet.has(selectedDate)) {
    notify.error(`No End of Day (EOD) has been performed for ${selectedDate}. An End of Day is required before generating RLC files.`);
    return;
  }

  const origHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span>Generating & Sending...</span>';

  const progressWrap = container.querySelector('#rlcProgressWrap');
  const progressFill = container.querySelector('#rlcProgressFill');
  const progressStatus = container.querySelector('#rlcProgressStatus');

  if (progressWrap) progressWrap.style.display = 'block';
  if (progressFill) progressFill.style.width = '30%';
  if (progressStatus) progressStatus.textContent = 'Building RLC file and attempting SFTP transmission...';

  try {
    const res = await api.post('/generate_and_send_rlc', {
      target_date: selectedDate,
      regenerate: true,
      source: 'transfer_history',
    });

    if (progressFill) progressFill.style.width = '100%';

    if (res && res.success) {
      notify.success(res.message || 'RLC sales file generated and transmitted successfully!');
      loadTransferHistory(container);
    } else {
      notify.error((res && res.message) || (res && res.error) || 'Failed to generate RLC file.');
    }
  } catch (err) {
    console.error('[transfer_history] generate error:', err);
    notify.error('Network error generating RLC sales file.');
  } finally {
    btn.disabled = false;
    btn.innerHTML = origHtml;
    setTimeout(() => {
      if (progressWrap) progressWrap.style.display = 'none';
      if (progressFill) progressFill.style.width = '0%';
    }, 1200);
  }
}

async function handleRetryFile(container, btn) {
  const targetDate = btn.dataset.date;
  const filename = btn.dataset.filename;
  if (!targetDate) {
    notify.error('Missing business date for retry.');
    return;
  }

  btn.disabled = true;
  const origText = btn.textContent;
  btn.textContent = '...';

  try {
    const res = await api.post('/retry_rlc_transfer', {
      target_date: targetDate,
      date: targetDate,
    });

    if (res && res.success) {
      notify.success(res.message || `RLC transfer retry initiated for ${filename}`);
      loadTransferHistory(container);
    } else {
      notify.error((res && res.message) || (res && res.error) || 'Retry transfer failed.');
    }
  } catch (err) {
    console.error('[transfer_history] retry error:', err);
    notify.error('Network error retrying transfer.');
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

function getNextRlcFilename(targetDate, filename) {
  const dateStr = targetDate ? targetDate.replace(/-/g, '') : '';
  if (filename && filename.includes('.')) {
    const parts = filename.split('.');
    const base = parts[0];
    const ext = parts[1];
    if (ext && ext.startsWith('01') && ext.length >= 3) {
      const currentBatch = parseInt(ext.slice(2), 10) || 1;
      const nextBatch = currentBatch + 1;
      return `${base}.01${nextBatch}`;
    }
  }
  return dateStr ? `${dateStr}.012` : 'new compliance file';
}

async function handleRegenFile(container, btn) {
  const targetDate = btn.dataset.date;
  const filename = btn.dataset.filename;
  if (!targetDate) {
    notify.error('Missing business date for regeneration.');
    return;
  }

  const nextFilename = getNextRlcFilename(targetDate, filename);
  const confirmed = await modal.confirm(
    'Generate Next RLC Batch',
    `Generate next RLC batch for ${targetDate}? This will create file ${nextFilename}.`,
    {
      confirmLabel: 'Generate Batch',
      cancelLabel: 'Cancel',
      danger: false,
    }
  );
  if (!confirmed) {
    return;
  }

  btn.disabled = true;
  const origText = btn.textContent;
  btn.textContent = '...';

  try {
    const res = await api.post('/generate_and_send_rlc', {
      target_date: targetDate,
      regenerate: true,
      source: 'transfer_history',
    });

    if (res && res.success) {
      notify.success(res.message || `Next RLC batch generated for ${targetDate}`);
      loadTransferHistory(container);
    } else {
      notify.error((res && res.message) || (res && res.error) || 'Regeneration failed.');
    }
  } catch (err) {
    console.error('[transfer_history] regen error:', err);
    notify.error('Network error regenerating RLC file.');
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

function formatTimestamp(ts) {
  if (!ts) return '-';
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    return d.toLocaleString('en-US', {
      month: '2-digit',
      day: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
    });
  } catch (e) {
    return String(ts);
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function escapeAttr(str) {
  return escapeHtml(str);
}

export default { mount, destroy };
