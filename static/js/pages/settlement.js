/* =============================================================================
   pages/settlement.js — settlement view (templates/settlement.html)
   -----------------------------------------------------------------------------
   spa.js contract: default export { mount }. Ports the legacy inline
   controller from templates/settlement.html + static/js/settlement.js 1:1
   (see .ulpi/design/pos-cashier-core.md, "Settlement"): the payment matrix
   (cash/card/gift_check/cheque), the 8-type discount modal with VAT
   exemption logic (senior/PWD/solo_parent VAT-exempt, athlete/MOV
   VAT-addback, regular percentage, OTH peso amount), the mixed discount
   per-PAX share calculation, the cash keyboard with presets, the card
   swipe reader, the presence lock (/order_presence + sendBeacon release),
   PAX field admin-auth edit, bill out, manual SI toggle, localStorage
   drafts, and the form submission with printer guard. All monetary
   calculations maintain byte-level behavioral parity with V1 settlement.js
   calculateOrderDiscount() (golden diff 0 against V1 matrix per spec).

   Discount stacking guard (RMC 71-2022 / Joint MC 1-2022):
   Promotional discounts (Regular, OTH) and statutory SC/PWD discounts
   (Senior, PWD, Athlete, Medal of Valor, Solo Parent) cannot be stacked.
   The modal blocks activation of a second type when the other is already
   selected, and shows a compliance error modal with the legal reference.
   ========================================================================== */
'use strict';

import { api, ApiError, getCsrfToken } from '../core/api.js';
import modal from '../core/modal.js';
import { notify } from '../core/toast.js';
import { guardPrinter } from '../core/guards.js';
import { openKeyboard, formatNumberWithCommas } from '../core/keyboard.js';

const PRESENCE_POLL_MS = 10000;
const RELEASE_PATH = (id) => `/release_order_lock/${id}`;

/* ---- Module state (seeded per mount) -------------------------------------- */
let ORDER_ID = '';
let ORDER_NO = '';
let ORDER_TOTAL = 0;
let CUSTOMER_NAME = '';
let ORDER_TIMESTAMP = '';
let CALCULATED_COVERS = 1;
let selectedDiscounts = {};
let lastConfirmedPaymentMethod = 'cash';
let pendingSettlementPaymentMethod = null;
let isPaymentSwitchPending = false;
let presenceInterval = null;
let lockConflictHandled = false;
let overlaySeq = 0;
let openOverlays = [];
let rootEl = null;
let destroyFns = [];

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function money(n) {
  return `\u20B1${Number(n || 0).toFixed(2)}`;
}

/* ---- Global helpers (V1 parity) ------------------------------------------- */
function getCurrentPax() {
  const paxField = document.getElementById('totalNoPax');
  if (paxField && paxField.value) {
    const val = parseInt(paxField.value, 10);
    if (Number.isFinite(val) && val > 0) return val;
  }
  return CALCULATED_COVERS || 1;
}

function getSettlementBaseTotal() {
  if (Number.isFinite(ORDER_TOTAL) && ORDER_TOTAL > 0) {
    return ORDER_TOTAL;
  }
  return 0;
}

function getDiscountQuantity(discountType) {
  const rawValue = parseInt(selectedDiscounts[discountType], 10);
  if (['senior', 'pwd', 'athlete', 'medal_of_valor', 'solo_parent'].includes(discountType)) {
    return Number.isFinite(rawValue) && rawValue > 0 ? rawValue : 1;
  }
  if (discountType === 'regular') {
    const regularValue = parseFloat(selectedDiscounts[discountType]);
    return Number.isFinite(regularValue) ? regularValue : 10;
  }
  if (discountType === 'oth') {
    const othValue = parseFloat(selectedDiscounts[oth]);
    return Number.isFinite(othValue) ? othValue : 0;
  }
  return Number.isFinite(rawValue) ? rawValue : 0;
}

function getDiscountReceiptLabel(discountType) {
  const labels = {
    senior: 'SC', pwd: 'PWD', athlete: 'NAAC', medal_of_valor: 'MOV',
    solo_parent: 'SOLO', regular: 'REG', oth: 'OTH'
  };
  return labels[discountType] || String(discountType || '').toUpperCase();
}

function getDiscountReceiptPercent(discountType) {
  if (discountType === 'solo_parent') return 10;
  if (['senior', 'pwd', 'athlete', 'medal_of_valor'].includes(discountType)) return 20;
  return 0;
}

