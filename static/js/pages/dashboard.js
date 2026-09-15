/*
   pages/dashboard.js — read instrument (KPI board + Chart.js panels)
   -----------------------------------------------------------------------------
   spa.js contract: default export { mount, destroy }. mount() reads the
   JSON bridges (#dashSalesData / #dashProductData — the legacy main.dashboard
   context + the V2 before_render_template enrichment), builds the chart
   palette from computed CSS tokens, renders both charts and wires the
   segmented filters; destroy() tears charts, observer and listeners down so
   htmx swaps never leak state.

   The global range bar (preset chips + From/To date inputs + Apply/Reset)
   fetches /dashboard/range JSON — a full-dashboard snapshot: KPI totals
   with prior-period deltas (same-length by default, month-aligned for the
   default MTD view), the daily/weekly/monthly series, per-category top
   products, top orders and the summary numbers. The default view is month
   to date (start of month -> today) — the server renders MTD everywhere
   and the range bar inputs default to it. Applying a
   range re-renders every panel in place; Reset restores the server-rendered
   default view (captured at mount) — no reloads either way. The MTD/YTD
   strip and the product Today/Yesterday/Month segment hide while a custom
   range is active.

   Product data shape (enrichment): per range { 'All': [...], '<category>':
   [...], '_categories': [...] } — rows carry { product_name, quantity, peso }.
   The category select and the Quantity/Peso toggle re-sort + re-render the
   top-N rows client-side (no requests).

   Chart.js v3.9.1 is vendored UMD — a side-effect import at module scope
   (no script tag, no load race): the UMD wrapper lands on window.Chart via
   the globalThis fallback before this module's body runs.

   Theme flips are watched via MutationObserver on html[data-theme]; charts
   are destroyed and re-created with the new palette (tokens are resolved at
   init time, so re-init is the deterministic path). No polling: this is a
   read view, same as V1 — data is server-rendered per navigation.
   ========================================================================== */
'use strict';

import '../../vendor/chartjs/chart.min.js';

const RANGE_CAPTION = {
  /* Default view is month to date: every granularity shows the MTD series
     until a custom range replaces it with real dates. */
  sales: { daily: 'This month', weekly: 'This month', monthly: 'This month' },
  product: { today: 'Today', yesterday: 'Yesterday', month: 'This month' },
};

const SALES_KEYS = { daily: 'daily', weekly: 'weekly', monthly: 'monthly' };
const PRODUCT_KEYS = { today: 'today', yesterday: 'yesterday', month: 'month', range: 'range' };
const TOP_ROWS = 8;

let salesChart = null;
let productChart = null;
let themeObserver = null;
let palette = null;
let animFrames = [];

/* Client-side state for the product panel (re-rendered on any change). */
const productState = { range: 'today', category: 'All', metric: 'qty', viewType: 'products' };
let productData = {};

/* Sales trend data + custom range state. salesData starts from the JSON
   bridge and is replaced by /dashboard/range fetches (presets/custom).
   globalRange tracks an applied range so Reset can restore the default
   view; initialView is the DOM snapshot taken at mount. */
let salesData = {};
let salesCustom = null;
let globalRange = null;
let initialView = null;
let disposed = false;

/* ---- Custom Date Range Picker state -------------------------------------- */
let drpOpen = false;
let drpCalYear = 0;
let drpCalMonth = 0; /* 0-indexed (left calendar) */
let drpStartISO = null; /* selected start */
let drpEndISO = null;   /* selected end */
let drpHoverISO = null; /* currently hovered date while picking end */
let drpPicking = false; /* true while waiting for end click */

/* ---- Small helpers --------------------------------------------------------- */
function el(id) {
  return document.getElementById(id);
}

/* Parse a computed rgb()/rgba()/hex string into {r, g, b}. */
function parseColor(value) {
  const match = String(value).match(
    /rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)(?:[,\s/]+([\d.]+))?\)/
  );
  if (match) return { r: +match[1], g: +match[2], b: +match[3] };
  const hex = String(value).match(/#([0-9a-f]{6})/i);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }
  return { r: 148, g: 163, b: 184 };
}

function rgba(color, alpha) {
  const c = parseColor(color);
  return `rgba(${c.r}, ${c.g}, ${c.b}, ${alpha})`;
}

