// 인라인 SVG 아이콘 (PRD 6-5): 선 2px, 둥근 끝, 18px, 색은 currentColor. 이모지·특수 문자는 쓰지 않는다.

const PATHS = {
  aed: '<path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/><path d="M12.8 9.5 10.6 13h2.8l-2.2 3.5"/>',
  pin: '<path d="M12 21s-6.5-5.6-6.5-11.2a6.5 6.5 0 0 1 13 0C18.5 15.4 12 21 12 21z"/><circle cx="12" cy="9.8" r="2.3"/>',
  locate: '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
  layers: '<path d="M12 3 3 8l9 5 9-5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m16.5 16.5 4 4"/>',
  stats: '<path d="M4 20V11M10 20V5M16 20v-6M3 20h18"/>',
  warning: '<path d="M10.3 4.2 2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z"/><path d="M12 10v4M12 17.5h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  walk: '<circle cx="13" cy="4.5" r="1.5"/><path d="m9 21 2.5-6.5L14 17v4M8 11l3-3.5 3 2.5 3 1M11.5 14.5 12.5 9"/>',
  chevron: '<path d="m6 15 6-6 6 6"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7"/>',
};

export function icon(name, { size = 18, label } = {}) {
  const body = PATHS[name] || "";
  const a11y = label ? `role="img" aria-label="${label}"` : 'aria-hidden="true" focusable="false"';
  return (
    `<svg ${a11y} width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" ` +
    `stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`
  );
}

/** HTML의 <span data-icon="이름"></span>을 SVG로 채운다. */
export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    el.classList.add("icon");
    el.innerHTML = icon(el.dataset.icon);
  });
}
