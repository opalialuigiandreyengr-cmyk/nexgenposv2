/* =============================================================================
   pages/pos.js — cashier register (templates/pos.html)
   -----------------------------------------------------------------------------
   spa.js contract: default export { mount, destroy }. Ports the legacy
   pos.html inline script 1:1 (see .ulpi/design/pos-cashier-core.md P1-P18):
   localStorage cart persistence ('posCartState'), Qty/g step math with 0.1
   rounding, grab-/fp- category filtering, VAT = total - total/1.12, the
   dine-in table gate, the printer guard, POST /save_pos_order (or
   /save_add_items/<id> in add mode), and the success redirects. mount()
   flags the documentElement so pos.css hides the shell chrome for the
   full-viewport instrument; destroy() removes the flag. Cart rows are
   re-rendered by this module, so their icons mirror _icons.html specs
   inline (dashboard.js pattern).
   ========================================================================== */
'use strict';

import { api } from '../core/api.js';
import modal from '../core/modal.js';
import { notify } from '../core/toast.js';
import { guardPrinter } from '../core/guards.js';
import { openKeyboard } from '../core/keyboard.js';

const CART_STORAGE_KEY = 'posCartState';

/* Inline SVG set mirroring partials/_icons.html for JS-rendered rows. */
function iconSvg(name, size = 14) {
  const SPECS = {
    'minus': '<line x1="5" y1="12" x2="19" y2="12"/>',
    'plus': '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    'trash': '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  };
  return `<svg class="icon icon-${name}" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SPECS[name] || ''}</svg>`;
}

/* ---- Register state -------------------------------------------------------- */
let cart = [];
let activeCategory = 'all';
let unitMode = 'qty';

let posShell = null;
let posCartToggle = null;
let posCartBackdrop = null;
let posCartCount = null;
let cartItems = null;
let emptyCartMessage = null;
let subtotalEl = null;
let vatEl = null;
let totalEl = null;
let checkoutBtn = null;
let customerNameInput = null;
let orderTypeInput = null;
let tableNumberInput = null;
let unitToggle = null;
let productsGrid = null;
let searchInput = null;
let noResultsMessage = null;
let categoriesSidebar = null;
let categoryButtons = null;

/* ---- Qty math (V1 parity) -------------------------------------------------- */
function qtyStep() {
  return unitMode === 'g' ? 0.5 : 1;
}

function normalizeQty(value) {
  const qty = Number(value || 0);
  return Math.max(0, Math.round(qty * 10) / 10);
}

function formatQty(value) {
  const qty = normalizeQty(value);
  return Number.isInteger(qty) ? String(qty) : qty.toFixed(1);
}

function money(value) {
  return `₱${Number(value || 0).toFixed(2)}`;
}

/* ---- Persistence (V1 localStorage contract) -------------------------------- */
function saveCartState() {
  const payload = {
    cart,
    customerName: customerNameInput ? (customerNameInput.value || '') : '',
    orderType: orderTypeInput ? (orderTypeInput.value || 'dinein') : 'dinein',
    tableNumber: tableNumberInput ? (tableNumberInput.value || '') : '',
    unitMode,
  };
  try {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify(payload));
  } catch (_) {
    /* storage full or blocked: cart still works for this session */
  }
}

function loadCartState() {
  try {
    const raw = localStorage.getItem(CART_STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed.cart)) {
      cart = parsed.cart.filter((item) => item && item.id != null && typeof item.name === 'string');
    }
    if (typeof parsed.customerName === 'string' && customerNameInput) {
      customerNameInput.value = parsed.customerName;
    }
    if (typeof parsed.orderType === 'string' && orderTypeInput) {
      orderTypeInput.value = parsed.orderType;
    }
    if (typeof parsed.tableNumber === 'string' && tableNumberInput) {
      tableNumberInput.value = parsed.tableNumber;
    }
    if (parsed.unitMode === 'g' || parsed.unitMode === 'qty') unitMode = parsed.unitMode;
  } catch (e) {
    console.warn('Failed to load POS cart state', e);
  }
}

