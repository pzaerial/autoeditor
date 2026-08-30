// ---------------------------------------------------------------- utils

export const $ = (id) => document.getElementById(id);
export const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
export const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

export function fmt(seconds) {
  if (!isFinite(seconds) || seconds < 0) seconds = 0;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export function fmtPrecise(seconds) {
  const base = fmt(seconds);
  const frac = Math.round((seconds % 1) * 10);
  return frac ? `${base}.${frac}` : base;
}

/** Timecode with enough decimals to be useful at the current zoom. */
export function fmtTick(seconds, span) {
  if (span > 120) return fmt(seconds);
  const base = fmt(Math.floor(seconds));
  const digits = span > 12 ? 1 : 2;
  const frac = (seconds % 1).toFixed(digits).slice(1);
  return base + frac;
}

export function parseTime(text) {
  const parts = String(text).trim().split(":");
  if (parts.some((p) => p === "" || isNaN(Number(p)))) return null;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
}

export function toast(message, bad) {
  const node = $("toast");
  node.textContent = message;
  node.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => (node.className = "toast"), 3200);
}

export async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({ error: "bad response" }));
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

export const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const mediaUrl = (path) => "/media?path=" + encodeURIComponent(path);
