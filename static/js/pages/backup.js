/* =============================================================================
   js/pages/backup.js — System Backups (Phase 5, Milestone 5.4)
   =============================================================================
   SPA contract: default export { mount, destroy }.
   ========================================================================== */
'use strict';

import { api } from '../core/api.js';
import modal from '../core/modal.js';
import { notify } from '../core/toast.js';

let rootEl = null;
let destroyFns = [];

/* Helper: Escape HTML */
function esc(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* Helper: Format bytes */
function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

async function loadBackups() {
  if (!rootEl) return;
  const tbody = rootEl.querySelector('#backupTableBody');
  const empty = rootEl.querySelector('#backupEmpty');
  const statCount = rootEl.querySelector('#statBackupCount');
  const statDate = rootEl.querySelector('#statLatestDate');

  if (!tbody) return;

  try {
    const res = await api.get('/list_backups');
    const backups = (res && res.backups) || [];

    if (statCount) statCount.textContent = backups.length.toString();
    if (statDate && backups.length > 0) {
      statDate.textContent = backups[0].created_at || backups[0].date || 'Recent';
    }

    if (backups.length === 0) {
      tbody.innerHTML = '';
      if (empty) empty.style.display = 'block';
      return;
    }

    if (empty) empty.style.display = 'none';

    tbody.innerHTML = backups.map((b) => {
      const filename = b.filename || b.name || '';
      const sizeStr = formatBytes(b.size || b.size_bytes || 0);
      const dateStr = b.created_at || b.date || b.timestamp || '—';

      return `
        <tr data-filename="${esc(filename)}">
          <td>
            <div class="backup-file-cell">
              <span class="backup-file-icon">
                <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
              </span>
              <span class="backup-file-name">${esc(filename)}</span>
            </div>
          </td>
          <td><span style="font-family: var(--font-mono);">${esc(sizeStr)}</span></td>
          <td><span style="font-family: var(--font-mono); color: var(--text-secondary);">${esc(dateStr)}</span></td>
          <td>
            <div class="backup-actions">
              <a href="/download_backup/${encodeURIComponent(filename)}" class="backup-btn-action backup-btn-download" download>
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                <span>Download</span>
              </a>
              <button type="button" class="backup-btn-action backup-btn-delete" data-action="delete" data-filename="${esc(filename)}">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
                <span>Delete</span>
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join('');

  } catch (err) {
    console.error('Error loading backups:', err);
    tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--color-danger); padding: 2rem;">Error connecting to backups storage.</td></tr>`;
  }
}

export function mount(el) {
  rootEl = el || document.querySelector('#view-root') || document;
  destroyFns = [];

  const btnCreate = rootEl.querySelector('#btnCreateBackup');
  const tbody = rootEl.querySelector('#backupTableBody');

  if (btnCreate) {
    btnCreate.addEventListener('click', async () => {
      btnCreate.disabled = true;
      btnCreate.innerHTML = `<span class="lib-spinner"></span> <span>Creating Backup...</span>`;

      try {
        const formData = new FormData();
        formData.append('backup_type', 'full');

        const res = await fetch('/backup_system', {
          method: 'POST',
          headers: { 'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]') || {}).content || '' },
          body: formData,
        });

        if (res.ok) {
          notify.success('Database backup created successfully.');
          loadBackups();
        } else {
          notify.error('Failed to create system backup.');
        }
      } catch (err) {
        notify.error('Network error during backup creation.');
      } finally {
        btnCreate.disabled = false;
        btnCreate.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg> <span>+ Create Backup Now</span>`;
      }
    });
  }

  if (tbody) {
    tbody.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-action="delete"]');
      if (!btn) return;

      const filename = btn.dataset.filename;
      const confirmed = await modal.confirm(
        'Delete Backup Archive',
        `Are you sure you want to permanently delete backup "${filename}"?`,
        { danger: true, confirmLabel: 'Delete Backup' }
      );
      if (!confirmed) return;

      try {
        const res = await api.delete(`/delete_backup/${encodeURIComponent(filename)}`);
        if (res && res.success) {
          notify.success(`Backup "${filename}" deleted.`);
          loadBackups();
        } else {
          await modal.error('Delete Failed', (res && res.message) || 'Unable to delete backup file.');
        }
      } catch (err) {
        await modal.error('Delete Error', 'Network error deleting backup file.');
      }
    });
  }

  loadBackups();
}

export function destroy() {
  destroyFns.forEach((fn) => {
    try { fn(); } catch (e) {}
  });
  destroyFns = [];
  rootEl = null;
}

export default { mount, destroy };