function clearCartStateUI() {
  cart = [];
  if (customerNameInput) customerNameInput.value = '';
  if (orderTypeInput) orderTypeInput.value = 'dinein';
  if (tableNumberInput) tableNumberInput.value = '';
  updateOrderTypeUI();
  try {
    localStorage.removeItem(CART_STORAGE_KEY);
  } catch (_) { /* ignore */ }
  renderCart();
}

/* ---- Cart rendering -------------------------------------------------------- */
function updateCartSummary() {
  const total = cart.reduce((sum, item) => sum + (item.price * item.quantity), 0);
  const qtyCount = cart.reduce((sum, item) => sum + item.quantity, 0);
  const subtotal = total / 1.12;
  const vat = total - subtotal;

  if (posCartCount) posCartCount.textContent = formatQty(qtyCount);
  if (subtotalEl) subtotalEl.textContent = money(subtotal);
  if (vatEl) vatEl.textContent = money(vat);
  if (totalEl) totalEl.textContent = money(total);
  if (checkoutBtn) checkoutBtn.disabled = cart.length === 0;
}

function renderCart() {
  if (cart.length === 0) {
    cartItems.innerHTML = '';
    cartItems.appendChild(emptyCartMessage);
    updateCartSummary();
    saveCartState();
    return;
  }

  cartItems.innerHTML = '';
  const list = document.createElement('ul');
  list.className = 'pos-cart-list';
  list.id = 'cartItemsList';

  cart.forEach((item) => {
    const lineTotal = item.price * item.quantity;
    const row = document.createElement('li');
    row.className = 'pos-cart-item';

    const main = document.createElement('div');
    main.className = 'pos-cart-item-main';

    const modBtn = document.createElement('button');
    modBtn.type = 'button';
    modBtn.className = 'pos-mod-btn';
    modBtn.dataset.action = 'modifier';
    modBtn.dataset.id = String(item.id);
    modBtn.title = 'Add Modifier';
    modBtn.textContent = 'MOD';

    const copy = document.createElement('div');
    copy.className = 'pos-cart-item-copy';
    const title = document.createElement('div');
    title.className = 'pos-cart-item-title';
    title.textContent = item.name;
    copy.appendChild(title);

    if (item.modifier) {
      const modifier = document.createElement('div');
      modifier.className = 'pos-item-modifier';
      modifier.textContent = item.modifier;
      copy.appendChild(modifier);
    }

    const qtyRow = document.createElement('div');
    qtyRow.className = 'pos-qty-row';
    const controls = document.createElement('div');
    controls.className = 'pos-qty-controls';
    const decBtn = document.createElement('button');
    decBtn.type = 'button';
    decBtn.className = 'pos-qty-btn';
    decBtn.dataset.action = 'decrease';
    decBtn.dataset.id = String(item.id);
    decBtn.title = 'Decrease quantity';
    decBtn.innerHTML = iconSvg('minus', 13);
    const incBtn = document.createElement('button');
    incBtn.type = 'button';
    incBtn.className = 'pos-qty-btn';
    incBtn.dataset.action = 'increase';
    incBtn.dataset.id = String(item.id);
    incBtn.title = 'Increase quantity';
    incBtn.innerHTML = iconSvg('plus', 13);
    controls.appendChild(decBtn);
    controls.appendChild(incBtn);
    const qtyValue = document.createElement('span');
    qtyValue.className = 'pos-qty-value';
    qtyValue.textContent = `Qty: ${formatQty(item.quantity)}`;
    qtyRow.appendChild(controls);
    qtyRow.appendChild(qtyValue);
    copy.appendChild(qtyRow);

    main.appendChild(modBtn);
    main.appendChild(copy);

    const side = document.createElement('div');
    side.className = 'pos-cart-item-side';
    const lineTotalEl = document.createElement('span');
    lineTotalEl.className = 'pos-cart-item-total';
    lineTotalEl.textContent = money(lineTotal);
    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'pos-remove-btn';
    removeBtn.dataset.action = 'remove';
    removeBtn.dataset.id = String(item.id);
    removeBtn.title = 'Remove item';
    removeBtn.innerHTML = iconSvg('trash', 14);
    side.appendChild(lineTotalEl);
    side.appendChild(removeBtn);

    row.appendChild(main);
    row.appendChild(side);
    list.appendChild(row);
  });

  cartItems.appendChild(list);
  updateCartSummary();
  saveCartState();
}