function formatMoney(value) {
  return `₱${Number(value || 0).toLocaleString('en-PH', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/* Compact ₱ ticks for large bar values: ₱1.2M / ₱3.4K / ₱500. */
function formatCompactPeso(value) {
  const n = Number(value || 0);
  if (n >= 1000000) return `₱${(n / 1000000).toFixed(1).replace(/\.0$/, '')}M`;
  if (n >= 1000) return `₱${(n / 1000).toFixed(1).replace(/\.0$/, '')}K`;
  return `₱${n.toLocaleString('en-PH', { maximumFractionDigits: 0 })}`;
}

function formatQuantity(value) {
  const n = Number(value || 0);
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

function readJson(id) {
  const node = el(id);
  if (!node) return {};
  try {
    return JSON.parse(node.textContent);
  } catch (error) {
    console.error('[dashboard] invalid JSON bridge', id, error);
    return {};
  }
}

/* Escape untrusted server strings before injecting into table markup. */
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

/* Minimal inline SVG set (mirrors partials/_icons.html specs) for rows the
   client re-renders — the Jinja macro only exists at render time. */
function iconSvg(name, size = 12) {
  const SPECS = {
    'arrow-up': '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
    'arrow-down': '<line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/>',
    'check': '<path d="M20 6L9 17l-5-5"/>',
    'clock': '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    'x': '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    'cart': '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>',
    'table': '<path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9"/>',
  };
  return `<svg class="icon icon-${name}" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SPECS[name] || ''}</svg>`;
}

function formatTime(iso) {
  if (!iso) return '&mdash;';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '&mdash;';
  return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

/* ---- Palette: resolved from computed tokens at init time ------------------- */
function readPalette() {
  const styles = getComputedStyle(document.documentElement);
  const read = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
  return {
    teal: read('--color-primary', '#14B8A6'),
    amber: read('--color-accent', '#F59E0B'),
    muted: read('--color-muted', '#94A3B8'),
    text: read('--text-main', '#0F172A'),
    textSecondary: read('--text-secondary', '#334155'),
    border: read('--color-border', 'rgba(148,163,184,0.14)'),
    tooltipBg: read('--nav-bg', '#0F172A'),
    tooltipBorder: read('--nav-border', 'rgba(148,163,184,0.2)'),
    tooltipText: read('--nav-text-hover', '#F8FAFC'),
    mono: read('--font-mono', 'JetBrains Mono'),
    reduceMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  };
}

function baseTooltip(p) {
  return {
    backgroundColor: p.tooltipBg,
    titleColor: p.tooltipText,
    bodyColor: p.tooltipText,
    borderColor: p.tooltipBorder,
    borderWidth: 1,
    padding: 10,
    cornerRadius: 8,
    titleFont: { family: p.mono, size: 12, weight: '600' },
    bodyFont: { family: p.mono, size: 11 },
    displayColors: true,
    boxPadding: 4,
  };
}

/* ---- Sales overview: two-line chart (Net teal / Gross amber) --------------- */
function createSalesChart(rows, p) {
  const canvas = el('salesChart');
  if (!canvas || !window.Chart) return null;
  if (salesChart) salesChart.destroy();

  const empty = el('salesChartEmpty');
  const hasData = Array.isArray(rows) && rows.length > 0;
  canvas.style.display = hasData ? '' : 'none';
  if (empty) empty.hidden = hasData;
  if (!hasData) return null;

  const caption = el('dashSalesRange');
  const rangeText = caption ? caption.textContent : 'the selected period';
  canvas.setAttribute(
    'aria-label',
    `Line chart of net and gross sales for ${rangeText}`
  );

  salesChart = new window.Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: rows.map((item) => item.display_date),
      datasets: [
        {
          label: 'Net Sales',
          data: rows.map((item) => item.net_sales),
          borderColor: p.teal,
          backgroundColor: rgba(p.teal, 0.1),
          borderWidth: 2.5,
          fill: true,
          tension: 0.35,
          pointRadius: 3,
          pointHoverRadius: 5,
          pointBackgroundColor: p.teal,
          pointBorderColor: p.tooltipBg,
          pointBorderWidth: 2,
          spanGaps: true,
        },
        {
          label: 'Gross Sales',
          data: rows.map((item) => item.gross_sales),
          borderColor: p.amber,
          backgroundColor: rgba(p.amber, 0.06),
          borderWidth: 2,
          fill: true,
          tension: 0.35,
          pointRadius: 3,
          pointHoverRadius: 5,
          pointBackgroundColor: p.amber,
          pointBorderColor: p.tooltipBg,
          pointBorderWidth: 2,
          spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: p.reduceMotion ? false : { duration: 280, easing: 'easeOutQuart' },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'top',
          align: 'end',
          labels: {
            color: p.muted,
            usePointStyle: true,
            pointStyle: 'circle',
            padding: 16,
            boxWidth: 8,
            boxHeight: 8,
            font: { family: p.mono, size: 11, weight: '500' },
          },
        },
        tooltip: {
          ...baseTooltip(p),
          callbacks: {
            label(context) {
              return `${context.dataset.label}: ${formatMoney(context.parsed.y)}`;
            },
          },
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: {
            color: p.muted,
            font: { family: p.mono, size: 10 },
            callback(value) {
              return formatCompactPeso(value);
            },
          },
          grid: { color: rgba(p.border, 0.8), drawBorder: false },
        },
        x: {
          ticks: { color: p.muted, font: { family: p.mono, size: 10 } },
          grid: { display: false, drawBorder: false },
        },
      },
    },
  });
  return salesChart;
}

/* ---- Top products: horizontal bars (teal, rank = y axis) ------------------- */
function sortTopRows(rows, metric) {
  const key = metric === 'peso' ? 'peso' : 'quantity';
  return [...(rows || [])]
    .sort((a, b) => Number(b[key] || 0) - Number(a[key] || 0))
    .slice(0, TOP_ROWS);
}

function createProductChart(rows, p, metric) {
  const canvas = el('productChart');
  if (!canvas || !window.Chart) return null;
  if (productChart) productChart.destroy();

  const empty = el('productChartEmpty');
  const hasData = Array.isArray(rows) && rows.length > 0;
  canvas.style.display = hasData ? '' : 'none';
  if (empty) empty.hidden = hasData;
  if (!hasData) return null;

  const pesoMode = metric === 'peso';
  const datasetLabel = pesoMode ? 'Peso Sales' : 'Quantity Sold';
  const values = rows.map((item) => (pesoMode ? item.peso : item.quantity));
  const total = values.reduce((sum, value) => sum + Number(value || 0), 0);

  const caption = el('dashProductRange');
  const rangeText = caption ? caption.textContent : '';
  const categoryText = productState.category === 'All' ? '' : ` in ${productState.category}`;
  canvas.setAttribute(
    'aria-label',
    `Horizontal bar chart of top selling products by ${pesoMode ? 'peso sales' : 'quantity sold'} for ${rangeText}${categoryText}`
  );

  productChart = new window.Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels: rows.map((item) => item.product_name),
      datasets: [
        {
          label: datasetLabel,
          data: values,
          backgroundColor: rgba(p.teal, 0.85),
          hoverBackgroundColor: p.teal,
          borderRadius: 0,
          borderSkipped: false,
          maxBarThickness: 22,
        },
      ],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      animation: p.reduceMotion ? false : { duration: 280, easing: 'easeOutQuart' },
      plugins: {
        legend: { display: false },
        tooltip: {
          ...baseTooltip(p),
          callbacks: {
            label(context) {
              const value = context.parsed.x || 0;
              const share = total > 0 ? Math.round((value / total) * 100) : 0;
              const row = rows[context.dataIndex] || {};
              const qty = formatQuantity(row.quantity);
              const peso = formatMoney(row.peso);
              return pesoMode
                ? `${context.label}: ${formatMoney(value)} · ${qty} units (${share}%)`
                : `${context.label}: ${qty} units · ${peso} (${share}%)`;
            },
          },
        },
      },
      scales: {
        x: {
          beginAtZero: true,
          ticks: {
            color: p.muted,
            font: { family: p.mono, size: 10 },
            callback(value) {
              return pesoMode
                ? formatCompactPeso(value)
                : Number.isInteger(value)
                  ? value
                  : '';
            },
          },
          grid: { color: rgba(p.border, 0.8), drawBorder: false },
        },
        y: {
          ticks: {
            color: p.textSecondary,
            font: { family: p.mono, size: 11 },
          },
          grid: { display: false, drawBorder: false },
        },
      },
    },
  });
  return productChart;
}

/* ---- Category select: rebuilt per range, selection kept when valid --------- */
function populateCategorySelect(rangeValue) {
  const select = el('dashCategorySelect');
  if (!select) return 'All';
  const scope = productData[PRODUCT_KEYS[rangeValue]] || { All: [], _categories: [] };
  const categories = scope._categories || [];
  const previous = select.value;
  select.innerHTML = '';

  const allOption = document.createElement('option');
  allOption.value = 'All';
  allOption.textContent = 'All categories';
  select.appendChild(allOption);
  categories.forEach((category) => {
    const option = document.createElement('option');
    option.value = category;
    option.textContent = category;
    select.appendChild(option);
  });

  const keep = previous && categories.includes(previous) ? previous : 'All';
  select.value = keep;
  return keep;
}

/* Aggregate product rows into category totals (sum quantity + peso). */
function aggregateByCategory(rows) {
  const map = {};
  for (const row of rows) {
    const cat = row.category || 'Uncategorized';
    if (!map[cat]) map[cat] = { product_name: cat, quantity: 0, peso: 0, category: cat };
    map[cat].quantity += Number(row.quantity || 0);
    map[cat].peso += Number(row.peso || 0);
  }
  return Object.values(map);
}

/* Toggle the category dropdown visibility. */
function syncCategoryDropdown() {
  const wrap = el('dashCategorySelect') && el('dashCategorySelect').closest('.dash-select-wrap');
  if (wrap) wrap.style.display = productState.viewType === 'categories' ? 'none' : '';
}

/* Update the panel title based on view type. */
function syncPanelTitle() {
  const title = document.querySelector('#productChartWrap')?.closest('.dash-panel')?.querySelector('.dash-panel-title');
  if (title) title.textContent = productState.viewType === 'categories' ? 'Top Selling Categories' : 'Top Selling Products';
}

/* Re-render the product chart from the current range/category/metric state. */
function renderProductChart(p = palette) {
  const scope = productData[PRODUCT_KEYS[productState.range]] || { All: [] };
  let rows;
  if (productState.viewType === 'categories') {
    rows = sortTopRows(aggregateByCategory(scope.All || []), productState.metric);
  } else {
    rows = sortTopRows(scope[productState.category] || scope.All || [], productState.metric);
  }
  createProductChart(rows, p, productState.metric);
  syncCategoryDropdown();
  syncPanelTitle();
}

/* ---- Date filter wiring (no requests; local data swap) --------------------- */
function currentValue(name) {
  const checked = document.querySelector(`input[name="${name}"]:checked`);
  return checked ? checked.value : null;
}

function syncRangeCaptions() {
  const productCaption = el('dashProductRange');
  if (!productCaption) return;
  if (globalRange) {
    productCaption.textContent = formatRangeCaption(globalRange.start, globalRange.end);
    return;
  }
  const productValue = currentValue('productView');
  productCaption.textContent = RANGE_CAPTION.product[productValue] || '';
}

/* Sales caption: preset labels until a custom range replaces them with the
   actual dates (e.g. "Aug 5 – Aug 20"). */
function syncSalesCaption() {
  const caption = el('dashSalesRange');
  if (!caption) return '';
  if (salesCustom) {
    caption.textContent = formatRangeCaption(salesCustom.start, salesCustom.end);
  } else {
    caption.textContent = RANGE_CAPTION.sales[currentValue('salesView')] || '';
  }
  return caption.textContent;
}

/* ---- Global range: delta markup, KPI / table / summary re-renders -------- */
function deltaMarkup(current, prior, money) {
  if (current === 0 && prior === 0) {
    return { html: 'No activity yet', cls: 'dash-kpi-delta--flat', aria: '' };
  }
  if (prior === 0) {
    return { html: 'No prior period', cls: 'dash-kpi-delta--flat', aria: '' };
  }
  const format = (value) => (money ? formatMoney(value) : String(Math.round(value)));
  if (current === prior) {
    return {
      html: `Same as prior period (${format(prior)})`,
      cls: 'dash-kpi-delta--flat',
      aria: `Same as prior period (${format(prior)})`,
    };
  }
  const up = current > prior;
  return {
    html: `${iconSvg(up ? 'arrow-up' : 'arrow-down')} vs prior period (${format(prior)})`,
    cls: up ? 'dash-kpi-delta--up' : 'dash-kpi-delta--down',
    aria: `${up ? 'Up' : 'Down'} ${format(Math.abs(current - prior))} vs prior period (${format(prior)})`,
  };
}

function renderKpis(k) {
  animateCount(el('kpiOrdersValue'), k.orders || 0, { duration: 1800, format: animInt });
  animateCount(el('kpiNetValue'), k.net || 0, { duration: 2200, format: animMoney });
  animateCount(el('kpiGrossValue'), k.gross || 0, { duration: 2200, format: animMoney });
  animateCount(el('kpiItemsValue'), k.items || 0, { duration: 1800, format: animInt });
  [
    ['kpiOrdersDelta', k.orders, k.prior_orders, false],
    ['kpiNetDelta', k.net, k.prior_net, true],
    ['kpiGrossDelta', k.gross, k.prior_gross, true],
    ['kpiItemsDelta', k.items, k.prior_items, false],
  ].forEach(([id, current, prior, money]) => {
    const node = el(id);
    if (!node) return;
    const delta = deltaMarkup(current, prior, money);
    node.className = `dash-kpi-delta${delta.cls ? ` ${delta.cls}` : ''}`;
    node.innerHTML = delta.html;
    if (delta.aria) node.setAttribute('aria-label', delta.aria);
    else node.removeAttribute('aria-label');
  });
  const section = el('dashKpiSection');
  if (section) section.setAttribute('aria-label', 'Selected range key numbers');
}

function statusChip(status) {
  if (status === 'completed') return `<span class="dash-chip dash-chip--completed">${iconSvg('check')}Completed</span>`;
  if (status === 'pending') return `<span class="dash-chip dash-chip--pending">${iconSvg('clock')}Pending</span>`;
  return `<span class="dash-chip dash-chip--cancelled">${iconSvg('x')}Cancelled</span>`;
}

function renderTopOrders(rows) {
  const body = el('dashTopOrdersBody');
  if (!body) return;
  body.textContent = '';
  if (!Array.isArray(rows) || rows.length === 0) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 6;
    td.className = 'dash-table-empty';
    td.innerHTML = '<div class="dash-empty dash-empty--inline"><p>No orders in this period</p></div>';
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const order of rows) {
    const tr = document.createElement('tr');
    tr.innerHTML = [
      `<td><span class="dash-order-no">${escapeHtml(order.order_no)}</span></td>`,
      `<td><strong class="dash-customer">${escapeHtml(order.customer_name)}</strong></td>`,
      `<td>${order.order_type === 'dinein'
        ? `<span class="dash-chip dash-chip--primary">${iconSvg('table')}Dine-in</span>`
        : `<span class="dash-chip dash-chip--muted">${iconSvg('cart')}Takeout</span>`}</td>`,
      `<td>${statusChip(order.status)}</td>`,
      `<td class="dash-amount">${formatMoney(order.total)}</td>`,
      `<td class="dash-time">${formatTime(order.timestamp)}</td>`,
    ].join('');
    body.appendChild(tr);
  }
}

function renderSummary(s) {
  const setText = (id, text) => {
    const node = el(id);
    if (node) node.textContent = text;
  };
  setText('dashSummaryAvg', formatMoney(s.avg_order_value));
  setText('dashSummarySalesPerOrder', formatMoney(s.sales_per_order));
  setText('dashSummaryItemsPerOrder', (Number(s.items_per_order) || 0).toFixed(1));
  setText('dashSummaryCustomers', String(s.customers));
  setText('dashSummaryReturning', `${s.returning} (${s.returning_pct}%)`);
}

/* Snapshot the pristine server-rendered DOM and initial dataset so Reset can restore it. */
function captureInitialView() {
  initialView = {
    salesData: salesData && Object.keys(salesData).length ? JSON.parse(JSON.stringify(salesData)) : (initialView?.salesData || {}),
    productData: productData && Object.keys(productData).length ? JSON.parse(JSON.stringify(productData)) : (initialView?.productData || {}),
    kpiSectionAria: el('dashKpiSection')?.getAttribute('aria-label') || '',
    values: ['kpiOrdersValue', 'kpiNetValue', 'kpiGrossValue', 'kpiItemsValue']
      .map((id) => ({ id, text: el(id)?.textContent ?? '' })),
    deltas: ['kpiOrdersDelta', 'kpiNetDelta', 'kpiGrossDelta', 'kpiItemsDelta']
      .map((id) => {
        const node = el(id);
        return {
          id,
          html: node?.innerHTML ?? '',
          cls: node?.className ?? '',
          aria: node?.getAttribute('aria-label') ?? '',
        };
      }),
    topOrdersHTML: el('dashTopOrdersBody')?.innerHTML ?? '',
    topOrdersTitle: el('dashTopOrdersTitle')?.textContent ?? '',
    topOrdersSub: el('dashTopOrdersSub')?.textContent ?? '',
    summaryTitle: el('dashSummaryTitle')?.textContent ?? '',
    summarySub: el('dashSummarySub')?.textContent ?? '',
    summary: ['dashSummaryAvg', 'dashSummarySalesPerOrder', 'dashSummaryItemsPerOrder',
      'dashSummaryCustomers', 'dashSummaryReturning']
      .map((id) => ({ id, text: el(id)?.textContent ?? '' })),
  };
}

function syncResetButton() {
  const resetBtn = el('dashRangeReset');
  if (resetBtn) {
    const isCustom = !!globalRange;
    resetBtn.disabled = !isCustom;
    resetBtn.style.opacity = isCustom ? '1' : '0.4';
    resetBtn.style.cursor = isCustom ? 'pointer' : 'default';
  }
}

function resetRange() {
  if (!globalRange || !initialView) return;
  globalRange = null;
  salesCustom = null;
  if (initialView.salesData && Object.keys(initialView.salesData).length) {
    salesData = JSON.parse(JSON.stringify(initialView.salesData));
  } else {
    salesData = readJson('dashSalesData');
  }
  if (initialView.productData && Object.keys(initialView.productData).length) {
    productData = JSON.parse(JSON.stringify(initialView.productData));
  } else {
    productData = readJson('dashProductData');
  }
  productState.range = currentValue('productView') || 'month';
  productState.category = populateCategorySelect(productState.range);
  markPreset(null);

  const kpiSection = el('dashKpiSection');
  if (kpiSection) kpiSection.setAttribute('aria-label', initialView.kpiSectionAria);
  initialView.values.forEach(({ id, text }) => {
    const node = el(id);
    if (node) node.textContent = text;
  });
  initialView.deltas.forEach(({ id, html, cls, aria }) => {
    const node = el(id);
    if (!node) return;
    node.className = cls;
    node.innerHTML = html;
    if (aria) node.setAttribute('aria-label', aria);
    else node.removeAttribute('aria-label');
  });
  const strip = el('dashPeriodStrip');
  if (strip) strip.hidden = false;
  const scopeSeg = el('dashProductScopeSeg');
  if (scopeSeg) scopeSeg.hidden = false;
  const body = el('dashTopOrdersBody');
  if (body) body.innerHTML = initialView.topOrdersHTML;
  const title = el('dashTopOrdersTitle');
  if (title) title.textContent = initialView.topOrdersTitle;
  const sub = el('dashTopOrdersSub');
  if (sub) sub.textContent = initialView.topOrdersSub;
  const summaryTitle = el('dashSummaryTitle');
  if (summaryTitle) summaryTitle.textContent = initialView.summaryTitle;
  const summarySub = el('dashSummarySub');
  if (summarySub) summarySub.textContent = initialView.summarySub;
  initialView.summary.forEach(({ id, text }) => {
    const node = el(id);
    if (node) node.textContent = text;
  });

  /* Reset DRP to default (this month). */
  const [defStart, defEnd] = presetDates('this-month');
  drpStartISO = defStart;
  drpEndISO = defEnd;
  drpPicking = false;
  drpHoverISO = null;
  updateDRPLabel();
  updateSidebarPresets('this-month');
  closeDRP();

  syncRangeCaptions();
  syncSalesCaption();
  createSalesChart(salesData[SALES_KEYS[currentValue('salesView') || 'daily']] || [], palette);
  renderProductChart();
  markPreset('this-month');
  syncResetButton();
}

async function applyGlobalRange(startISO, endISO, presetKey = null) {
  const caption = el('dashSalesRange');
  if (!startISO || !endISO) {
    if (caption) caption.textContent = 'Pick a start and end date';
    return;
  }
  if (startISO > endISO) {
    if (caption) caption.textContent = 'Start must be before end';
    return;
  }
  if (caption) caption.textContent = 'Loading range…';
  try {
    const response = await fetch(
      `/dashboard/range?start=${encodeURIComponent(startISO)}&end=${encodeURIComponent(endISO)}`,
      { credentials: 'same-origin' }
    );
    const data = await response.json();
    if (disposed) return;
    if (!data || data.success !== true) {
      throw new Error((data && data.error) || 'Range rejected');
    }
    if (!initialView) captureInitialView();

    globalRange = { start: startISO, end: endISO, presetKey };
    salesData = data.series || { daily: [], weekly: [], monthly: [] };
    salesCustom = { start: startISO, end: endISO };
    markPreset(presetKey);
    renderKpis(data.kpis || {});
    const strip = el('dashPeriodStrip');
    if (strip) strip.hidden = false;
    const scopeSeg = el('dashProductScopeSeg');
    if (scopeSeg) scopeSeg.hidden = true;
    productData = { range: data.products || { All: [], _categories: [] } };
    productState.range = 'range';
    productState.category = populateCategorySelect('range');
    syncSalesCaption();
    syncRangeCaptions();
    createSalesChart(salesData[SALES_KEYS[currentValue('salesView') || 'daily']] || [], palette);
    renderProductChart();
    renderTopOrders(data.top_orders || []);
    renderSummary(data.summary || {});
    const title = el('dashTopOrdersTitle');
    if (title) title.textContent = 'Top Orders';
    const sub = el('dashTopOrdersSub');
    if (sub) sub.textContent = formatRangeCaption(startISO, endISO);
    const summaryTitle = el('dashSummaryTitle');
    if (summaryTitle) {
      if (presetKey === 'today') summaryTitle.textContent = 'Today’s Summary';
      else if (presetKey === 'yesterday') summaryTitle.textContent = 'Yesterday’s Summary';
      else if (presetKey === 'this-month') summaryTitle.textContent = 'This Month’s Summary';
      else if (presetKey === 'last-month') summaryTitle.textContent = 'Last Month’s Summary';
      else summaryTitle.textContent = 'Operational Summary';
    }
    const summarySub = el('dashSummarySub');
    if (summarySub) summarySub.textContent = formatRangeCaption(startISO, endISO);
    syncResetButton();
  } catch (error) {
    console.error('[dashboard] range fetch failed', error);
    if (caption) caption.textContent = 'Couldn\u2019t load that range';
  }
}

/* ---- Global range bar: presets + From/To date inputs ---------------------- */
function toISO(day) {
  const y = day.getFullYear();
  const m = String(day.getMonth() + 1).padStart(2, '0');
  const d = String(day.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function presetDates(key) {
  const today = new Date();
  let start, end = today;
  if (key === 'today') {
    start = new Date(today);
  } else if (key === 'yesterday') {
    start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    end = new Date(start);
  } else if (key === 'last-7-days' || key === '7d') {
    start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6);
  } else if (key === 'last-30-days' || key === '30d') {
    start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 29);
  } else if (key === 'this-month') {
    start = new Date(today.getFullYear(), today.getMonth(), 1);
  } else if (key === 'last-month') {
    start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
    end = new Date(today.getFullYear(), today.getMonth(), 0);
  } else {
    start = new Date(today.getFullYear(), today.getMonth(), 1);
  }
  return [toISO(start), toISO(end)];
}

function formatRangeCaption(startISO, endISO) {
  const start = new Date(`${startISO}T00:00:00`);
  const end = new Date(`${endISO}T00:00:00`);
  const sameYear = start.getFullYear() === end.getFullYear();
  const currentYear = new Date().getFullYear();
  const day = { month: 'short', day: 'numeric' };
  const withYear = { ...day, year: 'numeric' };
  return `${start.toLocaleDateString([], sameYear ? day : withYear)} – ${end.toLocaleDateString([], sameYear && end.getFullYear() === currentYear ? day : withYear)}`;
}

function formatUSDate(isoStr) {
  if (!isoStr) return '';
  const [y, m, d] = isoStr.split('-');
  return `${m}/${d}/${y}`;
}

function markPreset(key) {
  document.querySelectorAll('.dash-preset[data-preset]').forEach((btn) => {
    btn.setAttribute('aria-pressed', String(btn.dataset.preset === key));
  });
}

/* ---- Custom Date Range Picker -------------------------------------------- */
const DRP_MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
const todayISO = () => toISO(new Date());

function closeDRP() {
  drpOpen = false;
  const popup = el('dashDRPPopup');
  if (popup) popup.hidden = true;
  const trigger = el('dashDRPTrigger');
  if (trigger) trigger.setAttribute('aria-expanded', 'false');
  const drp = el('dashDRP');
  if (drp) drp.removeAttribute('open');
}

function openDRP() {
  drpOpen = true;
  const popup = el('dashDRPPopup');
  if (popup) popup.hidden = false;
  const trigger = el('dashDRPTrigger');
  if (trigger) trigger.setAttribute('aria-expanded', 'true');
  const drp = el('dashDRP');
  if (drp) drp.setAttribute('open', '');
  /* Navigate calendar to the current start selection. */
  if (drpStartISO) {
    const d = new Date(`${drpStartISO}T00:00:00`);
    drpCalYear = d.getFullYear();
    drpCalMonth = d.getMonth();
  } else {
    const now = new Date();
    drpCalYear = now.getFullYear();
    drpCalMonth = now.getMonth();
  }
  updateSidebarPresets();
  renderDRPCalendars();
  updateDRPHint();
}

function updateDRPLabel() {
  const label = el('dashDRPLabel');
  if (!label) return;
  if (drpStartISO && drpEndISO) {
    label.textContent = formatRangeCaption(drpStartISO, drpEndISO);
  } else if (drpStartISO) {
    label.textContent = formatRangeCaption(drpStartISO, drpStartISO);
  } else {
    label.textContent = 'This month';
  }
}

function updateDRPHint() {
  const hint = el('dashDRPHint');
  if (!hint) return;
  if (drpPicking && drpStartISO && drpHoverISO) {
    const s = drpHoverISO < drpStartISO ? drpHoverISO : drpStartISO;
    const e = drpHoverISO < drpStartISO ? drpStartISO : drpHoverISO;
    hint.textContent = `${formatUSDate(s)} - ${formatUSDate(e)}`;
  } else if (drpStartISO && drpEndISO) {
    hint.textContent = `${formatUSDate(drpStartISO)} - ${formatUSDate(drpEndISO)}`;
  } else if (drpStartISO) {
    hint.textContent = `${formatUSDate(drpStartISO)} - Select end date`;
  } else {
    hint.textContent = 'Select start date';
  }
}

function drpISO(y, m, d) {
  return `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

