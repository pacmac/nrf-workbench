// What the Console page shows, as plain functions of the feeds (docs/specs/dashboard.md, "The Console page").
// No state and no timers here: the page passes in what the console feed and the activity feed delivered.

// Lines kept per link in the browser: 50 screens of 40 lines. At the service's 1 024-byte line cap that is 2 MB
// worst case. The service's retention is the real record (console.since); this only stops a tab left open for
// weeks from growing without limit.
export const KEEP_LINES = 2000;

// `old` is {link: [line]}. A page of console.line events extends it; a new epoch (the service restarted) replays
// from the start, so it clears first. A line is {key, wall, text, reply, partial}.
export function addLines(old, events, epoch, reset) {
  const next = reset ? {} : { ...old };
  for (const e of events) {
    if (e.type !== "console.line") continue;
    const p = e.payload;
    const line = { key: `${epoch}-${e.cursor}`, wall: e.wall, text: p.text, reply: Boolean(p.reply), partial: Boolean(p.partial) };
    next[p.link] = (next[p.link] || []).concat(line).slice(-KEEP_LINES);
  }
  return next;
}

// The activity rows that are sends on this link: {key, wall, text, cls, code, message, caller}. The row's wall is when
// the command ENDED; it started `duration_s` earlier, and that is when the bytes went, so a fast reply is drawn after
// the line that caused it and not before.
export function sendsOf(rows, link) {
  const out = [];
  for (const e of rows) {
    const p = e.payload;
    if (p.command !== "console.send" || !p.args || p.args.link !== link || typeof p.args.text !== "string") continue;
    out.push({
      key: `tx-${e.cursor}`, wall: e.wall - (p.duration_s || 0), text: p.args.text,
      cls: p.class, code: p.code, message: p.message, caller: p.caller,
    });
  }
  return out;
}

// What was said on the link, in order: the device's lines and the lines sent to it. Both carry the service's clock.
export function conversation(lines, sends) {
  const all = lines.map((l) => ({ ...l, kind: "rx" })).concat(sends.map((s) => ({ ...s, kind: "tx" })));
  return all.map((x, i) => [x, i]).sort((a, b) => (a[0].wall - b[0].wall) || (a[1] - b[1])).map((x) => x[0]);
}

// Up-arrow history: the texts sent on this link, newest first, whoever sent them and whatever the service answered
// (a shell recalls a refused command too), consecutive repeats collapsed.
export function historyOf(sends) {
  const out = [];
  for (let i = sends.length - 1; i >= 0; i--) {
    if (sends[i].text === "") continue;                  // a bare Enter is not worth recalling
    if (out.length === 0 || out[out.length - 1] !== sends[i].text) out.push(sends[i].text);
  }
  return out;
}

// Walk the history. `at` is -1 for the line being typed, else an index into `history` (0 = newest).
// Returns the new index, or null when the key does nothing (nothing older, or already at the typed line).
export function walk(history, at, key) {
  if (key === "ArrowUp") return at + 1 < history.length ? at + 1 : null;
  if (key === "ArrowDown") return at >= 0 ? at - 1 : null;
  return null;
}

// Why the input is off, in the service's own words, or "" when a send may be tried. This mirrors the service's
// refusals as a courtesy; the service makes them again, and the page shows what it answers.
export function cannotSend(link) {
  if (!link) return "No link is selected.";
  if (link.state === "unusable") return `This link cannot be used: ${link.reason || "see its problems"}.`;
  if (link.state !== "open") return `The adapter is ${link.state}${link.reason ? `: ${link.reason}` : ""}.`;
  if (link.rx_only) return "This link is receive only: nothing is wired from the bench to the device, so a send is refused (link_rx_only).";
  if (!link.voltage || !link.voltage.verified) return "The voltage of this link has not been verified, so a send is refused (voltage_unverified).";
  return "";
}
