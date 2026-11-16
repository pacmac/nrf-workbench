// Small helpers, and the two display limits, each derived.

// Rows of the activity table drawn at first. A screen at 1280x800 shows about 25 rows at 32 px each (800/32);
// four screens of history is 100, and "show more" adds another 100. The server's own retention is the real bound;
// this only limits how much is DRAWN at once.
export const ROWS_AT_FIRST = 100;
export const ROWS_PER_STEP = 100;

// Lines in the live ticker beside the band. It is a SHORT ticker (dashboard.md): one phone screen at 20 px
// a line is about 40, but the ticker shares the screen, so a quarter of that, 8, is enough to see one whole
// mode change (app, handover, bootloader, handover, app) and what came just before it.
export const TICKER_LINES = 8;

export function clock(epochSeconds) {
  if (epochSeconds == null) return "";
  return new Date(epochSeconds * 1000).toLocaleTimeString();
}

export function when(epochSeconds) {
  if (epochSeconds == null) return "";
  const d = new Date(epochSeconds * 1000);
  const today = new Date();
  return d.toDateString() === today.toDateString() ? d.toLocaleTimeString() : d.toLocaleString();
}

export function seconds(value) {
  if (value == null) return "";
  if (value < 1) return (value * 1000).toFixed(value < 0.01 ? 1 : 0) + " ms";   // 10 ms is where tenths stop meaning anything
  return value < 10 ? value.toFixed(2) + " s" : value.toFixed(1) + " s";
}

export function benchName() {
  const meta = document.querySelector('meta[name="bench-name"]');
  const name = meta ? meta.getAttribute("content") : "";
  return name && !name.includes("{{") ? name : "nrf-workbench";
}

export function describeBoards(boards) {
  if (!boards || boards.length === 0) return "no board attached";
  return boards.map((b) => `${b.mode}: ${b.product || b.id}`).join(", ");
}
