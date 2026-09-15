/* =============================================================================
   js/pages/users.js — Users & Staff Management (Phase 5, Milestone 5.2)
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

/* Helper: Parse flashed alert messages from Flask HTML response */
function extractFlashedError(htmlText) {
  if (!htmlText) return null;
  const parser = new DOMParser();
  const doc = parser.parseFromString(htmlText, 'text/html');
  const dangerAlert = doc.querySelector('.alert-danger, .alert-error, .flash-danger, .flash-error, [class*="danger"], [class*="error"]');
  if (dangerAlert) {
    const text = dangerAlert.textContent.trim();
    if (text) return text;
  }
  if (htmlText.includes('Username already exists')) {
    return 'Username already exists. Please choose a different username.';
  }
  if (htmlText.includes('Password must be at least 8 characters')) {
    return 'Password must be at least 8 characters long.';
  }
  if (htmlText.includes('Invalid role')) {
    return 'Invalid role selected.';
  }
  if (htmlText.includes('Invalid status')) {
    return 'Invalid status selected.';
  }
  if (htmlText.includes('User ID and role are required')) {
    return 'User ID and role are required.';
  }
  return null;
}

/* Helper: Refresh users view and re-mount event listeners on swapped DOM */
async function refreshUsersView() {
  const viewRoot = document.querySelector('#view-root');
  if (window.htmx && viewRoot) {
    await window.htmx.ajax('GET', '/users', { target: '#view-root', swap: 'innerHTML' });
    destroy();
    mount(document.querySelector('#view-root'));
  } else {
    window.location.reload();
  }
}

