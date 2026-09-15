/* =============================================================================
   js/pages/categories.js — Category Management (Phase 5, Milestone 5.1)
   =============================================================================
   SPA contract: default export { mount, destroy }.
   ========================================================================== */
'use strict';

import modal from '../core/modal.js';
import { notify } from '../core/toast.js';

let rootEl = null;
let destroyFns = [];

function openAddCategoryModal() {
  const overlayId = 'cat-add-modal';
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
  box.className = 'lib-modal-box is-warning lib-modal-warning';
  box.style.cssText = 'max-width: 440px; width: 100%;';

  box.innerHTML = `
    <h3 class="lib-modal-title lib-modal-warning-title" style="color: var(--text-main);">
      <span class="lib-modal-warning-icon" style="background: color-mix(in srgb, var(--color-primary) 14%, transparent); color: var(--color-primary);">
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>
      </span>
      Add New Category
    </h3>
    <form id="catAddModalForm" style="display: flex; flex-direction: column; gap: 0.85rem; margin-top: 0.75rem;">
      <div style="display: flex; flex-direction: column; gap: 0.35rem;">
        <label style="font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted);">
          Category Name <span style="color: var(--color-danger, #ef4444)">*</span>
        </label>
        <input type="text" name="name" id="catModalInputName" placeholder="e.g. Appetizers, Beverages, Desserts" required autocomplete="off"
          style="width: 100%; padding: 0.6rem 0.85rem; background: var(--bg-input, var(--bg-main)); border: 1px solid var(--border-color); border-radius: var(--radius-md); color: var(--text-main); font-size: 0.88rem; box-sizing: border-box;">
      </div>

      <div class="lib-modal-warning-actions" style="margin-top: 1rem;">
        <button type="button" class="btn lib-modal-warning-cancel" id="btnCancelCatModal">Cancel</button>
        <button type="submit" class="btn cat-btn-primary" style="flex: 1; justify-content: center; height: auto;" id="btnSubmitCatModal">Create Category</button>
      </div>
    </form>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    overlay.remove();
  };

  box.querySelector('#btnCancelCatModal').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  const form = box.querySelector('#catAddModalForm');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = box.querySelector('#catModalInputName');
    const name = input.value.trim();
    if (!name) return;

    const submitBtn = box.querySelector('#btnSubmitCatModal');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Creating...';

    try {
      const formData = new FormData();
      formData.append('name', name);

      const res = await fetch('/add_category', {
        method: 'POST',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]') || {}).content || '',
        },
        body: formData,
      });

      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        notify.success(`Category "${name}" created successfully.`);
        close();
        if (window.htmx) {
          window.htmx.ajax('GET', '/categories', { target: '#view-root', swap: 'innerHTML' });
        } else {
          window.location.reload();
        }
      } else {
        notify.error(data.message || 'Error creating category.');
        submitBtn.disabled = false;
        submitBtn.textContent = 'Create Category';
      }
    } catch (err) {
      console.error(err);
      notify.error('Network error creating category.');
      submitBtn.disabled = false;
      submitBtn.textContent = 'Create Category';
    }
  });

  if (window.openModal) window.openModal(overlay.id);
  setTimeout(() => {
    const input = box.querySelector('#catModalInputName');
    if (input) input.focus();
  }, 80);
}

export function mount(el) {
  rootEl = el || document.querySelector('#view-root') || document;
  destroyFns = [];

  const searchInput = rootEl.querySelector('#catSearchInput');
  const catGrid = rootEl.querySelector('#catGrid');
  const catCards = rootEl.querySelectorAll('.cat-card');
  const emptyState = rootEl.querySelector('#catEmpty');
  const countBadge = rootEl.querySelector('#catCountBadge');
  const btnAdd = rootEl.querySelector('#btnAddCategory');

  /* Client-side search */
  if (searchInput) {
    const onSearch = () => {
      const q = searchInput.value.trim().toLowerCase();
      let visibleCount = 0;

      catCards.forEach((card) => {
        const name = (card.dataset.categoryName || '').toLowerCase();
        const matches = !q || name.includes(q);
        card.style.display = matches ? 'flex' : 'none';
        if (matches) visibleCount++;
      });

      if (emptyState) emptyState.style.display = visibleCount === 0 ? 'block' : 'none';
      if (countBadge) countBadge.textContent = `${visibleCount} ${visibleCount === 1 ? 'Category' : 'Categories'} Found`;
    };

    searchInput.addEventListener('input', onSearch);
    destroyFns.push(() => searchInput.removeEventListener('input', onSearch));
  }

  /* Add button */
  if (btnAdd) {
    btnAdd.addEventListener('click', openAddCategoryModal);
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