function buildMonthDaysHTML(year, month, effStart, effEnd) {
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const today = todayISO();
  const maxDate = today;

  const prevDays = new Date(year, month, 0).getDate();
  let html = '';
  for (let i = firstDay - 1; i >= 0; i--) {
    const day = prevDays - i;
    const prevMonth = month - 1 < 0 ? 11 : month - 1;
    const prevYear = month - 1 < 0 ? year - 1 : year;
    const iso = drpISO(prevYear, prevMonth, day);
    html += `<button type="button" class="dash-drp-day dash-drp-day--other-month" data-iso="${iso}" disabled>${day}</button>`;
  }

  for (let d = 1; d <= daysInMonth; d++) {
    const iso = drpISO(year, month, d);
    const classes = ['dash-drp-day'];
    if (iso === today) classes.push('dash-drp-day--today');
    if (iso > maxDate) classes.push('dash-drp-day--disabled');

    const isStart = effStart && iso === effStart;
    const isEnd = effEnd && iso === effEnd;
    const isInRange = effStart && effEnd && iso > effStart && iso < effEnd;

    if (isStart) classes.push('dash-drp-day--selected', 'dash-drp-day--range-start');
    if (isEnd) classes.push('dash-drp-day--selected', 'dash-drp-day--range-end');
    if (isInRange) classes.push('dash-drp-day--inrange');

    const disabled = iso > maxDate ? ' disabled' : '';
    html += `<button type="button" class="${classes.join(' ')}" data-iso="${iso}"${disabled}>${d}</button>`;
  }

  const totalCells = firstDay + daysInMonth;
  const remainder = totalCells % 7;
  if (remainder > 0) {
    const fill = 7 - remainder;
    const nextMonth = month + 1 > 11 ? 0 : month + 1;
    const nextYear = month + 1 > 11 ? year + 1 : year;
    for (let i = 1; i <= fill; i++) {
      const iso = drpISO(nextYear, nextMonth, i);
      html += `<button type="button" class="dash-drp-day dash-drp-day--other-month" data-iso="${iso}" disabled>${i}</button>`;
    }
  }

  return html;
}

