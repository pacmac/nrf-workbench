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

// What a started command is doing, in words a person reads in the header. Anything not named here shows its own
// command name, so a new command is visible the day it lands, with no edit here.
const WORKING = {
  "programmer.flash": (a) => `Flashing ${a.serial || "a board"}`,
  "programmer.flash_stream": (a) => `Uploading and flashing ${a.serial || "a board"}`,
  "firmware.put": () => "Uploading a package",
  "firmware.delete": () => "Deleting a package",
  "firmware.move": () => "Moving a package",
  "firmware.project_new": () => "Creating a project",
  "firmware.project_delete": () => "Deleting a project",
};

export function workingText(run) {
  const f = WORKING[run.command];
  return f ? f(run.args || {}) : `Running ${run.command}`;
}

// The service's project-name rule (docs/specs/firmware.md, "projects"): 1 to 64 characters from a-z 0-9 . _ - and
// the first a letter or a digit. The service checks it too and is the authority; this only saves a round trip
// for a name that is certain to be refused. 64 is the service's own bound, not a choice made here.
export const PROJECT_NAME = /^[a-z0-9][a-z0-9._-]{0,63}$/;
// What is wrong with a name, in the words a person needs, or "" if nothing is. Names are lower-cased as they are typed
// (the page does it, the service still checks), so a capital letter never reaches here; the rest is said, not silently greyed out.
export function slugProblem(text) {
  if (!text) return "";
  if (text.length > 64) return "That is more than 64 characters.";
  if (!/^[a-z0-9]/.test(text)) return "It has to start with a letter or a digit.";
  const bad = text.match(/[^a-z0-9._-]/);
  if (!bad) return "";
  return bad[0] === " "
    ? "Spaces aren't allowed. Use a hyphen or an underscore instead, like my-name."
    : `“${bad[0]}” isn't allowed. Use letters, digits, dot, underscore or hyphen.`;
}
export const PROJECT_RULE_TEXT ="1 to 64 characters: lower-case letters, digits, dot, underscore or hyphen, starting with a letter or a digit";
export const DEFAULT_PROJECT = "unsorted";

export function flashBoundS(imageBytes) {
  return Math.ceil((FLASH_BOUND_FACTOR * imageBytes) / THROUGHPUT_SLOWEST_BPS * 10) / 10;   // one decimal, rounded up
}

// An ESP32 write is judged by esptool itself, so the bound is a give-up ceiling for the whole run (docs/specs/esp32.md).
// The wire time of the raw bytes is bytes x 10 bits / baud (start, 8 data, stop); esptool sends the image compressed, which is
// faster, so twice the raw time is generous. ESP_CONNECT_S is the time to reach the chip: esptool tries to connect 7 times by
// default, about 1.5 s each when the chip is silent, so 7 x 1.5 = 10.5 s, plus 1.5 s for the hash check and the reset.
// [unknown]: none of this has been timed on this bench, because no ESP32 is attached to it yet.
export const ESP_BAUDS = [115200, 230400, 460800, 921600];
export const ESP_CONNECT_S = 12;
export const ESP_BOUND_FACTOR = 2;

export function espFlashBoundS(imageBytes, baud) {
  return Math.ceil(ESP_BOUND_FACTOR * (imageBytes * 10) / baud + ESP_CONNECT_S);
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