function openAddUserModal() {
  const overlayId = 'usr-add-modal';
  if (document.getElementById(overlayId)) return;

  let hostEl = document.getElementById('modal-host');
  if (!hostEl) {
    hostEl = document.createElement('div');
    hostEl.id = 'modal-host';
    document.body.appendChild(hostEl);
  }

  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay';
  overlay.id = overlayId;

  const box = document.createElement('div');
  box.className = 'lib-modal-box is-warning lib-modal-warning usr-modal-box';

  box.innerHTML = `
    <h3 class="lib-modal-title lib-modal-warning-title" style="color: var(--text-main); margin-bottom: 0;">
      <span class="lib-modal-warning-icon" style="background: color-mix(in srgb, var(--color-primary) 14%, transparent); color: var(--color-primary);">
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="20" y1="8" x2="20" y2="14"/><line x1="23" y1="11" x2="17" y2="11"/></svg>
      </span>
      Create New User Account
    </h3>

    <form id="userAddModalForm" class="usr-modal-form">
      <div class="usr-modal-body">
        <div class="usr-modal-row-2col">
          <div class="usr-modal-field">
            <label class="usr-modal-label">Username <span style="color: var(--color-danger, #ef4444)">*</span></label>
            <input type="text" name="username" id="userAddName" placeholder="e.g. cashier_john" required autocomplete="off" class="usr-modal-input">
          </div>
          <div class="usr-modal-field">
            <label class="usr-modal-label">Password / PIN <span style="color: var(--color-danger, #ef4444)">*</span> <span style="font-size: 0.7rem; color: var(--text-muted); font-weight: normal;">(min. 8 chars)</span></label>
            <input type="password" name="password" id="userAddPass" placeholder="Min. 8 characters" required minlength="8" autocomplete="new-password" class="usr-modal-input">
          </div>
        </div>

        <div class="usr-modal-row-2col">
          <div class="usr-modal-field">
            <label class="usr-modal-label">Role <span style="color: var(--color-danger, #ef4444)">*</span></label>
            <select name="role" required class="usr-modal-select">
              <option value="cashier">Cashier</option>
              <option value="crew">Crew</option>
              <option value="manager">Manager</option>
              <option value="admin">Administrator</option>
              <option value="bir_guest">BIR Guest</option>
            </select>
          </div>
          <div class="usr-modal-field">
            <label class="usr-modal-label">Terminal MAC ID</label>
            <input type="text" name="mac_id" placeholder="Optional MAC binding" autocomplete="off" class="usr-modal-input">
          </div>
        </div>

        <div class="usr-modal-field">
          <label class="usr-modal-label">Magnetic Swipe Card ID</label>
          <input type="text" name="card_number" id="userAddCardInput" placeholder="Swipe card now or enter badge number..." autocomplete="off" class="usr-modal-input">
        </div>
      </div>

      <div class="usr-modal-actions">
        <button type="button" class="btn lib-modal-warning-cancel" id="btnCancelAddUser">Cancel</button>
        <button type="submit" class="btn user-btn-primary" style="flex: 1;" id="btnSubmitAddUser">Create User</button>
      </div>
    </form>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    overlay.remove();
  };

  box.querySelector('#btnCancelAddUser').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  const form = box.querySelector('#userAddModalForm');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const submitBtn = box.querySelector('#btnSubmitAddUser');
    const username = (form.querySelector('[name="username"]')?.value || '').trim();
    const password = form.querySelector('[name="password"]')?.value || '';
    const role = form.querySelector('[name="role"]')?.value;

    if (!username || !password || !role) {
      notify.error('Username, password, and role are required.');
      return;
    }

    if (password.length < 8) {
      notify.error('Password must be at least 8 characters long.');
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving...';

    try {
      const formData = new FormData(form);
      const res = await fetch('/create_user', {
        method: 'POST',
        headers: { 'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]') || {}).content || '' },
        body: formData,
      });

      const responseText = await res.text();
      const flashedError = extractFlashedError(responseText);

      if (res.ok && !flashedError) {
        notify.success(`User '${username}' created successfully.`);
        close();
        await refreshUsersView();
      } else {
        const errorMsg = flashedError || 'Error creating user account.';
        notify.error(errorMsg);
        submitBtn.disabled = false;
        submitBtn.textContent = 'Create User';
      }
    } catch (err) {
      console.error(err);
      notify.error('Network error creating user.');
      submitBtn.disabled = false;
      submitBtn.textContent = 'Create User';
    }
  });

  if (window.openModal) window.openModal(overlay.id);
  setTimeout(() => {
    const inputName = box.querySelector('#userAddName');
    if (inputName) inputName.focus();
  }, 100);
}

function openEditUserModal(user) {
  const overlayId = 'usr-edit-modal';
  if (document.getElementById(overlayId)) return;

  let hostEl = document.getElementById('modal-host');
  if (!hostEl) {
    hostEl = document.createElement('div');
    hostEl.id = 'modal-host';
    document.body.appendChild(hostEl);
  }

  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay';
  overlay.id = overlayId;

  const box = document.createElement('div');
  box.className = 'lib-modal-box is-warning lib-modal-warning usr-modal-box';

  const roleOptions = ['admin', 'manager', 'cashier', 'crew', 'bir_guest'].map((r) => {
    const label = r === 'admin' ? 'Administrator' : (r === 'bir_guest' ? 'BIR Guest' : r.charAt(0).toUpperCase() + r.slice(1));
    return `<option value="${r}" ${user.role === r ? 'selected' : ''}>${label}</option>`;
  }).join('');

  const statusOptions = ['active', 'banned', 'suspended'].map((s) => {
    return `<option value="${s}" ${user.status === s ? 'selected' : ''}>${s.charAt(0).toUpperCase() + s.slice(1)}</option>`;
  }).join('');

  box.innerHTML = `
    <h3 class="lib-modal-title lib-modal-warning-title" style="color: var(--text-main); margin-bottom: 0;">
      <span class="lib-modal-warning-icon" style="background: color-mix(in srgb, var(--color-primary) 14%, transparent); color: var(--color-primary);">
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
      </span>
      Edit User (${esc(user.username)})
    </h3>

    <form id="userEditModalForm" class="usr-modal-form">
      <input type="hidden" name="user_id" value="${user.id}">

      <div class="usr-modal-body">
        <div class="usr-modal-row-2col">
          <div class="usr-modal-field">
            <label class="usr-modal-label">Role <span style="color: var(--color-danger, #ef4444)">*</span></label>
            <select name="role" required class="usr-modal-select">
              ${roleOptions}
            </select>
          </div>
          <div class="usr-modal-field">
            <label class="usr-modal-label">Status <span style="color: var(--color-danger, #ef4444)">*</span></label>
            <select name="status" required class="usr-modal-select">
              ${statusOptions}
            </select>
          </div>
        </div>

        <div class="usr-modal-field">
          <label class="usr-modal-label">Reset Password / PIN <span style="font-size: 0.7rem; color: var(--text-muted); font-weight: normal;">(min. 8 chars if changing)</span></label>
          <input type="password" name="password" id="userEditPass" placeholder="Leave blank to keep unchanged" autocomplete="new-password" class="usr-modal-input">
        </div>

        <div class="usr-modal-row-2col">
          <div class="usr-modal-field">
            <label class="usr-modal-label">Terminal MAC ID</label>
            <input type="text" name="mac_id" value="${esc(user.mac_id || '')}" placeholder="Optional MAC binding" autocomplete="off" class="usr-modal-input">
          </div>
          <div class="usr-modal-field">
            <label class="usr-modal-label">Swipe Card ID</label>
            <input type="text" name="card_number" value="${esc(user.card_number || '')}" placeholder="Optional badge number" autocomplete="off" class="usr-modal-input">
          </div>
        </div>
      </div>

      <div class="usr-modal-actions">
        <button type="button" class="btn lib-modal-warning-cancel" id="btnCancelEditUser">Cancel</button>
        <button type="submit" class="btn user-btn-primary" style="flex: 1;" id="btnSubmitEditUser">Save Changes</button>
      </div>
    </form>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    overlay.remove();
  };

  box.querySelector('#btnCancelEditUser').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  const form = box.querySelector('#userEditModalForm');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const submitBtn = box.querySelector('#btnSubmitEditUser');
    const password = form.querySelector('[name="password"]')?.value || '';

    if (password && password.length < 8) {
      notify.error('New password must be at least 8 characters long.');
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving...';

    try {
      const formData = new FormData(form);
      const res = await fetch('/edit_user', {
        method: 'POST',
        headers: { 'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]') || {}).content || '' },
        body: formData,
      });

      const responseText = await res.text();
      const flashedError = extractFlashedError(responseText);

      if (res.ok && !flashedError) {
        notify.success(`User '${user.username}' updated successfully.`);
        close();
        await refreshUsersView();
      } else {
        const errorMsg = flashedError || 'Error updating user.';
        notify.error(errorMsg);
        submitBtn.disabled = false;
        submitBtn.textContent = 'Save Changes';
      }
    } catch (err) {
      console.error(err);
      notify.error('Network error updating user.');
      submitBtn.disabled = false;
      submitBtn.textContent = 'Save Changes';
    }
  });

  if (window.openModal) window.openModal(overlay.id);
}