function renderDRPCalendars() {
  const monthLeftEl = el('dashDRPMonthLeft');
  const monthRightEl = el('dashDRPMonthRight');
  const daysLeftEl = el('dashDRPDaysLeft');
  const daysRightEl = el('dashDRPDaysRight');
  if (!monthLeftEl || !monthRightEl || !daysLeftEl || !daysRightEl) return;

  const leftYear = drpCalYear;
  const leftMonth = drpCalMonth;
  monthLeftEl.textContent = `${DRP_MONTHS[leftMonth]} ${leftYear}`;

  let rightYear = leftYear;
  let rightMonth = leftMonth + 1;
  if (rightMonth > 11) {
    rightMonth = 0;
    rightYear++;
  }
  monthRightEl.textContent = `${DRP_MONTHS[rightMonth]} ${rightYear}`;

  let effStart = drpStartISO;
  let effEnd = drpEndISO;
  if (drpPicking && drpStartISO && drpHoverISO) {
    if (drpHoverISO >= drpStartISO) {
      effStart = drpStartISO;
      effEnd = drpHoverISO;
    } else {
      effStart = drpHoverISO;
      effEnd = drpStartISO;
    }
  }

  daysLeftEl.innerHTML = buildMonthDaysHTML(leftYear, leftMonth, effStart, effEnd);
  daysRightEl.innerHTML = buildMonthDaysHTML(rightYear, rightMonth, effStart, effEnd);
}

