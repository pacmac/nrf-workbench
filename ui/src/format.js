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

// ---- the flash bounds (docs/specs/dashboard.md, "The flash flow") -------------------------------------------
// The service requires both bounds and has no default for either (programmer spec). The page derives them.
//
// Measured on the real RAK4631 over native USB: the 332,092-byte image took 21.554 s, and the throughput across
// the runs was 12.35 to 15.42 kB/s. Handover into the bootloader took 0.362 to 0.589 s.
export const THROUGHPUT_SLOWEST_BPS = 12350;     // bytes/s, the slowest measured run
export const THROUGHPUT_FASTEST_BPS = 15420;     // bytes/s, the fastest measured run
// 2x the slowest-throughput time: one slow USB host or a burst of packet acknowledgements. For 332,092 bytes:
// 2 x 332,092 / 12,350 = 53.8 s, which is 2.5x the 21.554 s measured (the programmer spec asks 2 to 3x).
export const FLASH_BOUND_FACTOR = 2;
// 3x the worst measured handover (3 x 0.589 = 1.77 s), rounded up to a whole second.
export const ENTER_BOUND_S = 2;

export function flashBoundS(imageBytes) {
  return Math.ceil((FLASH_BOUND_FACTOR * imageBytes) / THROUGHPUT_SLOWEST_BPS * 10) / 10;   // one decimal, rounded up
}

// What a person should expect to wait: the measured range, fastest to slowest.
export function expectedRangeS(imageBytes) {
  return [imageBytes / THROUGHPUT_FASTEST_BPS, imageBytes / THROUGHPUT_SLOWEST_BPS];
}

export function rangeText([lo, hi]) {
  if (hi < 1) return "under a second";
  return `about ${Math.max(1, Math.round(lo))} to ${Math.max(1, Math.round(hi))} s`;
}

export function bytesText(n) {
  return n == null ? "" : `${Number(n).toLocaleString("en-US")} bytes`;
}

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