function getNumericTextContent(id, fallback = 0) {
  const el = document.getElementById(id);
  if (!el) return fallback;
  const raw = (el.textContent || '').replace(/[^0-9.-]/g, '');
  const parsed = parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function getSettlementTotalWithVat() {
  const el = document.getElementById('totalWithVat');
  if (el) return parseFloat(el.textContent) || getSettlementBaseTotal();
  return getSettlementBaseTotal();
}

function getDiscountCountCap() {
  // Do not hard-cap by current pax input; allow flexible badge editing.
  return 999;
}

function getDisplayedDiscountAmount() {
  const detailedDiscountAmountEl = document.getElementById('discountAmount');
  return parseFloat(detailedDiscountAmountEl?.textContent) || 0;
}

/* ---- LocalStorage draft (V1 parity) --------------------------------------- */
function getSettlementDraftStorageKey() {
  const idPart = ORDER_ID || ORDER_NO || 'default';
  return `settlementDraft:${idPart}`;
}

function saveSettlementDraftToLocalStorage() {
  try {
    if (isPaymentSwitchPending) return;
    const paymentMethodEl = document.querySelector('input[name="payment_method"]:checked');
    const discountBeneficiaries = {};
    document.querySelectorAll('.discount-name-field').forEach((input) => {
      const type = input.dataset.discountType;
      const index = parseInt(input.dataset.index, 10);
      if (!type || !Number.isFinite(index)) return;
      discountBeneficiaries[type] = discountBeneficiaries[type] || {};
      discountBeneficiaries[type][index] = discountBeneficiaries[type][index] || { name: '', id: '' };
      discountBeneficiaries[type][index].name = input.value || '';
    });
    document.querySelectorAll('.discount-id-field').forEach((input) => {
      const type = input.dataset.discountType;
      const index = parseInt(input.dataset.index, 10);
      if (!type || !Number.isFinite(index)) return;
      discountBeneficiaries[type] = discountBeneficiaries[type] || {};
      discountBeneficiaries[type][index] = discountBeneficiaries[type][index] || { name: '', id: '' };
      discountBeneficiaries[type][index].id = input.value || '';
    });

    const draft = {
      selectedDiscounts: selectedDiscounts || {},
      orderDiscount: document.getElementById('orderDiscount')?.value || 'no_discount',
      totalNoPax: document.getElementById('totalNoPax')?.value || '',
      paymentMethod: paymentMethodEl ? paymentMethodEl.value : 'cash',
      cardType: document.getElementById('cardType')?.value || 'gcash',
      cashReceived: document.getElementById('cashReceived')?.value || '',
      giftCheckAmount: document.getElementById('giftCheckAmount')?.value || '',
      giftCheckNumber: document.getElementById('giftCheckNumber')?.value || '',
      chequeAmount: document.getElementById('chequeAmount')?.value || '',
      chequeNumber: document.getElementById('chequeNumber')?.value || '',
      remarks: document.getElementById('remarks')?.value || '',
      discountBeneficiaries
    };
    localStorage.setItem(getSettlementDraftStorageKey(), JSON.stringify(draft));
  } catch (err) {
    console.warn('Failed to save settlement draft:', err);
  }
}

function clearSettlementLocalStorage() {
  try {
    localStorage.removeItem(getSettlementDraftStorageKey());
    localStorage.removeItem('settlementDraft:default');
    localStorage.removeItem('settlementData');
  } catch (err) {
    console.warn('Failed to clear settlement storage:', err);
  }
}

/* ---- Summary card discount breakdown -------------------------------------- */
function updateSummaryBreakdown(opts) {
  const { selectedDiscount, selectedDiscountKeys, isMultipleDiscounts,
    calculatedTotal, totalPax, discountAmount, taxExemptAmount,
    regularDiscountPercent, othDiscountAmount } = opts;

  const container = document.getElementById('receiptDiscountBreakdown');
  if (!container) return;

  const lines = [];
  const spp = totalPax > 0 ? calculatedTotal / totalPax : calculatedTotal;

  if (isMultipleDiscounts) {
    selectedDiscountKeys.forEach(dt => {
      const label = getDiscountReceiptLabel(dt);
      if (dt === 'regular') {
        const pct = parseFloat(selectedDiscounts['regular']) || 0;
        const rMode = selectedDiscounts['regular_mode'] || '%';
        if (rMode === '₱') { lines.push({ label: 'LESS: DISCOUNT (' + label + '):', amount: parseFloat(selectedDiscounts['regular']) || 0 }); }
        else { lines.push({ label: 'LESS: ' + pct + '% DISCOUNT (' + label + '):', amount: spp * (pct / 100) }); }
        return;
      }
      if (dt === 'oth') { lines.push({ label: 'LESS: OTHER DISCOUNT (' + label + '):', amount: parseFloat(selectedDiscounts['oth']) || 0 }); return; }
      const q = getDiscountQuantity(dt);
      const dhp = spp * q;
      const vat = dhp * (12 / 112);
      const pct = getDiscountReceiptPercent(dt);
      const base = dhp / 1.12;
      const dv = base * (pct / 100);
      lines.push({ label: 'LESS: 12% VAT (' + label + '):', amount: vat });
      lines.push({ label: 'LESS: ' + pct + '% DISCOUNT (' + label + '):', amount: dv });
      if (dt === 'athlete' || dt === 'medal_of_valor') lines.push({ label: 'ADD: 12% VAT (' + label + '):', amount: vat, sign: '+' });
    });
  } else if (selectedDiscount === 'senior' || selectedDiscount === 'pwd' || selectedDiscount === 'solo_parent') {
    const label = getDiscountReceiptLabel(selectedDiscount);
    const pct = getDiscountReceiptPercent(selectedDiscount);
    lines.push({ label: 'LESS: 12% VAT:', amount: taxExemptAmount });
    lines.push({ label: 'LESS: ' + pct + '% DISCOUNT (' + label + '):', amount: discountAmount });
  } else if (selectedDiscount === 'athlete' || selectedDiscount === 'medal_of_valor') {
    const label = getDiscountReceiptLabel(selectedDiscount);
    const lv = calculatedTotal * (12 / 112);
    lines.push({ label: 'LESS: 12% VAT:', amount: lv });
    lines.push({ label: 'LESS: 20% DISCOUNT (' + label + '):', amount: discountAmount });
    lines.push({ label: 'ADD: 12% VAT:', amount: lv, sign: '+' });
  } else if (selectedDiscount === 'regular') {
    const pct = parseFloat(selectedDiscounts['regular']) || 0;
    const rMode = selectedDiscounts['regular_mode'] || '%';
    if (rMode === '₱') { lines.push({ label: 'LESS: DISCOUNT:', amount: parseFloat(selectedDiscounts['regular']) || 0 }); }
    else { lines.push({ label: 'LESS: ' + Math.round(pct) + '% DISCOUNT (REG):', amount: discountAmount }); }
  } else if (selectedDiscount === 'oth') {
    lines.push({ label: 'LESS: OTHER DISCOUNT:', amount: othDiscountAmount || discountAmount });
  }

  const visibleLines = lines.filter(l => Math.abs(Number(l.amount) || 0) > 0.004);
  if (visibleLines.length === 0) {
    container.innerHTML = '<div class="stl-line-item"><span class="stl-label" id="receiptDiscountLabel">Less Discount</span><span class="stl-value">-\u20b1<span id="receiptDiscountAmount">0.00</span></span></div>';
    return;
  }
  container.innerHTML = visibleLines.map((line, i) => {
    const sign = line.sign === '+' ? '+' : '-';
    const labelId = i === 0 ? ' id="receiptDiscountLabel"' : '';
    const amountId = i === 0 ? ' id="receiptDiscountAmount"' : '';
    return '<div class="stl-line-item"><span class="stl-label"' + labelId + '>' + line.label + '</span><span class="stl-value">' + sign + '\u20b1<span' + amountId + '>' + Number(line.amount).toFixed(2) + '</span></span></div>';
  }).join('');
}

/* ---- Discount calculation (V1 parity - CRITICAL) -------------------------- */
function calculateOrderDiscount() {
  const selectedDiscountKeys = Object.keys(selectedDiscounts).filter(key => key !== 'no_discount' && key !== 'regular_mode');
  const selectedDiscount = selectedDiscountKeys.length > 0 ? selectedDiscountKeys[0] : 'no_discount';
  const isMultipleDiscounts = selectedDiscountKeys.length > 1;
  
  let discountQuantity = 0;
  let regularDiscountPercent = 0;
  let othDiscountAmount = 0;
  
  if (selectedDiscount === 'regular') {
    regularDiscountPercent = parseFloat(selectedDiscounts['regular']) || 0;
  } else if (selectedDiscount === 'oth') {
    othDiscountAmount = parseFloat(selectedDiscounts['oth']) || 0;
  } else if (['senior', 'pwd', 'athlete', 'medal_of_valor', 'solo_parent'].includes(selectedDiscount)) {
    discountQuantity = getDiscountQuantity(selectedDiscount);
  }
  
  const calculatedTotal = getSettlementBaseTotal();
  const hasGiftCheckProduct = false;
  const calculatedVAT = (calculatedTotal * 0.12 / 1.12);
  const calculatedSubtotal = calculatedTotal - calculatedVAT;
  
  const totalWithVatEl = document.getElementById('totalWithVat');
  const taxExemptAmountEl = document.getElementById('taxExemptAmount');
  const discountAmountEl = document.getElementById('discountAmount');
  
  if (totalWithVatEl) totalWithVatEl.textContent = calculatedTotal.toFixed(2);
  if (taxExemptAmountEl) taxExemptAmountEl.textContent = "0.00";
  if (discountAmountEl) discountAmountEl.textContent = "0.00";
  
  let finalTotal = calculatedTotal;
  let discountAmount = 0;
  let taxExemptAmount = 0;
  let subtotalTaxExempt = 0;
  
  /* HANDLE MULTIPLE DISCOUNTS (Mixed Discounts) */
  if (isMultipleDiscounts && !hasGiftCheckProduct) {
    const totalPax = getCurrentPax();
    const sharePerPerson = calculatedTotal / totalPax;
    let totalDiscount = 0, totalTaxExempt = 0, totalVatRemoved = 0;
    let totalAfterDiscount = 0, totalAddBackVat = 0, totalLess = 0, totalAdd = 0;
    
    selectedDiscountKeys.forEach((discountType) => {
      const quantity = getDiscountQuantity(discountType);
      if (discountType === 'regular') {
        const regMode = selectedDiscounts['regular_mode'] || '%';
        const regularPortion = sharePerPerson * 1;
        const regularValue = parseFloat(selectedDiscounts['regular']) || 0;
        let regularDiscount;
        if (regMode === '₱') {
          regularDiscount = regularValue;
        } else {
          regularDiscount = regularPortion * (regularValue / 100);
        }
        totalDiscount += regularDiscount;
        totalAfterDiscount += (regularPortion - regularDiscount);
        totalLess += regularDiscount;
      } else if (['senior', 'pwd', 'athlete', 'medal_of_valor', 'solo_parent'].includes(discountType)) {
        const discountHolderPortion = sharePerPerson * quantity;
        if (discountType === 'senior' || discountType === 'pwd' || discountType === 'solo_parent') {
          const discountPercent = discountType === 'solo_parent' ? 0.10 : 0.20;
          const vatExemptBase = discountHolderPortion / 1.12;
          const vatRemoved = discountHolderPortion - vatExemptBase;
          const discountValue = vatExemptBase * discountPercent;
          const netPayable = vatExemptBase - discountValue;
          totalDiscount += discountValue;
          totalTaxExempt += vatRemoved;
          totalVatRemoved += vatRemoved;
          totalAfterDiscount += netPayable;
          totalLess += vatRemoved + discountValue;
        } else if (discountType === 'athlete' || discountType === 'medal_of_valor') {
          const vatExemptBase = discountHolderPortion / 1.12;
          const vatRemoved = discountHolderPortion - vatExemptBase;
          const discountValue = vatExemptBase * 0.20;
          const afterDiscount = vatExemptBase - discountValue;
          const addBackVat = vatRemoved;
          const finalAfterAddVat = afterDiscount + addBackVat;
          totalDiscount += discountValue;
          totalAfterDiscount += finalAfterAddVat;
          totalAddBackVat += addBackVat;
          totalLess += vatRemoved + discountValue;
          totalAdd += addBackVat;
        }
      }
    });
    
    finalTotal = calculatedTotal - totalLess + totalAdd;
    discountAmount = totalDiscount;
    taxExemptAmount = totalVatRemoved;
    subtotalTaxExempt = calculatedTotal - totalVatRemoved;
    if (taxExemptAmountEl) taxExemptAmountEl.textContent = totalVatRemoved.toFixed(2);
    if (discountAmountEl) discountAmountEl.textContent = totalDiscount.toFixed(2);
  }
  /* HANDLE SINGLE DISCOUNT */
  else if (selectedDiscount !== 'no_discount') {
    if (selectedDiscount === 'regular') {
      const regMode = selectedDiscounts['regular_mode'] || '%';
      if (regMode === '₱') {
        /* Peso mode: flat amount off */
        discountAmount = regularDiscountPercent;
        finalTotal = calculatedTotal - discountAmount;
        if (finalTotal < 0) { finalTotal = 0; discountAmount = calculatedTotal; }
      } else {
        /* Percent mode: percentage off per share */
        const totalPax = getCurrentPax();
        const sharePerPerson = calculatedTotal / totalPax;
        discountAmount = sharePerPerson * (regularDiscountPercent / 100);
        finalTotal = calculatedTotal - discountAmount;
      }
    } else if (selectedDiscount === 'oth') {
      discountAmount = othDiscountAmount;
      finalTotal = calculatedTotal - discountAmount;
      if (finalTotal < 0) {
        finalTotal = 0;
        discountAmount = calculatedTotal;
      }
    } else if (selectedDiscount === 'senior' || selectedDiscount === 'pwd') {
      const totalPax = getCurrentPax();
      const sharePerPerson = calculatedTotal / totalPax;
      const seniorPortion = sharePerPerson * discountQuantity;
      const vatExemptBase = seniorPortion / 1.12;
      const vatRemoved = seniorPortion - vatExemptBase;
      const seniorDiscount = vatExemptBase * 0.20;
      const seniorNetPayable = vatExemptBase - seniorDiscount;
      const nonSeniorPortion = calculatedTotal - seniorPortion;
      finalTotal = seniorNetPayable + nonSeniorPortion;
      discountAmount = seniorDiscount;
      taxExemptAmount = vatRemoved;
      subtotalTaxExempt = vatExemptBase;
      if (taxExemptAmountEl) taxExemptAmountEl.textContent = taxExemptAmount.toFixed(2);
    } else if (selectedDiscount === 'athlete' || selectedDiscount === 'medal_of_valor') {
      const totalPax = getCurrentPax();
      const sharePerPerson = calculatedTotal / totalPax;
      const discountHolderPortion = sharePerPerson * discountQuantity;
      const nonDiscountHolderPortion = calculatedTotal - discountHolderPortion;
      const discountHolderLessVat = discountHolderPortion * (12 / 112);
      const discountHolderTotalAmountNoVat = discountHolderPortion - discountHolderLessVat;
      const discountHolderDiscount = discountHolderTotalAmountNoVat * 0.20;
      const afterDiscountAmount = discountHolderTotalAmountNoVat - discountHolderDiscount;
      const discountHolderAddVat = discountHolderLessVat;
      const discountHolderFinalTotal = afterDiscountAmount + discountHolderAddVat;
      finalTotal = discountHolderFinalTotal + nonDiscountHolderPortion;
      discountAmount = discountHolderDiscount;
      taxExemptAmount = 0.00;
      subtotalTaxExempt = 0.00;
    } else if (selectedDiscount === 'solo_parent') {
      const totalPax = getCurrentPax();
      const sharePerPerson = calculatedTotal / totalPax;
      const soloParentPortion = sharePerPerson * discountQuantity;
      const vatExemptBase = soloParentPortion / 1.12;
      const vatRemoved = soloParentPortion - vatExemptBase;
      const soloParentDiscount = vatExemptBase * 0.10;
      const soloParentNetPayable = vatExemptBase - soloParentDiscount;
      const nonSoloParentPortion = calculatedTotal - soloParentPortion;
      finalTotal = soloParentNetPayable + nonSoloParentPortion;
      discountAmount = soloParentDiscount;
      taxExemptAmount = vatRemoved;
      subtotalTaxExempt = vatExemptBase;
      if (taxExemptAmountEl) taxExemptAmountEl.textContent = taxExemptAmount.toFixed(2);
    }
  }

  /* Update displays */
  if (discountAmountEl) discountAmountEl.textContent = discountAmount.toFixed(2);
  const orderTotalDisplayEl = document.getElementById('orderTotalDisplay');
  const finalTotalEl = document.getElementById('finalTotal');
  if (orderTotalDisplayEl) orderTotalDisplayEl.textContent = calculatedTotal.toFixed(2);
  if (finalTotalEl) finalTotalEl.textContent = finalTotal.toFixed(2);

  /* VAT sales/exempt calculation */
  const totalPaxForVat = CALCULATED_COVERS;
  const zeroRatedSales = 0.00;
  const giftCheckPayment = document.getElementById('giftCheckPayment');
  const isGiftCheckPayment = giftCheckPayment && giftCheckPayment.checked;
  const giftCheckAmount = isGiftCheckPayment ? parseFloat(document.getElementById('giftCheckAmount').value) || 0 : 0;
  let amountDue = finalTotal;
  if (isGiftCheckPayment && giftCheckAmount >= finalTotal) amountDue = 0.00;

  let vatSales = 0, vatExemptSale = 0, totalSale = 0, vatAmountFinal = 0;
  if (isMultipleDiscounts) {
    const vatExemptTypes = ['senior', 'pwd', 'solo_parent'];
    const allVatExempt = selectedDiscountKeys.every(k => vatExemptTypes.includes(k) || (k === 'regular' && selectedDiscountKeys.length === 1));
    const totalDiscountHolders = selectedDiscountKeys.reduce((s, k) => { if (k === 'regular') return s; return s + getDiscountQuantity(k); }, 0);
    const hasNonDiscountHolders = totalPaxForVat > totalDiscountHolders;
    if (allVatExempt && selectedDiscountKeys.some(k => k !== 'regular') && !hasNonDiscountHolders) {
      vatSales = 0; vatExemptSale = calculatedTotal / 1.12; vatAmountFinal = 0; totalSale = vatExemptSale;
    } else {
      const sharePP = calculatedTotal / totalPaxForVat;
      let vatExemptPortion = 0, vatApplicablePortion = 0, regularDiscountPortion = 0;
      selectedDiscountKeys.forEach(dt => {
        const q = getDiscountQuantity(dt);
        const hp = sharePP * q;
        if (vatExemptTypes.includes(dt)) vatExemptPortion += hp;
        else if (dt === 'athlete' || dt === 'medal_of_valor') vatApplicablePortion += hp;
        else if (dt === 'regular') regularDiscountPortion += hp;
      });
      const totalDH = selectedDiscountKeys.reduce((s, k) => s + getDiscountQuantity(k), 0);
      const nonDHP = sharePP * (totalPaxForVat - totalDH);
      let regVatSales = 0, regVatAmt = 0;
      if (regularDiscountPortion > 0) {
        const rp = parseFloat(selectedDiscounts['regular']) || 0;
        const rMode = selectedDiscounts['regular_mode'] || '%';
        const rd = rMode === '₱' ? rp : regularDiscountPortion * (rp / 100);
        const rPay = regularDiscountPortion - rd;
        regVatSales = rPay / 1.12; regVatAmt = regVatSales * 0.12;
      }
      const otherVSP = nonDHP + vatApplicablePortion;
      const otherVS = otherVSP / 1.12; const otherVA = otherVS * 0.12;
      vatSales = regVatSales + otherVS; vatExemptSale = vatExemptPortion / 1.12;
      vatAmountFinal = regVatAmt + otherVA; totalSale = vatSales + vatExemptSale;
    }
  } else if (isGiftCheckPayment && giftCheckAmount >= finalTotal) {
    vatSales = calculatedSubtotal; vatExemptSale = 0; totalSale = calculatedTotal; vatAmountFinal = calculatedVAT;
  } else if (selectedDiscount === 'senior' || selectedDiscount === 'pwd') {
    const dq = getDiscountQuantity(selectedDiscount);
    if (totalPaxForVat === 1 && dq === 1) { vatSales = 0; vatExemptSale = calculatedSubtotal; totalSale = subtotalTaxExempt; }
    else {
      const spp = calculatedTotal / totalPaxForVat; const dhp = spp * dq;
      vatExemptSale = dhp / 1.12; vatSales = (calculatedTotal - dhp) / 1.12; totalSale = vatSales + vatExemptSale;
    }
    vatAmountFinal = calculatedVAT - taxExemptAmount;
  } else if (selectedDiscount === 'athlete' || selectedDiscount === 'medal_of_valor') {
    vatSales = calculatedSubtotal; vatExemptSale = 0; vatAmountFinal = calculatedVAT; totalSale = calculatedSubtotal;
  } else if (selectedDiscount === 'solo_parent') {
    const dq = getDiscountQuantity(selectedDiscount);
    if (totalPaxForVat === 1 && dq === 1) { vatSales = 0; vatExemptSale = calculatedSubtotal; totalSale = subtotalTaxExempt; }
    else {
      const spp = calculatedTotal / totalPaxForVat; const dhp = spp * dq;
      vatExemptSale = dhp / 1.12; vatSales = (calculatedTotal - dhp) / 1.12; totalSale = vatSales + vatExemptSale;
    }
    vatAmountFinal = calculatedVAT - taxExemptAmount;
  } else if (selectedDiscount === 'oth' || selectedDiscount === 'regular') {
    vatSales = calculatedTotal / 1.12; vatExemptSale = 0; vatAmountFinal = calculatedTotal - vatSales; totalSale = vatSales + vatExemptSale;
  } else {
    vatSales = calculatedSubtotal; vatExemptSale = 0; vatAmountFinal = calculatedVAT; totalSale = calculatedSubtotal;
  }

  const vatSalesEl = document.getElementById('vatSales');
  const vatExemptSaleEl = document.getElementById('vatExemptSale');
  const zeroRatedSalesEl = document.getElementById('zeroRatedSales');
  const vatAmountEl = document.getElementById('vatAmount');
  const amountDueEl = document.getElementById('amountDue');
  if (vatSalesEl) vatSalesEl.textContent = vatSales.toFixed(2);
  if (vatExemptSaleEl) vatExemptSaleEl.textContent = vatExemptSale.toFixed(2);
  if (zeroRatedSalesEl) zeroRatedSalesEl.textContent = zeroRatedSales.toFixed(2);
  if (vatAmountEl) vatAmountEl.textContent = vatAmountFinal.toFixed(2);
  if (amountDueEl) amountDueEl.textContent = amountDue.toFixed(2);

  /* --- Swap big total box to show Amount Due when discounts are active --- */
  const totalBoxLabel = document.querySelector('.stl-total-label');
  const origTotalRow = document.getElementById('originalTotalRow');
  const origTotalEl = document.getElementById('originalTotalDisplay');
  const amtDueRow = document.getElementById('amountDueRow');
  const hasActiveDiscounts = selectedDiscountKeys.length > 0;

  /* --- Update summary card discount breakdown --- */
  updateSummaryBreakdown({
    selectedDiscount, selectedDiscountKeys, isMultipleDiscounts,
    calculatedTotal, totalPax: getCurrentPax(), discountAmount,
    taxExemptAmount, regularDiscountPercent, othDiscountAmount
  });

  if (hasActiveDiscounts) {
    /* Big box: show Amount Due */
    if (totalBoxLabel) totalBoxLabel.textContent = 'Amount Due';
    if (orderTotalDisplayEl) orderTotalDisplayEl.textContent = amountDue.toFixed(2);
    /* Breakdown: show original total, hide the old Amount Due row */
    if (origTotalRow) origTotalRow.style.display = '';
    if (origTotalEl) origTotalEl.textContent = calculatedTotal.toFixed(2);
    if (amtDueRow) amtDueRow.style.display = 'none';
  } else {
    /* No discounts: restore big box to original total */
    if (totalBoxLabel) totalBoxLabel.textContent = 'Total';
    if (orderTotalDisplayEl) orderTotalDisplayEl.textContent = calculatedTotal.toFixed(2);
    if (origTotalRow) origTotalRow.style.display = 'none';
    if (amtDueRow) amtDueRow.style.display = '';
  }

  syncCreditAutoReceived();
  updateChangeAmount();
  renderSelectedDiscountBadges();
  updateSelectedDiscountsBoxVisibility();
  saveSettlementDraftToLocalStorage();
}

/* ---- Badge rendering (V1 parity) ---------------------------------------- */
function renderSelectedDiscountBadges() {
  const content = document.getElementById('selectedDiscountsContent');
  if (!content) return;
  const typeIcons = { regular:'REG', oth:'OTH', senior:'SC', pwd:'PWD', athlete:'NAAC', medal_of_valor:'MOV', solo_parent:'SP' };
  const html = Object.keys(selectedDiscounts || {}).filter(type => type !== 'regular_mode').map(type => {
    const icon = typeIcons[type] || type.toUpperCase();
    const raw = selectedDiscounts[type];
    const val = raw == null || raw === '' ? getDefaultDiscountValue(type) : raw;
    if (type === 'regular') { const rMode = selectedDiscounts['regular_mode'] || '%'; const displayVal = parseFloat(val) % 1 === 0 ? parseInt(val, 10) : parseFloat(val).toFixed(2); return `<div class="stl-discount-badge"><div class="stl-badge-icon">${icon}</div><div class="stl-badge-val"><span>${displayVal}${rMode === '%' ? '%' : '₱'}</span></div></div>`; }
    if (type === 'oth') { const displayVal = parseFloat(val) % 1 === 0 ? parseInt(val, 10) : parseFloat(val).toFixed(2); return `<div class="stl-discount-badge"><div class="stl-badge-icon">${icon}</div><div class="stl-badge-val"><span>₱${displayVal}</span></div></div>`; }
    return `<div class="stl-discount-badge"><div class="stl-badge-icon">${icon}</div><div class="stl-badge-val"><span>${parseInt(val, 10) || 1}</span></div></div>`;
  }).join('');
  content.innerHTML = html;
}

function renderSelectedDiscountMeta() {
  const details = document.getElementById('selectedDiscountDetails');
  if (!details) return;
  const beneficiaryTypes = ['senior', 'pwd', 'athlete', 'medal_of_valor', 'solo_parent'];
  const typeLabels = { senior:'SC', pwd:'PWD', athlete:'NAAC', medal_of_valor:'MOV', solo_parent:'SP', regular:'REG', oth:'OTH' };
  const preserved = {};
  document.querySelectorAll('.discount-name-field').forEach(inp => { const k = `${inp.dataset.discountType}_${inp.dataset.index}`; preserved[k] = preserved[k] || {}; preserved[k].name = inp.value || ''; });
  document.querySelectorAll('.discount-id-field').forEach(inp => { const k = `${inp.dataset.discountType}_${inp.dataset.index}`; preserved[k] = preserved[k] || {}; preserved[k].id = inp.value || ''; });
  document.querySelectorAll('.discount-value-field').forEach(inp => { preserved[inp.dataset.discountType] = { value: inp.value || '' }; });

  let html = '';
  Object.keys(selectedDiscounts || {}).filter(type => type !== 'regular_mode').forEach(type => {
    if (type === 'regular') {
      const val = preserved['regular']?.value || selectedDiscounts['regular'] || 10;
      const rMode = selectedDiscounts['regular_mode'] || '%';
      html += `<div class="stl-disc-group" data-discount-type="regular"><div class="stl-disc-header"><span class="stl-disc-type">REG</span><span>Regular Discount</span></div><div class="stl-disc-row stl-disc-value-row"><div class="stl-disc-mode-btns"><button type="button" class="stl-disc-mode-btn ${rMode === '₱' ? 'active' : ''}" data-mode="₱">₱</button><button type="button" class="stl-disc-mode-btn ${rMode === '%' ? 'active' : ''}" data-mode="%">%</button></div><input type="number" class="stl-disc-input discount-value-field" data-discount-type="regular" min="0" step="0.01" value="${parseFloat(val) || 0}"></div></div>`;
      return;
    }
    if (type === 'oth') {
      const val = preserved['oth']?.value || selectedDiscounts['oth'] || 0;
      html += `<div class="stl-disc-group" data-discount-type="oth"><div class="stl-disc-header"><span class="stl-disc-type">OTH</span><span>On The House</span></div><div class="stl-disc-row stl-disc-value-row"><span class="stl-disc-peso-label">₱</span><input type="number" class="stl-disc-input discount-value-field" data-discount-type="oth" min="0" step="0.01" value="${parseFloat(val) || 0}"></div></div>`;
      return;
    }
    if (!beneficiaryTypes.includes(type)) return;
    const qty = Math.max(1, parseInt(selectedDiscounts[type], 10) || 1);
    html += `<div class="stl-disc-group" data-discount-type="${type}"><div class="stl-disc-header"><span class="stl-disc-type">${typeLabels[type]}</span><div class="stl-disc-detail-controls"><button type="button" class="stl-disc-det-step" data-action="dec" data-discount-type="${type}" ${qty <= 1 ? 'disabled' : ''}>−</button><span class="stl-disc-det-count">${qty}</span><button type="button" class="stl-disc-det-step" data-action="inc" data-discount-type="${type}">+</button><button type="button" class="stl-disc-det-remove" data-discount-type="${type}" title="Remove">✕</button></div></div><div class="stl-disc-rows" data-discount-type="${type}">`;
    for (let i = 1; i <= qty; i++) {
      const k = `${type}_${i}`;
      html += `<div class="stl-disc-row" data-row-index="${i}"><input type="text" class="stl-disc-input discount-name-field" data-discount-type="${type}" data-index="${i}" placeholder="Name" value="${preserved[k]?.name || ''}"><input type="text" class="stl-disc-input discount-id-field" data-discount-type="${type}" data-index="${i}" placeholder="ID" value="${preserved[k]?.id || ''}"></div>`;
    }
    html += '</div></div>';
  });
  details.innerHTML = html;
}

function getDefaultDiscountValue(discountType) {
  if (discountType === 'regular') return 10;
  if (discountType === 'oth') return 0;
  return 1;
}

function updateSelectedDiscountsBoxVisibility() {
  const hasApplied = Object.keys(selectedDiscounts || {}).filter(k => k !== 'regular_mode').length > 0;
  const badgesBox = document.getElementById('discountBadgesBox');
  const detailsBox = document.getElementById('selectedDiscountsBox');
  const detailsEl = document.getElementById('selectedDiscountDetails');
  if (badgesBox) badgesBox.style.display = hasApplied ? 'block' : 'none';
  /* The discount column card always shows its state (V1 parity) — the
     "No discounts applied" placeholder is the column's empty voice.
     display:flex (not block) so the panel keeps growing inside the flex
     card and the placeholder centers in the full card body. */
  if (detailsBox) detailsBox.style.display = 'flex';
  if (!hasApplied && detailsEl) {
    /* Tall centered empty voice with the quiet tag glyph (inspiration layout). */
    detailsEl.innerHTML = '<div class="stl-no-discounts"><svg xmlns="http://www.w3.org/2000/svg" width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg><span>No discounts applied</span></div>';
  } else if (hasApplied && detailsEl) {
    const msg = detailsEl.querySelector('.stl-no-discounts');
    if (msg) msg.remove();
  }
}

function updateChangeAmount() {
  const paymentMethod = document.querySelector('input[name="payment_method"]:checked')?.value;
  const cashReceivedInput = document.getElementById('cashReceived');
  let cashReceived = 0;
  
  if (paymentMethod === 'cash' && cashReceivedInput) {
    cashReceived = parseFloat((cashReceivedInput.value || '').replace(/,/g, '')) || 0;
  } else if (paymentMethod === 'card') {
    cashReceived = parseFloat((cashReceivedInput?.value || '').replace(/,/g, '')) || 0;
    if (cashReceived <= 0) {
      cashReceived = parseFloat(document.getElementById('finalTotal')?.textContent) || 0;
    }
  } else if (paymentMethod === 'gift_check') {
    cashReceived = parseFloat(document.getElementById('giftCheckAmount')?.value) || 0;
  } else if (paymentMethod === 'cheque') {
    cashReceived = parseFloat(document.getElementById('chequeAmount')?.value) || 0;
  }
  
  const amountDue = parseFloat(document.getElementById('amountDue')?.textContent?.replace(/,/g, '')) || 0;
  const change = cashReceived - amountDue;
  
  const cashReceivedDisplayEl = document.getElementById('cashReceivedDisplay');
  const changeAmountEl = document.getElementById('changeAmount');
  
  if (cashReceivedDisplayEl) cashReceivedDisplayEl.textContent = cashReceived.toFixed(2);
  if (changeAmountEl) changeAmountEl.textContent = (change > 0 ? change : 0).toFixed(2);
}

function syncCreditAutoReceived() {
  const paymentMethod = document.querySelector('input[name="payment_method"]:checked')?.value;
  const cashReceivedInput = document.getElementById('cashReceived');
  if (paymentMethod === 'card' && cashReceivedInput) {
    const finalTotal = parseFloat(document.getElementById('finalTotal')?.textContent) || 0;
    if (finalTotal > 0 && (!cashReceivedInput.value || parseFloat(cashReceivedInput.value.replace(/,/g, '')) <= 0)) {
      cashReceivedInput.value = formatNumberWithCommas(finalTotal.toFixed(2));
    }
  }
}

export default {
  mount() {
    console.log('[settlement] mount() - settlement page module loaded');
    rootEl = document.querySelector('.stl-layout');
    if (!rootEl) {
      console.error('[settlement] mount() - root element .stl-layout not found');
      return;
    }
    
    /* Seed state from bridge */
    const bridgeEl = document.getElementById('settlementBridge');
    if (bridgeEl) {
      try {
        const bridge = JSON.parse(bridgeEl.textContent);
        ORDER_ID = bridge.order_id;
        ORDER_NO = bridge.order_no;
        ORDER_TOTAL = parseFloat(bridge.order_total) || 0;
        CUSTOMER_NAME = bridge.customer_name || '';
        ORDER_TIMESTAMP = bridge.order_timestamp || '';
        CALCULATED_COVERS = parseInt(bridge.calculated_covers) || 1;
      } catch (err) {
        console.error('[settlement] mount() - failed to parse bridge:', err);
      }
    }
    
    console.log('[settlement] mount() - seeded state:', { ORDER_ID, ORDER_NO, ORDER_TOTAL, CALCULATED_COVERS });
    
    /* Initialize presence lock */
    initializePresenceLock();
    
    /* Initialize payment method switching */
    initializePaymentMethods();
    
    /* Initialize discount button */
    initializeDiscountButton();
    
    /* Initialize PAX field edit */
    initializePaxEditAuth();
    
    /* Initialize bill out button */
    initializeBillOut();
    
    /* Initialize manual SI toggle */
    initializeManualSiToggle();
    
    /* Initialize form submission */
    initializeFormSubmission();
    
    /* Initialize card swipe reader */
    initializeCardSwipeReader();
    
    /* Initialize global keyboard (numpad only — A-Z disabled for POS terminals) */
    initializeGlobalKeyboard();
    
    /* Restore draft from localStorage or run initial calculation */
    const hasRestoredDraft = restoreSettlementDraftFromLocalStorage();
    if (!hasRestoredDraft) {
      calculateOrderDiscount();
    }
    
    /* Wire delegated input/change listeners for draft persistence */
    initializeDraftPersistence();
    
    console.log('[settlement] mount() - initialization complete');
  },
  
  destroy() {
    console.log('[settlement] destroy() - cleanup');
    if (presenceInterval) {
      clearInterval(presenceInterval);
      presenceInterval = null;
    }
    const releaseUrl = RELEASE_PATH(ORDER_ID);
    const csrfToken = getCsrfToken();
    const payload = JSON.stringify({ csrf_token: csrfToken });
    if (navigator.sendBeacon) {
      navigator.sendBeacon(releaseUrl, new Blob([payload], { type: 'application/json' }));
    } else {
      fetch(releaseUrl, {
        method: 'POST',
        keepalive: true,
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: payload
      });
    }
    destroyFns.forEach(fn => fn());
    destroyFns = [];
  }
};

function initializePresenceLock() {
  async function sendOrderPresence() {
    try {
      /* api.post injects X-CSRFToken — a bare fetch is blocked by the CSRF
         middleware with 400, which used to render as a false "Order In
         Use" modal on every settle-page load. */
      await api.post(`/order_presence/${ORDER_ID}`, {});
      return true;
    } catch (err) {
      /* Only a genuine 409 lock conflict freezes the page. Any other
         failure (network hiccup, expired session, non-lock server error)
         must NOT brick settlement — the server auto-expires stale locks. */
      if (err instanceof ApiError && (err.status === 409 || (err.detail && err.detail.lock_conflict))) {
        showOrderLockModal(err.message);
      }
      return false;
    }
  }
  
  function showOrderLockModal(message) {
    if (lockConflictHandled) return;
    lockConflictHandled = true;
    if (presenceInterval) clearInterval(presenceInterval);
    const pageRoot = document.querySelector('.stl-layout');
    if (pageRoot) pageRoot.style.pointerEvents = 'none';
    const modalMessage = message || 'Order is currently being processed by another user. Please wait until they finish.';
    modal.alert('Order In Use', modalMessage);
  }
  
  sendOrderPresence();
  presenceInterval = setInterval(sendOrderPresence, PRESENCE_POLL_MS);
}

function initializePaymentMethods() {
  const paymentRadios = document.querySelectorAll('input[name="payment_method"]');
  paymentRadios.forEach(radio => {
    radio.addEventListener('change', handlePaymentMethodChange);
  });
  
  const creditTypeGrid = document.getElementById('creditTypeGrid');
  const cardTypeInput = document.getElementById('cardType');
  if (creditTypeGrid && cardTypeInput) {
    creditTypeGrid.querySelectorAll('.stl-credit-option').forEach((btn) => {
      btn.addEventListener('click', function() {
        const selectedValue = this.getAttribute('data-value') || 'gcash';
        cardTypeInput.value = selectedValue;
        creditTypeGrid.querySelectorAll('.stl-credit-option').forEach((opt) => {
          const isActive = opt === this;
          opt.classList.toggle('active', isActive);
          opt.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        });
        updateCardSwipeVisibility();
        cardTypeInput.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
  }
  
  updateCardSwipeVisibility();
  updateChangeAmount();
}

function handlePaymentMethodChange() {
  const selectedMethod = this.value;
  const cashReceivedField = document.getElementById('cashReceivedField');
  const cardOptions = document.getElementById('cardOptions');
  const giftCheckAmountField = document.getElementById('giftCheckAmountField');
  const giftCheckNumberField = document.getElementById('giftCheckNumberField');
  const chequeAmountField = document.getElementById('chequeAmountField');
  const chequeNumberField = document.getElementById('chequeNumberField');
  
  if (cashReceivedField) cashReceivedField.style.display = (selectedMethod === 'cash' || selectedMethod === 'card') ? 'block' : 'none';
  if (cardOptions) cardOptions.style.display = (selectedMethod === 'card') ? 'block' : 'none';
  if (giftCheckAmountField) giftCheckAmountField.style.display = (selectedMethod === 'gift_check') ? 'block' : 'none';
  if (giftCheckNumberField) giftCheckNumberField.style.display = (selectedMethod === 'gift_check') ? 'block' : 'none';
  if (chequeAmountField) chequeAmountField.style.display = (selectedMethod === 'cheque') ? 'block' : 'none';
  if (chequeNumberField) chequeNumberField.style.display = (selectedMethod === 'cheque') ? 'block' : 'none';
  
  lastConfirmedPaymentMethod = selectedMethod;
  calculateOrderDiscount();
  saveSettlementDraftToLocalStorage();
}

function updateCardSwipeVisibility() {
  const cardTypeInput = document.getElementById('cardType');
  const cardSwipeField = document.getElementById('cardSwipeField');
  const currentType = cardTypeInput ? cardTypeInput.value : 'gcash';
  const isCardSwipe = currentType === 'credit_card' || currentType === 'debit_card';
  if (cardSwipeField) {
    cardSwipeField.style.display = isCardSwipe ? 'block' : 'none';
  }
}

function initializeDiscountButton() {
  const orderDiscountBtn = document.getElementById('orderDiscountBtn');
  if (orderDiscountBtn) {
    orderDiscountBtn.addEventListener('click', showDiscountModal);
  }
  /* Second entry point in the payment card (card rebuild, 2026-08-28):
     same modal, separate id so the frozen discounts-card hook stays intact. */
  const orderDiscountBtnPay = document.getElementById('orderDiscountBtnPay');
  if (orderDiscountBtnPay) {
    orderDiscountBtnPay.addEventListener('click', showDiscountModal);
  }
}

/* Check if regular/OTH discount is currently active in the modal. */
function hasAnyActiveDiscount(excludeType) {
  const overlay = document.querySelector('.disc-modal');
  if (!overlay) return false;
  const regularCard = overlay.querySelector('.stl-disc-option[data-discount-type="regular"]');
  const othCard = overlay.querySelector('.stl-disc-option[data-discount-type="oth"]');
  if (regularCard?.classList.contains('checked') && excludeType !== 'regular') return true;
  if (othCard?.classList.contains('checked') && excludeType !== 'oth') return true;
  return false;
}

/* Uncheck the "No Discount" checkbox and remove its visual state. */
function clearNoDiscount(overlayEl) {
  const ndCb = overlayEl.querySelector('input.discount-option[data-discount-type="no_discount"]');
  if (!ndCb) return;
  const ndCard = ndCb.closest('.stl-disc-option');
  if (ndCb.checked) {
    ndCb.checked = false;
  }
  if (ndCard) ndCard.classList.remove('checked');
}

function showDiscountModal() {
  console.log('[settlement] showDiscountModal() - opening discount modal');
  const discountTypes = [
    { key: 'no_discount', label: 'No Discount', icon: '—' },
    { key: 'senior', label: 'Senior Citizen', icon: 'SC', hasStepper: true },
    { key: 'pwd', label: 'PWD', icon: 'PWD', hasStepper: true },
    { key: 'athlete', label: 'National Athlete', icon: 'NAAC', hasStepper: true },
    { key: 'medal_of_valor', label: 'Medal of Valor', icon: 'MOV', hasStepper: true },
    { key: 'solo_parent', label: 'Solo Parent', icon: 'SP', hasStepper: true },
    { key: 'regular', label: 'Regular Discount', icon: 'REG' },
    { key: 'oth', label: 'On The House', icon: 'OTH' }
  ];

  /* Local mutable state for stepper counts — starts from selectedDiscounts. */
  const stepperState = {};
  discountTypes.forEach(dt => {
    if (dt.hasStepper) stepperState[dt.key] = parseInt(selectedDiscounts[dt.key], 10) || 0;
  });

  let hostEl = document.getElementById('modal-host');
  if (!hostEl) { hostEl = document.createElement('div'); hostEl.id = 'modal-host'; document.body.appendChild(hostEl); }

  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay open';
  overlay.innerHTML = `
    <div class="lib-modal-box disc-modal" role="dialog" aria-modal="true">
      <h3 class="lib-modal-title"><span class="disc-title-icon"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg></span>Apply Discount</h3>
      <div class="lib-modal-text">
        <div class="stl-disc-grid">
          ${discountTypes.map(dt => {
            const discountKeysOnly = Object.keys(selectedDiscounts).filter(k => k !== 'regular_mode');
            const isChecked = discountKeysOnly.length === 0 && dt.key === 'no_discount'
              || selectedDiscounts[dt.key] !== undefined;
            if (dt.hasStepper) {
              const count = stepperState[dt.key];
              const active = count > 0;
              return `<div class="stl-disc-option ${active ? 'checked' : ''}" data-discount-type="${dt.key}">
                <span class="stl-disc-opt-icon">${dt.icon}</span>
                <span class="stl-disc-opt-label">${dt.label}</span>
                <div class="stl-disc-stepper">
                  <button type="button" class="stl-disc-step-btn stl-disc-step-minus" data-key="${dt.key}" ${count <= 0 ? 'disabled' : ''}>−</button>
                  <span class="stl-disc-step-count" data-key="${dt.key}">${count}</span>
                  <button type="button" class="stl-disc-step-btn stl-disc-step-plus" data-key="${dt.key}">+</button>
                </div>
              </div>`;
            }
            /* Regular Discount / On The House — simple clickable cards */
            if (dt.key === 'regular' || dt.key === 'oth') {
              return `<div class="stl-disc-option ${isChecked ? 'checked' : ''}" data-discount-type="${dt.key}">
                <span class="stl-disc-opt-icon">${dt.icon}</span>
                <span class="stl-disc-opt-label">${dt.label}</span>
              </div>`;
            }
            return `<label class="stl-disc-option ${isChecked ? 'checked' : ''}">
              <input type="checkbox" class="discount-option" data-discount-type="${dt.key}" ${isChecked ? 'checked' : ''}>
              <span class="stl-disc-opt-icon">${dt.icon}</span>
              <span class="stl-disc-opt-label">${dt.label}</span>
            </label>`;
          }).join('')}
        </div>
      </div>
      <div class="disc-modal-actions">
        <button type="button" class="btn btn-ghost disc-modal-btn" data-action="cancel">Cancel</button>
        <button type="button" class="btn btn-primary disc-modal-btn" data-action="confirm" data-autofocus="1">Apply</button>
      </div>
    </div>`;

  hostEl.appendChild(overlay);
  if (window.openModal) window.openModal(overlay.id || overlay.className);

  const close = () => { overlay.remove(); };
  overlay.querySelector('[data-action="cancel"]').addEventListener('click', close);
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });

  /* --- Stepper (beneficiary types) --- */
  function refreshSteppers() {
    overlay.querySelectorAll('.stl-disc-stepper').forEach(stepper => {
      const key = stepper.querySelector('.stl-disc-step-plus')?.dataset.key;
      if (!key) return;
      const count = stepperState[key] || 0;
      stepper.querySelector('.stl-disc-step-count').textContent = count;
      stepper.querySelector('.stl-disc-step-minus').disabled = count <= 0;
      const card = stepper.closest('.stl-disc-option');
      if (card) card.classList.toggle('checked', count > 0);
    });
  }

  overlay.addEventListener('click', e => {
    /* Stepper +/- button */
    const btn = e.target.closest('.stl-disc-step-btn');
    if (btn) {
      const key = btn.dataset.key;
      const delta = btn.classList.contains('stl-disc-step-plus') ? 1 : -1;
      const currentCount = stepperState[key] || 0;
      const nextCount = Math.max(0, currentCount + delta);
      /* Block: cannot activate a statutory discount when Regular/OTH is selected. */
      if (delta > 0 && currentCount === 0 && hasAnyActiveDiscount(key)) {
        modal.error(
          'Discounts Cannot Be Combined',
          'Per RMC 71-2022 (Joint MC 1-2022), promotional discounts and statutory SC/PWD discounts cannot be stacked — only the higher/more favorable discount applies. Remove the other discount first.'
        );
        return;
      }
      /* Auto-remove No Discount when activating a discount. */
      if (delta > 0 && nextCount > 0 && currentCount === 0) {
        clearNoDiscount(overlay);
      }
      stepperState[key] = nextCount;
      refreshSteppers();
      updateParams();
      return;
    }
    /* Click on the card itself (not on a stepper button) → toggle selection */
    const card = e.target.closest('.stl-disc-option[data-discount-type]');
    if (!card) return;
    const dt = card.dataset.discountType;
    const dtInfo = discountTypes.find(d => d.key === dt);

    /* --- Regular / OTH cards: toggle on card click (not stepper, no checkbox) --- */
    const isInlineType = dt === 'regular' || dt === 'oth';
    if (isInlineType) {
      const isCurrentlyChecked = card.classList.contains('checked');
      if (isCurrentlyChecked) {
        card.classList.remove('checked');
      } else {
        /* Block statutory + Regular/OTH stacking per RMC 71-2022. */
        const hasStepperActive = Object.entries(stepperState).some(([k, v]) => v > 0);
        if (hasStepperActive) {
          modal.error('Discounts Cannot Be Combined', 'Per RMC 71-2022 (Joint MC 1-2022), promotional discounts and statutory SC/PWD discounts cannot be stacked — only the higher/more favorable discount applies. Remove the other discount first.');
          return;
        }
        /* Uncheck the other Regular/OTH card */
        overlay.querySelectorAll('.stl-disc-option[data-discount-type="regular"],.stl-disc-option[data-discount-type="oth"]').forEach(other => {
          if (other !== card) other.classList.remove('checked');
        });
        clearNoDiscount(overlay);
        card.classList.add('checked');
      }
      return;
    }

    if (!dtInfo?.hasStepper) return;   /* only stepper cards */
    /* Auto-remove No Discount when selecting a discount type below it. */
    clearNoDiscount(overlay);
    /* Block statutory + Regular/OTH stacking per RMC 71-2022. */
    if (stepperState[dt] === 0 && hasAnyActiveDiscount(dt)) {
      modal.error(
        'Discounts Cannot Be Combined',
        'Per RMC 71-2022 (Joint MC 1-2022), promotional discounts and statutory SC/PWD discounts cannot be stacked — only the higher/more favorable discount applies. Remove the other discount first.'
      );
      return;
    }
    if (stepperState[dt] > 0) {
      stepperState[dt] = 0;           /* deselect */
    } else {
      stepperState[dt] = 1;           /* select with count 1 */
    }
    refreshSteppers();
    updateParams();
  });

  /* --- Checkbox (no_discount) --- */
  function getCheckboxes() { return overlay.querySelectorAll('.discount-option'); }

  function updateParams() {
    /* No-op: Regular/OTH now use inline inputs inside their cards. */
  }

  getCheckboxes().forEach(cb => {
    cb.addEventListener('change', () => {
      const dt = cb.dataset.discountType;

      /* --- No Discount checked: clear everything else --- */
      if (dt === 'no_discount' && cb.checked) {
        getCheckboxes().forEach(other => {
          if (other !== cb) { other.checked = false; other.closest('.stl-disc-option')?.classList.remove('checked'); }
        });
        /* Also uncheck and clear inline discount cards */
        overlay.querySelectorAll('.stl-disc-inline').forEach(card => {
          card.classList.remove('checked');
          const icb = card.querySelector('.discount-option');
          if (icb) icb.checked = false;
        });
        Object.keys(stepperState).forEach(k => { stepperState[k] = 0; });
        refreshSteppers();
        cb.closest('.stl-disc-option')?.classList.toggle('checked', true);
        updateParams();
        return;
      }

      /* --- Regular/OTH checked: block if statutory discounts are active --- */
      if ((dt === 'regular' || dt === 'oth') && cb.checked) {
        const hasStepperActive = Object.entries(stepperState).some(([k, v]) => v > 0);
        if (hasStepperActive) {
          cb.checked = false;
          cb.closest('.stl-disc-option')?.classList.remove('checked');
          modal.error(
            'Discounts Cannot Be Combined',
            'Per RMC 71-2022 (Joint MC 1-2022), promotional discounts and statutory SC/PWD discounts cannot be stacked — only the higher/more favorable discount applies. Remove the other discount first.'
          );
          return;
        }
      }

      /* --- Any other checkbox checked: uncheck No Discount --- */
      if (cb.checked) {
        clearNoDiscount(overlay);
      }
      cb.closest('.stl-disc-option')?.classList.toggle('checked', cb.checked);
      updateParams();
    });
  });

  updateParams();

  overlay.querySelector('[data-action="confirm"]').addEventListener('click', () => {
    const newDiscounts = {};
    let hasNoDiscount = false;

    /* Gather checkbox-based selections (no_discount). */
    getCheckboxes().forEach(cb => {
      if (!cb.checked) return;
      const dt = cb.dataset.discountType;
      if (dt === 'no_discount') { hasNoDiscount = true; return; }
    });

    /* Gather Regular / OTH from checked cards. */
    overlay.querySelectorAll('.stl-disc-option[data-discount-type="regular"],.stl-disc-option[data-discount-type="oth"]').forEach(card => {
      if (!card.classList.contains('checked')) return;
      const dt = card.dataset.discountType;
      if (dt === 'regular') {
        newDiscounts['regular'] = selectedDiscounts['regular'] || '10';
        newDiscounts['regular_mode'] = selectedDiscounts['regular_mode'] || '%';
      } else if (dt === 'oth') {
        newDiscounts['oth'] = selectedDiscounts['oth'] || '0';
      }
    });

    /* Gather stepper-based selections (beneficiary types). */
    Object.keys(stepperState).forEach(k => {
      if (stepperState[k] > 0) newDiscounts[k] = stepperState[k];
    });

    if (hasNoDiscount || Object.keys(newDiscounts).length === 0) {
      selectedDiscounts = {};
      document.getElementById('orderDiscount').value = 'no_discount';
    } else {
      selectedDiscounts = newDiscounts;
      const types = Object.keys(selectedDiscounts).filter(k => k !== 'regular_mode').sort();
      document.getElementById('orderDiscount').value = types.length === 1 ? types[0] : types.join(',');
    }

    updateSelectedDiscountsBoxVisibility();
    close();
    calculateOrderDiscount();
    renderSelectedDiscountMeta();
    saveSettlementDraftToLocalStorage();
  });

  const focusTarget = overlay.querySelector('[data-autofocus]');
  if (focusTarget) focusTarget.focus();
}

/* ---- PAX admin auth modal (card swipe + credentials) ---------------------- */
function showPaxAuthModal() {
  return new Promise((resolve) => {
    const SWIPE_TIMEOUT_MS = 350;
    const SWIPE_MIN_LENGTH = 8;
    let resolved = false;
    let mode = 'swipe';
    let keyBuffer = '';
    let lastKeyTime = 0;
    let swipeTimer = null;
    let authOverlaySeq = 0;

    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'pax-auth-' + (++authOverlaySeq);

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-warning lib-modal-warning';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');

    box.innerHTML = `
      <h3 class="lib-modal-title lib-modal-warning-title" id="${overlay.id}-title">
        <span class="lib-modal-warning-icon"><svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></span>
        Authentication Required
      </h3>
      <p class="lib-modal-text lib-modal-warning-text">Swipe an admin or manager card, or enter credentials to unlock PAX editing.</p>
      <div class="auth-tabs" role="tablist">
        <button type="button" class="auth-tab active" data-auth-mode="swipe" aria-pressed="true">Card Swipe</button>
        <button type="button" class="auth-tab" data-auth-mode="credentials" aria-pressed="false">Use Credentials</button>
      </div>
      <div class="auth-panel" data-auth-panel="swipe">
        <div class="auth-swipe-waiting">
          <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="4" width="22" height="16" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>
          <span>Waiting for card swipe...</span>
        </div>
        <span class="auth-swipe-hint">Use an active admin or manager card.</span>
      </div>
      <form class="auth-form" data-auth-panel="credentials" style="display:none;">
        <div class="auth-field">
          <label for="${overlay.id}-user">Admin/Manager Username</label>
          <input type="text" id="${overlay.id}-user" name="username" placeholder="Enter username" autocomplete="username">
        </div>
        <div class="auth-field">
          <label for="${overlay.id}-pass">Admin/Manager Password</label>
          <input type="password" id="${overlay.id}-pass" name="password" placeholder="Enter password" autocomplete="current-password">
        </div>
      </form>
      <div class="lib-modal-warning-actions" style="margin-top:0.75rem;">
        <button type="button" class="btn lib-modal-warning-cancel" data-auth-action="cancel">Cancel</button>
        <button type="button" class="btn btn-warning" data-auth-action="confirm">Unlock</button>
      </div>`;

    overlay.appendChild(box);
    el.appendChild(overlay);

    function finish(value) {
      if (resolved) return;
      resolved = true;
      document.removeEventListener('keydown', onKeyDown);
      if (swipeTimer) clearTimeout(swipeTimer);
      window.closeModal(overlay);
      overlay.remove();
      resolve(value);
    }

    function extractCardNumber(buffer) {
      const digitsOnly = String(buffer).replace(/\D/g, '');
      return digitsOnly.length >= SWIPE_MIN_LENGTH ? digitsOnly : null;
    }

    function onSwipeDetected(raw) {
      const cardNum = extractCardNumber(raw);
      if (!cardNum) return;
      finish({ card_number: cardNum });
    }

    function onKeyDown(e) {
      if (resolved) return;
      const target = e.target;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'SELECT' || target.tagName === 'TEXTAREA')) return;
      const now = Date.now();
      if (now - lastKeyTime > SWIPE_TIMEOUT_MS) keyBuffer = '';
      lastKeyTime = now;
      if (e.key.length === 1) keyBuffer += e.key;
      if (swipeTimer) { clearTimeout(swipeTimer); swipeTimer = null; }
      if (e.key === 'Enter' && keyBuffer.length >= SWIPE_MIN_LENGTH) {
        onSwipeDetected(keyBuffer);
        keyBuffer = '';
        e.preventDefault();
        return;
      }
      if (keyBuffer.length > 120) {
        onSwipeDetected(keyBuffer);
        keyBuffer = '';
        e.preventDefault();
        return;
      }
      swipeTimer = setTimeout(() => {
        if (keyBuffer.length >= SWIPE_MIN_LENGTH) onSwipeDetected(keyBuffer);
        keyBuffer = '';
        swipeTimer = null;
      }, SWIPE_TIMEOUT_MS + 80);
      if (keyBuffer.length > 500) keyBuffer = keyBuffer.slice(-200);
    }

    /* Tab switching */
    box.addEventListener('click', (e) => {
      const tab = e.target.closest('[data-auth-mode]');
      if (tab) {
        mode = tab.dataset.authMode;
        box.querySelectorAll('[data-auth-mode]').forEach(t => {
          t.classList.toggle('active', t === tab);
          t.setAttribute('aria-pressed', String(t === tab));
        });
        box.querySelector('[data-auth-panel="swipe"]').style.display = mode === 'swipe' ? '' : 'none';
        box.querySelector('[data-auth-panel="credentials"]').style.display = mode === 'credentials' ? '' : 'none';
        if (mode === 'credentials') {
          const first = box.querySelector(`#${overlay.id}-user`);
          if (first) first.focus();
        }
        return;
      }
      if (e.target.closest('[data-auth-action="cancel"]')) {
        finish(null);
        return;
      }
      if (e.target.closest('[data-auth-action="confirm"]')) {
        if (mode === 'credentials') {
          const username = box.querySelector(`#${overlay.id}-user`).value.trim();
          const password = box.querySelector(`#${overlay.id}-pass`).value;
          if (!username || !password) {
            notify.error('Please enter both username and password.');
            return;
          }
          finish({ username, password });
        } else {
          finish(null);
        }
      }
    });

    const userInput = box.querySelector(`#${overlay.id}-user`);
    const passInput = box.querySelector(`#${overlay.id}-pass`);
    if (userInput && passInput) {
      userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          passInput.focus();
        }
      });
      passInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          const confirmBtn = box.querySelector('[data-auth-action="confirm"]');
          if (confirmBtn) confirmBtn.click();
        }
      });
    }

    /* Enter key on credential fields submits */
    box.querySelector('.auth-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const username = box.querySelector(`#${overlay.id}-user`).value.trim();
      const password = box.querySelector(`#${overlay.id}-pass`).value;
      if (!username || !password) {
        notify.error('Please enter both username and password.');
        return;
      }
      finish({ username, password });
    });

    /* Overlay click dismisses */
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) finish(null);
    });
    document.addEventListener('keydown', onKeyDown);

    window.openModal(overlay.id);
    const swipePanel = box.querySelector('[data-auth-panel="swipe"]');
    if (swipePanel) swipePanel.focus();
  });
}