function updateSidebarPresets(activeKey = null) {
  document.querySelectorAll('.dash-drp-preset').forEach((btn) => {
    const key = btn.dataset.preset;
    let isActive = false;
    if (activeKey) {
      isActive = key === activeKey;
    } else if (drpStartISO && drpEndISO) {
      const [pStart, pEnd] = presetDates(key);
      isActive = drpStartISO === pStart && drpEndISO === pEnd;
    }
    btn.classList.toggle('is-active', isActive);
    btn.setAttribute('aria-selected', String(isActive));
  });
}

function wireDateRangePicker() {
  const trigger = el('dashDRPTrigger');
  const popup = el('dashDRPPopup');
  const prevBtn = el('dashDRPPrev');
  const nextBtn = el('dashDRPNext');
  const applyBtn = el('dashDRPApply');
  const cancelBtn = el('dashDRPCancel');
  const resetBtn = el('dashRangeReset');
  const sidebar = el('dashDRPSidebar');
  const calendarsEl = el('dashDRPCalendars');
  if (!trigger || !popup) return;

  /* Default: this month. */
  const [defStart, defEnd] = presetDates('this-month');
  drpStartISO = defStart;
  drpEndISO = defEnd;
  drpPicking = false;
  drpHoverISO = null;
  updateDRPLabel();
  updateSidebarPresets('this-month');

  /* Toggle open/close. */
  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    if (drpOpen) {
      closeDRP();
    } else {
      openDRP();
    }
  });

  /* Prevent clicks inside popup from closing the picker */
  popup.addEventListener('click', (e) => {
    e.stopPropagation();
  });

  /* Close on outside click. */
  document.addEventListener('click', (e) => {
    const drp = el('dashDRP');
    if (!drpOpen || !drp) return;
    const path = e.composedPath ? e.composedPath() : [];
    if (path.length > 0) {
      if (!path.includes(drp)) {
        closeDRP();
      }
    } else if (!drp.contains(e.target) && e.target.isConnected) {
      closeDRP();
    }
  });

  /* Month navigation. */
  if (prevBtn) {
    prevBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      drpCalMonth--;
      if (drpCalMonth < 0) { drpCalMonth = 11; drpCalYear--; }
      renderDRPCalendars();
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      drpCalMonth++;
      if (drpCalMonth > 11) { drpCalMonth = 0; drpCalYear++; }
      renderDRPCalendars();
    });
  }

  /* Sidebar preset clicks. */
  if (sidebar) {
    sidebar.addEventListener('click', (e) => {
      e.stopPropagation();
      const btn = e.target.closest('.dash-drp-preset');
      if (!btn) return;
      const key = btn.dataset.preset;
      if (!key) return;

      const [start, end] = presetDates(key);
      drpStartISO = start;
      drpEndISO = end;
      drpPicking = false;
      drpHoverISO = null;

      const startDateObj = new Date(`${start}T00:00:00`);
      drpCalYear = startDateObj.getFullYear();
      drpCalMonth = startDateObj.getMonth();

      updateSidebarPresets(key);
      renderDRPCalendars();
      updateDRPHint();
    });
  }

  /* Hover range slide delegation */
  if (calendarsEl) {
    calendarsEl.addEventListener('mouseover', (e) => {
      if (!drpPicking || !drpStartISO) return;
      const btn = e.target.closest('.dash-drp-day:not(.dash-drp-day--disabled)');
      if (!btn) return;
      const iso = btn.dataset.iso;
      if (iso && iso !== drpHoverISO) {
        drpHoverISO = iso;
        renderDRPCalendars();
        updateDRPHint();
      }
    });

    calendarsEl.addEventListener('mouseleave', () => {
      if (drpPicking && drpHoverISO) {
        drpHoverISO = null;
        renderDRPCalendars();
        updateDRPHint();
      }
    });

    /* Day click delegation */
    calendarsEl.addEventListener('click', (e) => {
      e.stopPropagation();
      const btn = e.target.closest('.dash-drp-day:not(.dash-drp-day--disabled)');
      if (!btn) return;
      const iso = btn.dataset.iso;
      if (!iso) return;

      if (!drpPicking) {
        /* First click: set start */
        drpStartISO = iso;
        drpEndISO = null;
        drpHoverISO = null;
        drpPicking = true;
      } else {
        /* Second click: set end */
        if (iso < drpStartISO) {
          drpEndISO = drpStartISO;
          drpStartISO = iso;
        } else {
          drpEndISO = iso;
        }
        drpPicking = false;
        drpHoverISO = null;
      }
      updateSidebarPresets();
      renderDRPCalendars();
      updateDRPHint();
    });
  }

  /* Apply button in popup. */
  if (applyBtn) {
    applyBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const finalEnd = drpEndISO || drpStartISO;
      if (drpStartISO && finalEnd) {
        drpEndISO = finalEnd;
        drpPicking = false;
        drpHoverISO = null;
        updateDRPLabel();
        closeDRP();
        applyGlobalRange(drpStartISO, finalEnd);
      }
    });
  }

  /* Cancel — close without applying. */
  if (cancelBtn) {
    cancelBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      drpPicking = false;
      drpHoverISO = null;
      closeDRP();
    });
  }

  /* Reset button — restores default MTD view. */
  if (resetBtn) {
    resetBtn.addEventListener('click', resetRange);
  }
}