function addToCart(product) {
  const step = qtyStep();
  const existing = cart.find((item) => String(item.id) === String(product.id));
  if (existing) {
    existing.quantity = normalizeQty(existing.quantity + step);
  } else {
    cart.push({ id: product.id, name: product.name, price: Number(product.price), quantity: step });
  }
  renderCart();
}

/* ---- Filtering (V1 grab-/fp- rules) ---------------------------------------- */
function filterValue(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '_');
}

function applyProductFilters() {
  if (!productsGrid || !noResultsMessage) return;

  const search = String(searchInput ? searchInput.value : '').trim().toLowerCase();
  const selectedCategory = filterValue(activeCategory);
  const cards = Array.from(productsGrid.querySelectorAll('.pos-product, .product-card'));
  let visibleCount = 0;

  cards.forEach((card) => {
    /* Use visible text as a fallback because legacy/catalog records can have
       missing or HTML-escaped data attributes. */
    const name = String(
      card.dataset.productName || card.querySelector('.pos-product-name')?.textContent || '',
    ).trim().toLowerCase();
    const originalName = name;
    const category = filterValue(
      card.dataset.productCategory || card.querySelector('.pos-product-category')?.textContent,
    );

    const isGrabProduct = originalName.startsWith('grab-');
    const isFoodpandaProduct = originalName.startsWith('fp-');

    let categoryMatch = selectedCategory === 'all' || category === selectedCategory;

    if (selectedCategory === 'grab') {
      categoryMatch = isGrabProduct;
    } else if (selectedCategory === 'foodpanda') {
      categoryMatch = isFoodpandaProduct;
    } else if (isGrabProduct || isFoodpandaProduct) {
      categoryMatch = false;
    }

    const searchMatch = !search || name.includes(search) || category.includes(search);
    const visible = categoryMatch && searchMatch;
    card.hidden = !visible;
    if (visible) visibleCount += 1;
  });

  noResultsMessage.hidden = visibleCount !== 0;
}

function updateOrderTypeUI() {
  const badge = document.getElementById('tableNumberBadge');
  const value = tableNumberInput.value.trim();
  if (badge) badge.textContent = value || '-';
}

/* ---- Cart drawer (medium screens, V1 parity) ------------------------------- */
function isMediumCartDrawerMode() {
  return window.matchMedia('(max-width: 991.98px)').matches;
}

function toggleCartDrawer(forceOpen) {
  if (!isMediumCartDrawerMode()) return;
  const nextState = typeof forceOpen === 'boolean' ? forceOpen : !posShell.classList.contains('cart-open');
  posShell.classList.toggle('cart-open', nextState);
  posCartToggle.setAttribute('aria-expanded', nextState ? 'true' : 'false');
  posCartBackdrop.hidden = !nextState;
}

/* ---- Checkout -------------------------------------------------------------- */
function buildItemPayload() {
  return cart.map((item) => ({
    id: item.id,
    name: item.name,
    price: item.price,
    quantity: item.quantity,
    modifier: item.modifier || null,
  }));
}