function initializePaxEditAuth() {
  const paxInput = document.getElementById('totalNoPax');
  if (!paxInput) return;
  let isPromptOpen = false;
  
  paxInput.addEventListener('dblclick', async function() {
    if (!paxInput.hasAttribute('readonly')) return;
    if (isPromptOpen) return;
    isPromptOpen = true;
    
    try {
      const auth = await showPaxAuthModal();
      if (!auth) return;

      /* Verify credentials against the server */
      const data = await api.post('/verify_admin_credentials',
        auth.card_number
          ? { card_number: auth.card_number }
          : { username: auth.username, password: auth.password }
      );

      if (!data || !data.success) {
        modal.error('Authentication Failed', data?.message || 'Invalid admin/manager credentials.');
        return;
      }

      paxInput.removeAttribute('readonly');
      paxInput.style.background = 'var(--bg-card)';
      paxInput.style.cursor = 'text';
      paxInput.title = 'PAX/CUSTOMER is now editable';
      paxInput.focus();
    } catch (error) {
      console.error('Auth error:', error);
      modal.error('Authentication Error', 'Failed to verify credentials. Please try again.');
    } finally {
      isPromptOpen = false;
    }
  });

  /* Stepper buttons (card rebuild, 2026-08-28): stepping a locked field
     re-opens the same admin auth modal via the dblclick handler; once
     unlocked they adjust the count in place and re-fire input/change so
     any listeners stay in sync. */
  const paxMinusBtn = document.getElementById('paxMinusBtn');
  const paxPlusBtn = document.getElementById('paxPlusBtn');
  const stepPax = (delta) => {
    if (paxInput.hasAttribute('readonly')) {
      paxInput.dispatchEvent(new Event('dblclick'));
      return;
    }
    const current = parseInt(paxInput.value, 10) || 1;
    const next = Math.max(1, current + delta);
    if (next !== current) {
      paxInput.value = String(next);
      paxInput.dispatchEvent(new Event('input', { bubbles: true }));
      paxInput.dispatchEvent(new Event('change', { bubbles: true }));
    }
  };
  if (paxMinusBtn) paxMinusBtn.addEventListener('click', () => stepPax(-1));
  if (paxPlusBtn) paxPlusBtn.addEventListener('click', () => stepPax(1));
}