export function mount(el) {
  rootEl = el || document.querySelector('#view-root') || document;
  destroyFns = [];

  const searchInput = rootEl.querySelector('#userSearchInput');
  const roleFilter = rootEl.querySelector('#userRoleFilter');
  const statusFilter = rootEl.querySelector('#userStatusFilter');
  const userRows = rootEl.querySelectorAll('#userTableBody tr');
  const emptyState = rootEl.querySelector('#userEmpty');
  const btnAdd = rootEl.querySelector('#btnAddUser');
  const tableBody = rootEl.querySelector('#userTableBody');

  /* Filtering */
  const filterRows = () => {
    const q = searchInput ? searchInput.value.trim().toLowerCase() : '';
    const r = roleFilter ? roleFilter.value : '';
    const s = statusFilter ? statusFilter.value : '';
    let visibleCount = 0;

    userRows.forEach((row) => {
      const uname = (row.dataset.username || '').toLowerCase();
      const urole = row.dataset.role || '';
      const ustatus = row.dataset.status || '';

      const matchesQ = !q || uname.includes(q);
      const matchesR = !r || urole === r;
      const matchesS = !s || ustatus === s;

      const visible = matchesQ && matchesR && matchesS;
      row.style.display = visible ? '' : 'none';
      if (visible) visibleCount++;
    });

    if (emptyState) emptyState.style.display = visibleCount === 0 ? 'block' : 'none';
  };

  if (searchInput) searchInput.addEventListener('input', filterRows);
  if (roleFilter) roleFilter.addEventListener('change', filterRows);
  if (statusFilter) statusFilter.addEventListener('change', filterRows);

  /* Add User Button */
  if (btnAdd) btnAdd.addEventListener('click', openAddUserModal);

  /* Table Edit & Delete Actions */
  if (tableBody) {
    tableBody.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;

      const action = btn.dataset.action;
      const id = btn.dataset.id;
      const username = btn.dataset.username || 'this user';

      if (action === 'edit') {
        const u = {
          id,
          username,
          role: btn.dataset.role || 'cashier',
          status: btn.dataset.status || 'active',
          mac_id: btn.dataset.mac || '',
          card_number: btn.dataset.card || '',
        };
        openEditUserModal(u);
      } else if (action === 'delete') {
        const confirmed = await modal.confirm(
          'Delete User Account',
          `Are you sure you want to delete user "${username}"? This action cannot be undone.`,
          { danger: true, confirmLabel: 'Delete User' }
        );
        if (!confirmed) return;

        try {
          const formData = new FormData();
          formData.append('user_id', id);
          formData.append('confirmation', 'delete');

          const res = await fetch('/delete_user', {
            method: 'POST',
            headers: { 'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]') || {}).content || '' },
            body: formData,
          });

          const responseText = await res.text();
          const flashedError = extractFlashedError(responseText);

          if (res.ok && !flashedError) {
            notify.success(`User "${username}" deleted successfully.`);
            await refreshUsersView();
          } else {
            await modal.error('Delete Failed', flashedError || 'Unable to delete user account.');
          }
        } catch (err) {
          console.error(err);
          await modal.error('Delete Error', 'Network error deleting user.');
        }
      }
    });
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