async function handleCheckout() {
  if (cart.length === 0) {
    notify.warning('Your cart is empty');
    return;
  }

  const addToOrderIdEl = document.getElementById('addToOrderId');
  const isAddMode = addToOrderIdEl && addToOrderIdEl.value;

  if (!isAddMode && orderTypeInput.value === 'dinein' && !tableNumberInput.value.trim()) {
    notify.warning('Please select at least one table for dine-in');
    return;
    return;
  }

  const printerConnected = await guardPrinter();
  if (!printerConnected) return;

  const total = cart.reduce((sum, item) => sum + item.price * item.quantity, 0);
  const subtotal = total / 1.12;
  const vat = total - subtotal;

  checkoutBtn.disabled = true;

  try {
    if (isAddMode) {
      const data = await api.post(`/save_add_items/${addToOrderIdEl.value}`, {
        items: buildItemPayload(),
        subtotal,
        vat,
        total,
      });

      if (data && data.success) {
        clearCartStateUI();
        window.location.href = '/order/' + addToOrderIdEl.value;
      } else {
        notify.error((data && data.message) || 'Failed to add items');
      }
      return;
    }

    const payload = {
      customerName: customerNameInput ? (customerNameInput.value || '') : '',
      orderType: orderTypeInput ? orderTypeInput.value : 'dinein',
      tables: tableNumberInput && tableNumberInput.value
        ? tableNumberInput.value.split(',').map((v) => v.trim()).filter(Boolean)
        : [],
      subtotal,
      vat,
      total,
      items: buildItemPayload(),
      serverId: window.selectedServerId || null,
    };

    const data = await api.post('/save_pos_order', payload);

    if (data && data.success) {
      clearCartStateUI();
      window.location.href =
        `/orders?order_success=true&order_no=${encodeURIComponent(data.orderNo)}` +
        `&printed=${data.printed ? 'true' : 'false'}` +
        `&order_type=${encodeURIComponent(payload.orderType)}` +
        `&tables=${encodeURIComponent(payload.tables && Array.isArray(payload.tables) ? payload.tables.join(', ') : (payload.tables || ''))}`;
    } else {
      notify.error((data && data.message) || 'Failed to submit order');
    }
  } catch (error) {
    console.error('Register checkout failed:', error);
    if (error && error.detail && error.detail.eod_performed) {
      notify.error(error.message);
    } else if (error && error.message) {
      notify.error(error.message);
    } else {
      notify.error('Error submitting order');
    }
  } finally {
    checkoutBtn.disabled = cart.length === 0;
  }
}