function collectSettlementBillData() {
  const _orderDiscount = document.getElementById('orderDiscount')?.value || 'no_discount';
  const _totalWithVat = getSettlementTotalWithVat();
  const _finalTotal = getNumericTextContent('finalTotal', ORDER_TOTAL);
  const _totalNoOfPax = getCurrentPax();
  const _isMultiple = _orderDiscount.includes(',');

  // ---- Re-derive all discount values using the same math as calculateOrderDiscount() ----
  let _taxExemptAmount = 0;
  let _discountAmount = 0;
  let _subtotalTaxExempt = 0;
  let _lessVatAmount = 0;
  let _addVatAmount = 0;
  let _mixedDiscounts = {};

  const _sharePP = _totalWithVat / _totalNoOfPax;

  if (_isMultiple) {
    const _types = _orderDiscount.split(',').map(t => t.trim());
    let totalDiscount = 0, totalVatRemoved = 0, totalLess = 0, totalAdd = 0;

    _types.forEach(dt => {
      const q = getDiscountQuantity(dt);
      const hp = _sharePP * q;
      const vatRemoved = hp * (12 / 112);

      let da = 0, va = 0;
      if (dt === 'regular') {
        const _regMode = selectedDiscounts['regular_mode'] || '%';
        const _regVal = parseFloat(selectedDiscounts['regular']) || 0;
        da = _regMode === '₱' ? _regVal : (_sharePP * 1) * (_regVal / 100);
        totalLess += da;
      } else if (dt === 'senior' || dt === 'pwd' || dt === 'solo_parent') {
        const pct = dt === 'solo_parent' ? 0.10 : 0.20;
        const vatExBase = hp / 1.12;
        da = vatExBase * pct;
        va = hp - vatExBase;
        totalVatRemoved += va;
        totalLess += va + da;
      } else if (dt === 'athlete' || dt === 'medal_of_valor') {
        const vatExBase = hp / 1.12;
        da = vatExBase * 0.20;
        va = hp - vatExBase;
        totalLess += va + da;
        totalAdd += va;
      }
      totalDiscount += da;
      _mixedDiscounts[dt] = { vat: va, discount: da, percent: dt === 'solo_parent' ? 10 : 20 };
    });

    _discountAmount = totalDiscount;
    _taxExemptAmount = totalVatRemoved;
    _subtotalTaxExempt = _totalWithVat - totalVatRemoved;

  } else if (_orderDiscount === 'senior' || _orderDiscount === 'pwd') {
    const q = getDiscountQuantity(_orderDiscount);
    const hp = _sharePP * q;
    const vatExBase = hp / 1.12;
    _taxExemptAmount = hp - vatExBase;
    _discountAmount = vatExBase * 0.20;
    _subtotalTaxExempt = vatExBase;

  } else if (_orderDiscount === 'solo_parent') {
    const q = getDiscountQuantity(_orderDiscount);
    const hp = _sharePP * q;
    const vatExBase = hp / 1.12;
    _taxExemptAmount = hp - vatExBase;
    _discountAmount = vatExBase * 0.10;
    _subtotalTaxExempt = vatExBase;

  } else if (_orderDiscount === 'athlete' || _orderDiscount === 'medal_of_valor') {
    const q = getDiscountQuantity(_orderDiscount);
    const hp = _sharePP * q;
    _lessVatAmount = hp * (12 / 112);
    _addVatAmount = _lessVatAmount;
    const vatExBase = hp / 1.12;
    _discountAmount = vatExBase * 0.20;

  } else if (_orderDiscount === 'regular') {
    const _regMode = selectedDiscounts['regular_mode'] || '%';
    const _regVal = parseFloat(selectedDiscounts['regular']) || 0;
    _discountAmount = _regMode === '₱' ? _regVal : _sharePP * (_regVal / 100);

  } else if (_orderDiscount === 'oth') {
    _discountAmount = parseFloat(selectedDiscounts['oth'] || 0);
  }

  // discountQuantities per type
  const _discountQuantities = {};
  if (_orderDiscount && _orderDiscount !== 'no_discount') {
    const _types = _orderDiscount.includes(',') ? _orderDiscount.split(',') : [_orderDiscount];
    _types.forEach(t => {
      const tr = (t || '').trim();
      if (!tr) return;
      _discountQuantities[tr] = getDiscountQuantity(tr);
    });
  }

  // discountBeneficiaries - indexed format { type: { 0: { name, id }, 1: ... } }
  const _discountBeneficiaries = {};
  document.querySelectorAll('.discount-name-field').forEach(inp => {
    const dt = inp.dataset.discountType;
    const idx = parseInt(inp.dataset.index, 10);
    if (!dt) return;
    const safeIdx = Number.isFinite(idx) ? idx : 0;
    _discountBeneficiaries[dt] = _discountBeneficiaries[dt] || {};
    _discountBeneficiaries[dt][safeIdx] = _discountBeneficiaries[dt][safeIdx] || { name: '', id: '' };
    _discountBeneficiaries[dt][safeIdx].name = inp.value.trim();
  });
  document.querySelectorAll('.discount-id-field').forEach(inp => {
    const dt = inp.dataset.discountType;
    const idx = parseInt(inp.dataset.index, 10);
    if (!dt) return;
    const safeIdx = Number.isFinite(idx) ? idx : 0;
    _discountBeneficiaries[dt] = _discountBeneficiaries[dt] || {};
    _discountBeneficiaries[dt][safeIdx] = _discountBeneficiaries[dt][safeIdx] || { name: '', id: '' };
    _discountBeneficiaries[dt][safeIdx].id = inp.value.trim();
  });

  return {
    customerName: CUSTOMER_NAME || '',
    timestamp: ORDER_TIMESTAMP || '',
    orderNo: ORDER_NO || '',
    orderDiscount: _orderDiscount,
    discountAmount: _discountAmount,
    taxExemptAmount: _taxExemptAmount,
    subtotalTaxExempt: _subtotalTaxExempt,
    totalWithVat: _totalWithVat,
    finalTotal: _finalTotal,
    lessVatAmount: _lessVatAmount,
    addVatAmount: _addVatAmount,
    totalNoOfPax: _totalNoOfPax,
    discountQuantities: _discountQuantities,
    regularDiscountPercent: parseFloat(selectedDiscounts['regular'] || 0),
    mixedDiscounts: _mixedDiscounts,
    discountBeneficiaries: _discountBeneficiaries,
    is_mixed_discount_from_frontend: _isMultiple
  };
}

