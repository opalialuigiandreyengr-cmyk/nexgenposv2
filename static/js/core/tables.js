/**
 * core/tables.js — Global Table Templates & Design System
 * -----------------------------------------------------------------------------
 * Shared table SVG templates, capacity mappings, and color schemes.
 * Consumed by:
 *   - Live Orders (pages/orders.js)
 *   - Table Management (pages/table_management.js)
 *   - POS Register table selection modal (pages/pos.js)
 */

export const POS_TABLE_TEMPLATES = {
  square_small: {
    capacity: 2,
    svg: `<svg width="78" height="94" viewBox="0 0 200 240" xmlns="http://www.w3.org/2000/svg">
              <rect x="82" y="38" width="36" height="20" rx="6" fill="{COLOR}"/>
              <rect x="4" y="102" width="20" height="36" rx="6" fill="{COLOR}"/>
              <rect x="176" y="102" width="20" height="36" rx="6" fill="{COLOR}"/>
              <rect x="25" y="60" width="150" height="120" rx="24" fill="{COLOR}"/>
              <rect x="82" y="182" width="36" height="20" rx="6" fill="{COLOR}"/>
            </svg>`,
  },
  square_medium: {
    capacity: 4,
    svg: `<svg width="94" height="94" viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg">
              <rect x="100" y="26" width="40" height="22" rx="7" fill="{COLOR}"/>
              <rect x="100" y="192" width="40" height="22" rx="7" fill="{COLOR}"/>
              <rect x="26" y="100" width="22" height="40" rx="7" fill="{COLOR}"/>
              <rect x="192" y="100" width="22" height="40" rx="7" fill="{COLOR}"/>
              <rect x="50" y="50" width="140" height="140" rx="24" fill="{COLOR}"/>
            </svg>`,
  },
  rectangle_large: {
    capacity: 6,
    svg: `<svg width="140" height="94" viewBox="0 0 360 240" xmlns="http://www.w3.org/2000/svg">
              <rect x="70" y="18" width="36" height="20" rx="6" fill="{COLOR}"/>
              <rect x="162" y="18" width="36" height="20" rx="6" fill="{COLOR}"/>
              <rect x="254" y="18" width="36" height="20" rx="6" fill="{COLOR}"/>
              <rect x="18" y="102" width="20" height="36" rx="6" fill="{COLOR}"/>
              <rect x="322" y="102" width="20" height="36" rx="6" fill="{COLOR}"/>
              <rect x="70" y="202" width="36" height="20" rx="6" fill="{COLOR}"/>
              <rect x="162" y="202" width="36" height="20" rx="6" fill="{COLOR}"/>
              <rect x="254" y="202" width="36" height="20" rx="6" fill="{COLOR}"/>
              <rect x="40" y="40" width="280" height="160" rx="20" fill="{COLOR}"/>
            </svg>`,
  },
  circle_medium: {
    capacity: 4,
    svg: `<svg width="94" height="94" viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg">
              <rect x="94" y="24" width="52" height="24" rx="8" fill="{COLOR}"/>
              <rect x="94" y="192" width="52" height="24" rx="8" fill="{COLOR}"/>
              <rect x="24" y="94" width="24" height="52" rx="8" fill="{COLOR}"/>
              <rect x="192" y="94" width="24" height="52" rx="8" fill="{COLOR}"/>
              <circle cx="120" cy="120" r="70" fill="{COLOR}"/>
            </svg>`,
  },
};

export const TABLE_SIZE_BY_TYPE = {
  square_small: 2,
  square_medium: 4,
  circle_medium: 4,
  rectangle_large: 6,
};

export const TABLE_COLOR_BY_SIZE = {
  2: '#D4AF37',   // 2 Pax - Gold
  4: '#f7922eff', // 4 Pax - Amber / Orange
  6: '#8B95A1',   // 6 Pax - Slate / Steel
};

export function getTableColor(tableType, customColor) {
  if (customColor) return customColor;
  const size = TABLE_SIZE_BY_TYPE[tableType] || 2;
  return TABLE_COLOR_BY_SIZE[size] || '#6FA8FF';
}

export function getTableSvg(tableType, colorOverride) {
  const template = POS_TABLE_TEMPLATES[tableType] || POS_TABLE_TEMPLATES.square_small;
  const color = getTableColor(tableType, colorOverride);
  return template.svg.replace(/{COLOR}/g, color);
}

export default {
  POS_TABLE_TEMPLATES,
  TABLE_SIZE_BY_TYPE,
  TABLE_COLOR_BY_SIZE,
  getTableColor,
  getTableSvg,
};