/* ---- URL entry params (orders-board start flow, V1 parity) ----------------- */
function applyUrlParams() {
  const urlParams = new URLSearchParams(window.location.search);
  const paramOrderType = urlParams.get('order_type');
  const paramTableNumber = urlParams.get('table_number');
  const paramServerId = urlParams.get('server_id');
  const paramServerName = urlParams.get('server_name');

  if (paramOrderType) {
    orderTypeInput.value = paramOrderType;
    if (!paramTableNumber && tableNumberInput) {
      tableNumberInput.value = '';
    }
  }

  if (paramTableNumber) {
    tableNumberInput.value = paramTableNumber;
  }

  if (paramServerId) {
    window.selectedServerId = paramServerId;
    window.selectedServerName = paramServerName || '';
  }

  if (paramOrderType || paramTableNumber) {
    updateOrderTypeUI();
    saveCartState();
    if (window.history.replaceState) {
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }
}

/* ---- Wiring ---------------------------------------------------------------- */
function bindEvents() {
  productsGrid.addEventListener('click', (event) => {
    const card = event.target.closest('.pos-product');
    if (!card) return;
    addToCart({
      id: card.dataset.productId,
      name: card.dataset.productName,
      price: card.dataset.productPrice,
    });
  });

  /* Keep category filtering scoped to the category list on both register
     routes instead of depending on the shell's delegated click handler. */
  if (categoryButtons) {
    categoryButtons.addEventListener('click', (event) => {
      const btn = event.target.closest('.pos-cat-btn, .pos-category-btn');
      if (!btn || !categoryButtons.contains(btn)) return;

      categoryButtons.querySelectorAll('.pos-cat-btn, .pos-category-btn').forEach((categoryBtn) => {
        const pressed = categoryBtn === btn;
        categoryBtn.classList.toggle('is-active', pressed);
        categoryBtn.setAttribute('aria-pressed', String(pressed));
      });
      activeCategory = btn.dataset.category || 'all';
      applyProductFilters();
    });
  }

  posShell.addEventListener('click', (event) => {
    const actionBtn = event.target.closest('[data-action]');
    if (actionBtn) {
      const id = actionBtn.dataset.id;
      const action = actionBtn.dataset.action;
      const item = cart.find((entry) => String(entry.id) === String(id));
      if (!item) return;

      if (action === 'remove') {
        cart = cart.filter((entry) => String(entry.id) !== String(id));
      }
      if (action === 'increase') {
        item.quantity = normalizeQty(item.quantity + qtyStep());
      }
      if (action === 'decrease') {
        item.quantity = normalizeQty(item.quantity - qtyStep());
        if (item.quantity <= 0) {
          cart = cart.filter((entry) => String(entry.id) !== String(id));
        }
      }
      if (action === 'modifier') {
        openKeyboard(item.modifier || '', (newModifier) => {
          item.modifier = newModifier;
          renderCart();
          saveCartState();
        });
        return;
      }
      renderCart();
      return;
    }

    if (event.target.closest('[data-unit-mode]')) {
      const btn = event.target.closest('[data-unit-mode]');
      unitMode = btn.dataset.unitMode === 'g' ? 'g' : 'qty';
      document.querySelectorAll('.pos-unit-btn').forEach((b) => {
        const pressed = b === btn;
        b.classList.toggle('is-active', pressed);
        b.setAttribute('aria-pressed', String(pressed));
      });
      saveCartState();
      return;
    }

    if (event.target.closest('.pos-cart-toggle')) {
      toggleCartDrawer();
      return;
    }

    if (event.target === posCartBackdrop) {
      toggleCartDrawer(false);
      return;
    }

    /* Narrow screens: tapping the rail edge (not a button) opens the rail. */
    if (categoriesSidebar && (event.target === categoriesSidebar || event.target.closest('.pos-cat-brand'))) {
      posShell.classList.toggle('cat-open');
    }
  });

  searchInput.addEventListener('input', applyProductFilters);
  if (customerNameInput) customerNameInput.addEventListener('input', saveCartState);
  checkoutBtn.addEventListener('click', handleCheckout);
  document.getElementById('clearCartBtn').addEventListener('click', () => {
    /* In add-items mode, always go back to the order detail page.
       window.history.back() doesn't restore the shell properly when
       the POS register was loaded as a full page. */
    const addToOrderIdEl = document.getElementById('addToOrderId');
    if (addToOrderIdEl && addToOrderIdEl.value) {
      window.location.href = '/order/' + addToOrderIdEl.value;
    } else if (window.history.length > 1) {
      window.history.back();
    } else {
      window.location.href = '/orders';
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && posShell.classList.contains('cart-open')) {
      toggleCartDrawer(false);
    }
  });

  window.addEventListener('resize', () => {
    if (!isMediumCartDrawerMode()) {
      posShell.classList.remove('cart-open');
      posCartToggle.setAttribute('aria-expanded', 'false');
      posCartBackdrop.hidden = true;
    }
  });
}

/* ---- spa.js lifecycle ------------------------------------------------------ */
export default {
  mount() {
    posShell = document.getElementById('posShell');
    posCartToggle = document.getElementById('posCartToggle');
    posCartBackdrop = document.getElementById('posCartBackdrop');
    posCartCount = document.getElementById('posCartCount');
    cartItems = document.getElementById('cartItems');
    emptyCartMessage = document.getElementById('emptyCartMessage');
    subtotalEl = document.getElementById('subtotal');
    vatEl = document.getElementById('vat');
    totalEl = document.getElementById('total');
    checkoutBtn = document.getElementById('checkoutBtn');
    customerNameInput = document.getElementById('customerName');
    orderTypeInput = document.getElementById('orderType');
    tableNumberInput = document.getElementById('tableNumber');
    unitToggle = document.getElementById('posUnitToggle');
    productsGrid = document.getElementById('productsGrid');
    searchInput = document.getElementById('productSearch');
    noResultsMessage = document.getElementById('noResultsMessage');
    categoriesSidebar = document.getElementById('categoriesSidebar');
    categoryButtons = document.getElementById('categoryButtons');

    document.documentElement.classList.add('pos-register-active');

    bindEvents();
    loadCartState();
    applyUrlParams();
    updateOrderTypeUI();
    renderCart();
    applyProductFilters();
    toggleCartDrawer(false);

    if (searchInput) searchInput.focus({ preventScroll: true });
  },

  destroy() {
    document.documentElement.classList.remove('pos-register-active');
    cart = [];
    posShell = null;
  },
};