function initializeBillOut() {
  const billOutBtn = document.getElementById('billOutBtn');
  if (!billOutBtn) return;
  
  billOutBtn.addEventListener('click', async function() {
    /* --- Validate discount name/ID fields --- */
    const _billDiscLabels = { senior: 'Senior Citizen', pwd: 'PWD', athlete: 'National Athlete', medal_of_valor: 'Medal of Valor', solo_parent: 'Solo Parent' };
    const _bEmptyNames = [], _bEmptyIds = [];
    document.querySelectorAll('.discount-name-field').forEach(inp => {
      if (!inp.value.trim()) _bEmptyNames.push(_billDiscLabels[inp.dataset.discountType] || inp.dataset.discountType);
    });
    document.querySelectorAll('.discount-id-field').forEach(inp => {
      if (!inp.value.trim()) _bEmptyIds.push(_billDiscLabels[inp.dataset.discountType] || inp.dataset.discountType);
    });
    if (_bEmptyNames.length > 0 || _bEmptyIds.length > 0) {
      const _parts = [];
      if (_bEmptyNames.length > 0) _parts.push(`${[...new Set(_bEmptyNames)].join(' and ')} name${_bEmptyNames.length > 1 ? 's' : ''}`);
      if (_bEmptyIds.length > 0) _parts.push(`${[...new Set(_bEmptyIds)].join(' and ')} ID${_bEmptyIds.length > 1 ? 's' : ''}`);
      modal.error('Missing Information', `Please provide the ${_parts.join(' and ')} before continuing.`);
      return;
    }

    try {
      const printerReady = await guardPrinter();
      if (!printerReady) return;

      billOutBtn.disabled = true;
      billOutBtn.innerHTML = '<span>Printing...</span>';
      
      const billData = collectSettlementBillData();
      const data = await api.post('/bill_out/' + ORDER_ID, billData);
      if (data && data.success) {
        notify.success('Bill printed successfully!');
      } else {
        const msg = (data && data.message) || 'Failed to print bill — Printer may be busy or disconnected.';
        modal.error('Printer Error', msg);
        notify.error(msg);
      }
    } catch (error) {
      console.error('Error in bill out:', error);
      const msg = error.message || 'Failed to print bill — Printer may be busy or disconnected.';
      modal.error('Printer Error', msg);
      notify.error(msg);
    } finally {
      billOutBtn.disabled = false;
      billOutBtn.innerHTML = '<span>Bill Out</span>';
    }
  });
}

