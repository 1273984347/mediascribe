// 主题：月之暗面(dark) / 月之亮面(light)。
// head 内的防闪白脚本已设好初始值，这里只负责切换与持久化。
import { $ } from './dom.js';

const themeBtn = $("theme-toggle");

const ICONS = {
  sun:
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/></svg>',
  moon:
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>',
  play:
    '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.54.84l10-6.5a1 1 0 0 0 0-1.68l-10-6.5A1 1 0 0 0 8 5.5Z"/></svg>',
};

function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  themeBtn.innerHTML =
    t === "light" ? ICONS.moon + "<span>暗面</span>" : ICONS.sun + "<span>亮面</span>";
  themeBtn.title = t === "light" ? "切换到月之暗面" : "切换到月之亮面";
}

let theme = null;

try { theme = localStorage.getItem("ms_theme"); } catch (_) { /* storage 不可用 */ }

if (!theme) theme = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";

applyTheme(theme);

themeBtn.addEventListener("click", () => {
  theme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(theme);
  try { localStorage.setItem("ms_theme", theme); } catch (_) { /* storage 不可用 */ }
});

export { ICONS, applyTheme, theme };
