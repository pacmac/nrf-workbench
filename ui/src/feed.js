// The live feed: ONE websocket, /ws/events, carries everything (docs/specs/dashboard.md, "no polling").
//
// There is no timer anywhere here. The client sends `want` with the cursor and epoch it last saw per feed, and the
// server sends pages (events, lost, reset, latest, epoch) and pushes more as they happen. On a reconnect the
// same `want` resumes from the cursors, so nothing is lost or repeated; if the service restarted the epoch
// differs and the page says `reset`; if the cursor is older than what is retained the page says how many were
// `lost`. Reconnecting is event-driven: once at once after a live feed drops, and otherwise only on the browser's
// own `online` and `visibilitychange` events or a button. Never a retry loop on a clock.

const STATES = ["connecting", "live", "dropped", "unauthorized", "unavailable"];

export function feedUrl() {
  const u = new URL("ws/events", document.baseURI);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  return u.toString();
}

// `from` is {feed: {cursor, epoch}} for a feed the page has already caught up to by another read (the meter's profile), so the feed carries on from exactly there.
export function createFeed({ feeds, onPage, onState, from }) {
  const cursors = new Map(feeds.map((name) => [name, { cursor: (from && from[name] && from[name].cursor) || 0, epoch: (from && from[name] && from[name].epoch) || null }]));
  let socket = null;
  let wasLive = false;
  let closed = false;

  function say(state, detail) {
    onState({ state, at: Date.now(), detail: detail || "" });
  }

  function connect() {
    if (closed || (socket && (socket.readyState === 0 || socket.readyState === 1))) return;
    say("connecting");
    wasLive = false;
    let ws;
    try {
      ws = new WebSocket(feedUrl());
    } catch (e) {
      say("unavailable", String(e));
      return;
    }
    socket = ws;
    ws.onopen = () => {
      wasLive = true;
      say("live");
      const want = {};
      cursors.forEach((v, k) => { want[k] = { cursor: v.cursor, epoch: v.epoch }; });
      ws.send(JSON.stringify({ want }));
    };
    ws.onmessage = (message) => {
      let m;
      try { m = JSON.parse(message.data); } catch (e) { return; }
      if (m.type === "events" && cursors.has(m.feed)) {
        cursors.set(m.feed, { cursor: m.page.latest, epoch: m.page.epoch });
        onPage(m.feed, m.page);
      } else if (m.type === "error") {
        say("live", m.message);
      }
    };
    ws.onclose = (event) => {
      if (socket !== ws) return;
      socket = null;
      if (closed) return;
      if (event.code === 4401) { say("unauthorized", "the feed needs a login"); return; }
      if (event.code === 4403) { say("unauthorized", "the feed refused this page's origin"); return; }
      if (wasLive) { say("dropped"); connect(); return; }     // it was live: try once, at once. No loop.
      say("dropped", "could not connect; waiting for the browser to come back online, or press Reconnect");
    };
    ws.onerror = () => {};                                      // the close event that follows says what happened
  }

  const resume = () => {
    if (!socket) connect();
  };
  const onVisible = () => {
    if (document.visibilityState === "visible") resume();
  };
  window.addEventListener("online", resume);
  document.addEventListener("visibilitychange", onVisible);

  return {
    start: connect,
    reconnect: resume,
    stop() {
      closed = true;
      window.removeEventListener("online", resume);
      document.removeEventListener("visibilitychange", onVisible);
      if (socket) socket.close();
    },
  };
}

export { STATES };