function initializeManualSiToggle() {
  const toggleManualSiBtn = document.getElementById('toggleManualSiBtn');
  const manualSiFieldWrap = document.getElementById('manualSiFieldWrap');
  const manualSiInput = document.getElementById('remarks');
  
  if (toggleManualSiBtn && manualSiFieldWrap && manualSiInput) {
    const setManualSiState = (show) => {
      manualSiFieldWrap.style.display = show ? 'block' : 'none';
      toggleManualSiBtn.setAttribute('aria-expanded', show ? 'true' : 'false');
      toggleManualSiBtn.innerHTML = show
        ? '<span>Hide Manual SI #</span>'
        : '<span>Add Manual SI #</span>';
      if (show) {
        manualSiInput.focus();
      } else {
        manualSiInput.value = '';
      }
    };
    
    setManualSiState(Boolean(manualSiInput.value && manualSiInput.value.trim() !== ''));
    toggleManualSiBtn.addEventListener('click', function() {
      const isExpanded = this.getAttribute('aria-expanded') === 'true';
      setManualSiState(!isExpanded);
    });
  }
}

function initializeFormSubmission() {
  const settlementForm = document.getElementById('settlementForm');
  if (!settlementForm) return;
  
  settlementForm.addEventListener('submit', async function(e) {
    e.preventDefault();
    e.stopPropagation();
    
    calculateOrderDiscount();

    /* --- Validate discount name/ID fields --- */
    const _discLabels = { senior: 'Senior Citizen', pwd: 'PWD', athlete: 'National Athlete', medal_of_valor: 'Medal of Valor', solo_parent: 'Solo Parent' };
    const _emptyNames = [], _emptyIds = [];
    document.querySelectorAll('.discount-name-field').forEach(inp => {
      if (!inp.value.trim()) _emptyNames.push(_discLabels[inp.dataset.discountType] || inp.dataset.discountType);
    });
    document.querySelectorAll('.discount-id-field').forEach(inp => {
      if (!inp.value.trim()) _emptyIds.push(_discLabels[inp.dataset.discountType] || inp.dataset.discountType);
    });
    if (_emptyNames.length > 0 || _emptyIds.length > 0) {
      const _parts = [];
      if (_emptyNames.length > 0) _parts.push(`${[...new Set(_emptyNames)].join(' and ')} name${_emptyNames.length > 1 ? 's' : ''}`);
      if (_emptyIds.length > 0) _parts.push(`${[...new Set(_emptyIds)].join(' and ')} ID${_emptyIds.length > 1 ? 's' : ''}`);
      modal.error('Missing Information', `Please provide the ${_parts.join(' and ')} before continuing.`);
      return;
    }

    /* --- Validate amount received >= amount due --- */
    const _pm = document.querySelector('input[name="payment_method"]:checked')?.value || 'cash';
    let _received = 0;
    if (_pm === 'cash') {
      _received = parseFloat((document.getElementById('cashReceived')?.value || '').replace(/,/g, '')) || 0;
    } else if (_pm === 'card') {
      _received = parseFloat((document.getElementById('cashReceived')?.value || '').replace(/,/g, '')) || 0;
      if (_received <= 0) _received = parseFloat(document.getElementById('finalTotal')?.textContent) || 0;
    } else if (_pm === 'gift_check') {
      _received = parseFloat(document.getElementById('giftCheckAmount')?.value) || 0;
    } else if (_pm === 'cheque') {
      _received = parseFloat(document.getElementById('chequeAmount')?.value) || 0;
    }
    const _due = parseFloat(document.getElementById('amountDue')?.textContent?.replace(/,/g, '')) || ORDER_TOTAL;
    if (_received < _due) {
      modal.error('Insufficient Amount', `Amount received (\u20B1${_received.toFixed(2)}) is less than the amount due (\u20B1${_due.toFixed(2)}). Please enter the correct amount.`);
      return;
    }

    const printerReady = await guardPrinter();
    if (!printerReady) return;

    notify.info('Settling order...');
    
    try {
      const formData = new FormData(settlementForm);

      /* ---- Append calculated settlement values (V1 parity) ---- */
      const _orderDiscount = document.getElementById('orderDiscount')?.value || 'no_discount';
      let _discountQuantity = 0;
      if (_orderDiscount.includes(',')) {
        for (const _dt of _orderDiscount.split(',')) _discountQuantity += getDiscountQuantity(_dt.trim());
      } else {
        _discountQuantity = getDiscountQuantity(_orderDiscount);
      }
      const _regularDiscountPercent = parseFloat(selectedDiscounts['regular'] || 0);
      const _othDiscountAmount = parseFloat(selectedDiscounts['oth'] || 0);
      const _finalTotal = getNumericTextContent('finalTotal', ORDER_TOTAL);
      const _discountAmount = getDisplayedDiscountAmount();
      const _taxExemptAmount = getNumericTextContent('taxExemptAmount', 0);
      const _totalWithVat = getSettlementTotalWithVat();
      let _subtotalTaxExempt = 0;
      if (_orderDiscount.includes(',') || ['senior','pwd','solo_parent'].includes(_orderDiscount)) {
        _subtotalTaxExempt = _totalWithVat - _taxExemptAmount;
      }
      const _vatSales = getNumericTextContent('vatSales', 0);
      const _vatExemptSale = getNumericTextContent('vatExemptSale', 0);
      const _zeroRatedSales = getNumericTextContent('zeroRatedSales', 0);
      const _totalSale = ORDER_TOTAL;
      const _vatAmount = getNumericTextContent('vatAmount', 0);
      const _amountDue = getNumericTextContent('amountDue', ORDER_TOTAL);

      formData.append('final_total', _finalTotal);
      let _mixedDiscounts = {};
      let _formDiscountAmount = _discountAmount;
      if (_orderDiscount.includes(',')) {
        const _types = _orderDiscount.split(',').map(t => t.trim());
        const _totalPax = getCurrentPax();
        const _sharePP = ORDER_TOTAL / _totalPax;
        _types.forEach(dt => {
          const q = getDiscountQuantity(dt);
          const pct = dt === 'solo_parent' ? 10 : 20;
          const hp = _sharePP * q;
          let da = 0;
          if (dt === 'regular') { const _regMode = selectedDiscounts['regular_mode'] || '%'; const _regVal = parseFloat(selectedDiscounts['regular']) || 0; da = _regMode === '₱' ? _regVal : (_sharePP * 1) * (_regVal / 100); }
          else { da = (hp / 1.12) * (pct / 100); }
          _mixedDiscounts[dt] = da;
        });
        let _bs = 0; Object.values(_mixedDiscounts).forEach(a => { _bs += a; });
        if (_bs > 0) _formDiscountAmount = _bs;
      }
      formData.append('discount_amount', _formDiscountAmount);
      formData.append('tax_exempt_amount', _taxExemptAmount);
      formData.append('subtotal_tax_exempt', _subtotalTaxExempt);
      formData.append('total_with_vat', _totalWithVat);
      formData.append('vat_sales', _vatSales);
      formData.append('vat_exempt_sale', _vatExemptSale);
      formData.append('zero_rated_sales', _zeroRatedSales);
      formData.append('total_sale', _totalSale);
      formData.append('vat_amount', _vatAmount);
      formData.append('amount_due', _amountDue);
      formData.append('regular_discount_percent', _regularDiscountPercent);
      formData.append('oth_discount_amount', _othDiscountAmount);
      formData.append('discount_quantity', _discountQuantity);
      formData.append('discount_breakdown', JSON.stringify(_mixedDiscounts));

      const response = await fetch(settlementForm.action, {
        method: 'POST',
        headers: { 'X-CSRFToken': getCsrfToken() },
        body: formData,
        credentials: 'same-origin'
      });
      
      const data = await response.json();
      if (data.success) {
        clearSettlementLocalStorage();
        notify.success('Transaction complete!');
        setTimeout(() => {
          window.location.href = '/orders';
        }, 1500);
      } else {
        notify.error(data.message || 'Failed to settle order');
      }
    } catch (error) {
      console.error('Error settling order:', error);
      notify.error('Error settling order');
    }
  });
}