function wireFilters(p) {
  document.querySelectorAll('input[name="salesView"]').forEach((input) => {
    input.addEventListener('change', () => {
      if (!input.checked) return;
      syncSalesCaption();
      createSalesChart(salesData[SALES_KEYS[input.value]] || [], p);
    });
  });
  document.querySelectorAll('input[name="productView"]').forEach((input) => {
    input.addEventListener('change', () => {
      if (!input.checked || globalRange) return;
      productState.range = input.value;
      productState.category = populateCategorySelect(input.value);
      syncRangeCaptions();
      renderProductChart();
    });
  });
  document.querySelectorAll('input[name="productMetric"]').forEach((input) => {
    input.addEventListener('change', () => {
      if (!input.checked) return;
      productState.metric = input.value;
      renderProductChart();
    });
  });
  const select = el('dashCategorySelect');
  if (select) {
    select.addEventListener('change', () => {
      productState.category = select.value;
      renderProductChart();
    });
  }
  document.querySelectorAll('input[name="productType"]').forEach((input) => {
    input.addEventListener('change', () => {
      if (!input.checked) return;
      productState.viewType = input.value;
      renderProductChart();
    });
  });
}

/* ---- Today's date line (server context has no date; real clock data) ------- */
function setDateLine() {
  const node = el('dashDate');
  if (!node) return;
  node.textContent = new Date().toLocaleDateString([], {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

/* ---- Count-up animation ------------------------------------------------- */
animFrames = [];

/* Ease-out cubic for a satisfying deceleration. */
function easeOutCubic(t) {
  return 1 - Math.pow(1 - t, 3);
}

/*
 * animateCount(el, target, opts)
 *   Counts from the element's current text (parsed as number) to `target`
 *   over `duration` ms. `format(value)` returns the display string.
 *   Skips animation when reduced-motion is preferred or target === 0.
 */
function animateCount(node, target, { duration = 2000, format = (v) => String(v) } = {}) {
  if (!node) return;
  if (palette && palette.reduceMotion) {
    node.textContent = format(target);
    return;
  }
  const startVal = parseFloat(node.textContent.replace(/[^\d.-]/g, '')) || 0;
  if (Math.abs(startVal - target) < 0.01) {
    node.textContent = format(target);
    return;
  }
  const startTime = performance.now();

  function step(now) {
    if (disposed) return;
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const eased = easeOutCubic(progress);
    const current = startVal + (target - startVal) * eased;
    node.textContent = format(current);
    if (progress < 1) {
      const id = requestAnimationFrame(step);
      animFrames.push(id);
    }
  }
  const id = requestAnimationFrame(step);
  animFrames.push(id);
}

function cancelAnimations() {
  for (const id of animFrames) cancelAnimationFrame(id);
  animFrames = [];
}

/* Format helpers for animateCount targets. */
function animMoney(v) { return formatMoney(v); }
function animInt(v) { return String(Math.round(v)); }
function animDec1(v) { return v.toFixed(1); }

/* ---- Async overview fetch (fast-load pattern) ---------------------------- */
async function fetchOverview() {
  if (disposed) return;
  try {
    const response = await fetch('/dashboard/overview', { credentials: 'same-origin' });
    const data = await response.json();
    if (disposed) return;
    if (!data || data.success !== true) return;
    applyOverviewData(data);
  } catch (err) {
    console.error('[dashboard] overview fetch failed', err);
  }
}

function applyOverviewData(data) {
  const k = data.kpi || {};
  const p = palette;

  /* --- Animate KPI values --- */
  animateCount(el('kpiOrdersValue'), k.orders || 0, { duration: 1800, format: animInt });
  animateCount(el('kpiNetValue'), k.net || 0, { duration: 2200, format: animMoney });
  animateCount(el('kpiGrossValue'), k.gross || 0, { duration: 2200, format: animMoney });
  animateCount(el('kpiItemsValue'), k.items || 0, { duration: 1800, format: animInt });

  /* KPI deltas */
  [
    ['kpiOrdersDelta', k.orders, k.prior_orders, false],
    ['kpiNetDelta', k.net, k.prior_net, true],
    ['kpiGrossDelta', k.gross, k.prior_gross, true],
    ['kpiItemsDelta', k.items, k.prior_items, false],
  ].forEach(([id, current, prior, money]) => {
    const node = el(id);
    if (!node) return;
    const delta = deltaMarkup(current, prior, money);
    node.className = `dash-kpi-delta${delta.cls ? ` ${delta.cls}` : ''}`;
    node.innerHTML = delta.html;
    if (delta.aria) node.setAttribute('aria-label', delta.aria);
    else node.removeAttribute('aria-label');
  });

  /* --- Period strip --- */
  const periods = data.periods || {};
  const mtd = periods.mtd || { net: 0, prior: 0 };
  const ytd = periods.ytd || { net: 0, prior: 0 };
  const periodValues = document.querySelectorAll('.dash-period-value');
  if (periodValues[0]) animateCount(periodValues[0], mtd.net || 0, { duration: 2400, format: animMoney });
  if (periodValues[1]) animateCount(periodValues[1], ytd.net || 0, { duration: 2400, format: animMoney });

  /* Period deltas */
  const periodDeltas = document.querySelectorAll('.dash-period-right .dash-kpi-delta');
  [mtd, ytd].forEach((period, i) => {
    const node = periodDeltas[i];
    if (!node) return;
    const d = deltaMarkup(period.net || 0, period.prior || 0, true);
    node.className = `dash-kpi-delta${d.cls ? ` ${d.cls}` : ''}`;
    node.innerHTML = d.html;
  });

  /* --- Charts --- */
  salesData = data.series || { daily: [], weekly: [], monthly: [] };
  productData = data.products || {};
  productState.range = 'month';
  productState.category = populateCategorySelect('month');
  createSalesChart(salesData[SALES_KEYS[currentValue('salesView') || 'daily']] || [], p);
  renderProductChart(p);
  syncSalesCaption();
  syncRangeCaptions();

  /* --- Top orders --- */
  renderTopOrders(data.top_orders || []);
  const title = el('dashTopOrdersTitle');
  if (title) title.textContent = 'Top Orders';
  const sub = el('dashTopOrdersSub');
  if (sub) sub.textContent = 'This month';

  /* --- Summary --- */
  renderSummary(data.summary || {});
  const summaryTitle = el('dashSummaryTitle');
  if (summaryTitle) summaryTitle.textContent = 'This Month’s Summary';
  const summarySub = el('dashSummarySub');
  if (summarySub) summarySub.textContent = 'This month';

  /* Re-snapshot after data loads so Reset works correctly. */
  captureInitialView();
}

export default {
  mount() {
    setDateLine();
    salesData = readJson('dashSalesData');
    productData = readJson('dashProductData');
    salesCustom = null;
    globalRange = null;
    initialView = null;
    disposed = false;
    animFrames = [];

    palette = readPalette();
    const p = palette;

    const salesValue = currentValue('salesView') || 'daily';
    productState.range = currentValue('productView') || 'month';
    productState.metric = currentValue('productMetric') || 'qty';
    productState.category = populateCategorySelect(productState.range);
    captureInitialView();
    syncRangeCaptions();
    createSalesChart(salesData[SALES_KEYS[salesValue]] || [], p);
    renderProductChart();
    wireFilters(p);
    wireDateRangePicker();
    syncResetButton();

    /* Fast-load: fetch real data async and animate numbers in. */
    fetchOverview();

    /* Charts resolve tokens at init; a theme flip re-inits them. */
    themeObserver = new MutationObserver(() => {
      const next = readPalette();
      palette = next;
      createSalesChart(salesData[SALES_KEYS[currentValue('salesView') || 'daily']] || [], next);
      renderProductChart(next);
    });
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
  },

  destroy() {
    disposed = true;
    cancelAnimations();
    if (themeObserver) {
      themeObserver.disconnect();
      themeObserver = null;
    }
    if (salesChart) {
      salesChart.destroy();
      salesChart = null;
    }
    if (productChart) {
      productChart.destroy();
      productChart = null;
    }
    palette = null;
    productData = {};
    salesData = {};
    salesCustom = null;
    globalRange = null;
    initialView = null;
    drpOpen = false;
    drpStartISO = null;
    drpEndISO = null;
    drpPicking = false;
  },
};