/* ---- Card swipe reader (V1 parity) ---------------------------------------- */
function initializeCardSwipeReader() {
  const cardSwipeDataInput = document.getElementById('cardSwipeData');
  if (!cardSwipeDataInput) return;
  const cardSwipeStatus = document.getElementById('cardSwipeStatus');
  const cardSwipeParsed = document.getElementById('cardSwipeParsed');
  const parsedCardNumber = document.getElementById('parsedCardNumber');
  const parsedCardName = document.getElementById('parsedCardName');
  const parsedExpiry = document.getElementById('parsedExpiry');
  const parsedCardType = document.getElementById('parsedCardType');
  const cardTypeInput = document.getElementById('cardType');

  let keyBuffer = '', lastKeyTime = 0, swipeTimer = null, suppressEnterUntil = 0;
  const SWIPE_TIMEOUT_MS = 350, SWIPE_MIN_LENGTH = 8;

  function isCardSwipeActive() {
    const ct = cardTypeInput ? cardTypeInput.value : '';
    return ct === 'credit_card' || ct === 'debit_card';
  }
  function getCardType(pan) {
    if (!pan) return null;
    const f = pan.charAt(0), ft = pan.slice(0, 2);
    if (f === '4') return 'Visa'; if (f === '5') return 'Mastercard';
    if (ft === '34' || ft === '37') return 'Amex'; if (f === '6') return 'Discover';
    if (f === '3') return 'JCB'; return 'Card';
  }
  function parseTrack1(t) { const m = t.match(/^%?B?(\d{13,19})\^([^\^]*)\^(\d{4})/); return m ? { pan: m[1], name: m[2].replace(/\//g, ' ').trim(), expiry: m[3] } : null; }
  function parseTrack2(t) { const m = t.match(/^;?(\d{13,19})=(\d{4})/); return m ? { pan: m[1], name: null, expiry: m[2] } : null; }
  function parseCardSwipe(raw) {
    raw = raw.trim(); let result = { pan: null, name: null, expiry: null };
    const lines = raw.split(/[\r\n]+/);
    for (const line of lines) {
      const t = line.trim(); if (!t) continue;
      if (t.includes('^')) { const p = parseTrack1(t); if (p) { result.pan = p.pan; result.name = p.name; if (p.expiry) result.expiry = p.expiry; } }
      if (t.includes('=')) { const p = parseTrack2(t); if (p) { if (!result.pan) result.pan = p.pan; if (p.expiry) result.expiry = p.expiry; } }
    }
    if (result.pan) return result;
    const nm = raw.match(/(\d{13,19})/); return nm ? { pan: nm[1], name: null, expiry: null } : null;
  }
  function looksLikeSwipe(buf) { return buf.includes('%') || buf.includes(';') || /\d{13,}/.test(buf); }
  function displayParsed(parsed) {
    if (!cardSwipeParsed || !parsedCardNumber) return;
    cardSwipeParsed.style.display = 'block';
    const ct = getCardType(parsed.pan);
    if (parsedCardType) { if (ct) { parsedCardType.textContent = ct; parsedCardType.style.display = 'inline-block'; } else parsedCardType.style.display = 'none'; }
    const masked = parsed.pan ? parsed.pan.slice(0, 4) + ' **** **** ' + parsed.pan.slice(-4) : '----';
    parsedCardNumber.textContent = 'Card: ' + masked;
    if (parsedCardName) { if (parsed.name) { parsedCardName.textContent = parsed.name; parsedCardName.style.display = 'inline-block'; } else parsedCardName.style.display = 'none'; }
    if (parsedExpiry) { if (parsed.expiry && parsed.expiry.length === 4) { parsedExpiry.textContent = 'Exp: ' + parsed.expiry.slice(2, 4) + '/' + parsed.expiry.slice(0, 2); parsedExpiry.style.display = 'inline-block'; } else parsedExpiry.style.display = 'none'; }
  }
  function onSwipeDetected(raw) {
    suppressEnterUntil = Date.now() + 800;
    if (cardSwipeDataInput) cardSwipeDataInput.value = raw.trim();
    if (cardSwipeStatus) { cardSwipeStatus.textContent = 'Card detected!'; cardSwipeStatus.className = 'stl-swipe-status stl-swipe-ok'; }
    const parsed = parseCardSwipe(raw);
    if (parsed) {
      displayParsed(parsed);
      const cardJson = { card_type: getCardType(parsed.pan) || 'Card', number: parsed.pan || '', expiry: parsed.expiry || '' };
      const hiddenJson = document.getElementById('cardSwipeJson');
      if (hiddenJson) hiddenJson.value = JSON.stringify(cardJson);
    } else if (cardSwipeParsed) cardSwipeParsed.style.display = 'none';
    if (cardSwipeDataInput) { cardSwipeDataInput.style.transition = 'box-shadow 0.3s ease'; cardSwipeDataInput.style.boxShadow = '0 0 0 3px rgba(25,135,84,0.35)'; setTimeout(() => { cardSwipeDataInput.style.boxShadow = ''; }, 600); }
  }
  function processBuffer(buf) { if (buf.length >= SWIPE_MIN_LENGTH && looksLikeSwipe(buf)) onSwipeDetected(buf); }

  document.addEventListener('keydown', function(e) {
    if (!isCardSwipeActive()) { keyBuffer = ''; return; }
    if (e.key === 'Enter' && Date.now() < suppressEnterUntil) { e.preventDefault(); return; }
    const now = Date.now(); if (now - lastKeyTime > SWIPE_TIMEOUT_MS) keyBuffer = '';
    lastKeyTime = now;
    if (e.key.length === 1) keyBuffer += e.key;
    if (swipeTimer) { clearTimeout(swipeTimer); swipeTimer = null; }
    if (e.key === 'Enter' && keyBuffer.length >= SWIPE_MIN_LENGTH) { processBuffer(keyBuffer); keyBuffer = ''; e.preventDefault(); return; }
    if (keyBuffer.length >= SWIPE_MIN_LENGTH && keyBuffer.endsWith('?')) { processBuffer(keyBuffer); keyBuffer = ''; e.preventDefault(); return; }
    if (keyBuffer.length > 120) { processBuffer(keyBuffer); keyBuffer = ''; e.preventDefault(); return; }
    swipeTimer = setTimeout(() => { if (keyBuffer.length >= SWIPE_MIN_LENGTH) processBuffer(keyBuffer); keyBuffer = ''; swipeTimer = null; }, SWIPE_TIMEOUT_MS + 80);
    if (keyBuffer.length > 500) keyBuffer = keyBuffer.slice(-200);
  });

  cardSwipeDataInput.addEventListener('input', function() { if (!isCardSwipeActive()) return; const v = cardSwipeDataInput.value; if (v.length >= SWIPE_MIN_LENGTH && looksLikeSwipe(v)) onSwipeDetected(v); });
  cardSwipeDataInput.addEventListener('keydown', function(e) { if (e.key === 'Enter') e.preventDefault(); });
  console.log('[CardReader] USB card swipe reader detector initialized');
}

/* ---- Global keyboard (V1 parity) ----------------------------------------- */
function initializeGlobalKeyboard() {
  let keyboardOpen = false;
  function openKb(input) {
    if (keyboardOpen || input.disabled) return;
    if (input.id === 'totalNoPax' && input.hasAttribute('readonly')) return;
    if (input.id === 'cardSwipeData') return;
    keyboardOpen = true;
    const wasRO = input.hasAttribute('readonly');
    if (!wasRO) input.setAttribute('readonly', 'true');
    input.blur();
    setTimeout(() => {
      const curVal = (input.value || '').replace(/,/g, '');
      const isCash = input.id === 'cashReceived';
      const isNum = input.type === 'number' || isCash;
      let exactAmt = null;
      if (isCash) { const ft = document.getElementById('finalTotal'); if (ft) { const p = parseFloat(ft.textContent.replace(/,/g, '')); if (Number.isFinite(p) && p > 0) exactAmt = p.toFixed(2); } }
      const isPax = input.id === 'totalNoPax';
      let title = 'ENTER VALUE';
      if (!isPax) { const lbl = document.querySelector('label[for="' + input.id + '"]'); if (lbl && lbl.textContent.trim()) title = lbl.textContent.trim(); }
      openKeyboard(curVal, function(newVal) {
        if (isCash) { input.value = formatNumberWithCommas(newVal); } else { input.value = newVal; }
        if (!wasRO) input.removeAttribute('readonly');
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.blur();
        setTimeout(() => { keyboardOpen = false; }, 300);
      }, null, {
        layout: 'cash',
        displayLabel: isPax ? 'PAX / CUSTOMER' : (isCash ? 'RECEIVED' : title),
        sectionTitle: isPax ? 'QUICK PAX' : 'QUICK CASH',
        presetMode: isPax ? 'set' : 'add',
        emptyDisplay: isPax ? '0' : '0.00',
        integerOnly: isPax,
        cashPresets: isPax ? ['1','2','3','4','5','6'] : ['100','500','1000','1500','2000','5000'],
        exactAmount: exactAmt,
        onCancel() {
          if (!wasRO) input.removeAttribute('readonly');
          input.blur();
          setTimeout(() => { keyboardOpen = false; }, 300);
        }
      });
    }, 50);
  }
  document.addEventListener('click', function(e) {
    const target = e.target;
    if (target.tagName !== 'INPUT' || target.type === 'radio' || target.type === 'checkbox' || target.type === 'hidden') return;
    if (keyboardOpen) return;
    /* Only open keyboard for amount/PAX inputs — skip text fields */
    const keyboardIds = ['cashReceived', 'totalNoPax', 'giftCheckAmount', 'chequeAmount'];
    if (!keyboardIds.includes(target.id)) return;
    e.preventDefault();
    openKb(target);
  }, true);
  console.log('[Keyboard] Global keyboard initialized');
}

/* ---- Draft restore (V1 parity) ------------------------------------------- */
function restoreSettlementDraftFromLocalStorage() {
  try {
    const raw = localStorage.getItem(getSettlementDraftStorageKey());
    if (!raw) return false;
    const draft = JSON.parse(raw);
    if (!draft || typeof draft !== 'object') return false;
    if (draft.totalNoPax) { const f = document.getElementById('totalNoPax'); if (f) f.value = draft.totalNoPax; }
    if (draft.selectedDiscounts && typeof draft.selectedDiscounts === 'object') selectedDiscounts = { ...draft.selectedDiscounts };
    const odField = document.getElementById('orderDiscount');
    if (odField) odField.value = draft.orderDiscount || 'no_discount';
    const pmVal = draft.paymentMethod || 'cash';
    const pmRadio = document.querySelector(`input[name="payment_method"][value="${pmVal}"]`);
    if (pmRadio) { pmRadio.checked = true; pmRadio.dispatchEvent(new Event('change', { bubbles: true })); }
    const ctField = document.getElementById('cardType');
    if (ctField) { ctField.value = draft.cardType || 'gcash'; ctField.dispatchEvent(new Event('change', { bubbles: true })); }
    const crField = document.getElementById('cashReceived');
    if (crField && draft.cashReceived) crField.value = formatNumberWithCommas(draft.cashReceived);
    if (document.getElementById('giftCheckAmount')) document.getElementById('giftCheckAmount').value = draft.giftCheckAmount || '';
    if (document.getElementById('giftCheckNumber')) document.getElementById('giftCheckNumber').value = draft.giftCheckNumber || '';
    if (document.getElementById('chequeAmount')) document.getElementById('chequeAmount').value = draft.chequeAmount || '';
    if (document.getElementById('chequeNumber')) document.getElementById('chequeNumber').value = draft.chequeNumber || '';
    if (document.getElementById('remarks')) document.getElementById('remarks').value = draft.remarks || '';
    if (Object.keys(selectedDiscounts || {}).filter(k => k !== 'regular_mode').length > 0) {
      renderSelectedDiscountBadges();
      renderSelectedDiscountMeta();
      updateSelectedDiscountsBoxVisibility();
      const beneficiaries = draft.discountBeneficiaries || {};
      Object.keys(beneficiaries).forEach(type => {
        const indexed = beneficiaries[type] || {};
        Object.keys(indexed).forEach(idxStr => {
          const idx = parseInt(idxStr, 10); if (!Number.isFinite(idx)) return;
          const pair = indexed[idxStr] || {};
          const nf = document.querySelector(`.discount-name-field[data-discount-type="${type}"][data-index="${idx}"]`);
          const idf = document.querySelector(`.discount-id-field[data-discount-type="${type}"][data-index="${idx}"]`);
          if (nf) nf.value = pair.name || '';
          if (idf) idf.value = pair.id || '';
        });
      });
    } else {
      updateSelectedDiscountsBoxVisibility();
    }
    calculateOrderDiscount();
    updateChangeAmount();
    return true;
  } catch (err) {
    console.warn('Failed to restore settlement draft:', err);
    return false;
  }
}

/* ---- Draft persistence (delegated listeners) ----------------------------- */
function initializeDraftPersistence() {
  document.addEventListener('input', function(e) {
    const ids = ['totalNoPax','cashReceived','giftCheckAmount','giftCheckNumber','chequeAmount','chequeNumber','remarks'];
    if (ids.includes(e.target.id)) { saveSettlementDraftToLocalStorage(); }
    if (e.target.id === 'totalNoPax') calculateOrderDiscount();
    if (e.target.id === 'cashReceived') {
      const cardRadio = document.getElementById('cardPayment');
      if (cardRadio && cardRadio.checked) {
        const ft = parseFloat(document.getElementById('finalTotal').textContent) || 0;
        e.target.value = ft.toFixed(2);
      }
      updateChangeAmount();
    }
    if (e.target.id === 'giftCheckAmount' || e.target.id === 'chequeAmount') { calculateOrderDiscount(); updateChangeAmount(); }
    if (e.target.classList.contains('discount-name-field') || e.target.classList.contains('discount-id-field')) saveSettlementDraftToLocalStorage();
    if (e.target.classList.contains('discount-value-field')) {
      const dt = e.target.dataset.discountType;
      if (dt) { selectedDiscounts[dt] = e.target.value; calculateOrderDiscount(); renderSelectedDiscountBadges(); saveSettlementDraftToLocalStorage(); }
    }
  });
  document.addEventListener('change', function(e) {
    if (e.target.name === 'payment_method' || e.target.id === 'cardType') saveSettlementDraftToLocalStorage();
  });
  const paxField = document.getElementById('totalNoPax');
  if (paxField) paxField.addEventListener('change', () => calculateOrderDiscount());

  /* Delegated click handler for discount detail stepper +/- and X remove */
  document.addEventListener('click', function(e) {
    /* Mode toggle (₱ / %) for Regular Discount in discounts column */
    const modeBtn = e.target.closest('.stl-disc-mode-btn');
    if (modeBtn) {
      const mode = modeBtn.dataset.mode;
      selectedDiscounts['regular_mode'] = mode;
      const btns = modeBtn.parentElement.querySelectorAll('.stl-disc-mode-btn');
      btns.forEach(b => b.classList.toggle('active', b === modeBtn));
      calculateOrderDiscount();
      renderSelectedDiscountBadges();
      renderSelectedDiscountMeta();
      saveSettlementDraftToLocalStorage();
      return;
    }

    const stepBtn = e.target.closest('.stl-disc-det-step');
    if (stepBtn) {
      const dt = stepBtn.dataset.discountType;
      const action = stepBtn.dataset.action;
      const current = parseInt(selectedDiscounts[dt], 10) || 1;
      if (action === 'inc') {
        selectedDiscounts[dt] = current + 1;
      } else if (action === 'dec' && current > 1) {
        selectedDiscounts[dt] = current - 1;
      }
      const types = Object.keys(selectedDiscounts).filter(k => k !== 'regular_mode').sort();
      document.getElementById('orderDiscount').value = types.length === 1 ? types[0] : types.join(',');
      updateSelectedDiscountsBoxVisibility();
      renderSelectedDiscountMeta();
      calculateOrderDiscount();
      saveSettlementDraftToLocalStorage();
      return;
    }
    const removeBtn = e.target.closest('.stl-disc-det-remove');
    if (removeBtn) {
      const dt = removeBtn.dataset.discountType;
      delete selectedDiscounts[dt];
      const types = Object.keys(selectedDiscounts).filter(k => k !== 'regular_mode').sort();
      document.getElementById('orderDiscount').value = types.length === 0 ? 'no_discount' : types.length === 1 ? types[0] : types.join(',');
      updateSelectedDiscountsBoxVisibility();
      renderSelectedDiscountMeta();
      calculateOrderDiscount();
      saveSettlementDraftToLocalStorage();
    }
  });
}

/* ---- Cash received editability helper ------------------------------------ */
function setCashReceivedEditable(isEditable) {
  const input = document.getElementById('cashReceived');
  if (!input) return;
  input.readOnly = !isEditable;
  input.style.backgroundColor = isEditable ? '' : 'var(--bg-subtle)';
  input.style.cursor = isEditable ? '' : 'not-allowed';
}
