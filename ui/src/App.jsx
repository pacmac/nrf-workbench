import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { loadSession, logout, readJson } from "./api.js";
import { createFeed } from "./feed.js";
import { TICKER_LINES, benchName } from "./format.js";
import { applyTheme, currentTheme, nextTheme } from "./theme.js";
import { Button, Lamp, NavContext, cx } from "./ui.jsx";
import Login from "./Login.jsx";
import Working from "./Working.jsx";
import Home from "./Home.jsx";
import Help from "./Help.jsx";
import { useOverview } from "./overview.js";
import Devices from "./Devices.jsx";
import Firmware from "./Firmware.jsx";
import Activity from "./Activity.jsx";
import Commands from "./Commands.jsx";
import Console from "./Console.jsx";
import Gpio from "./Gpio.jsx";
import Wiring from "./wiring/Wiring.jsx";
import FlashDialog from "./FlashDialog.jsx";
import { addLines } from "./consoleLog.js";

// The daily pages first, the reference last. The nav is one row that scrolls sideways, so it never wraps on a phone.
const PAGES = [["home", "Home"], ["devices", "Devices"], ["wiring", "Wiring"], ["console", "Console"], ["firmware", "Firmware"], ["gpio", "GPIO"], ["activity", "Activity"], ["commands", "Commands"], ["help", "Help"]];

// How many activity rows the browser keeps in memory. Same derivation as the draw limit: the server's retention
// is the real bound, this only stops a tab that stays open for weeks from growing without limit. 5 x the
// rows drawn at first (5 x 100) keeps "show more" useful for several screens.
const KEEP_ROWS = 500;

function tickerText(event) {
  const p = event.payload;
  if (event.type === "devices.changed") {
    // An event that carries `usb_changes` says what was plugged or unplugged; one with none is a board's state or an adapter's
    // port appearing, which the lists below show, so it adds no line.
    if (Array.isArray(p.usb_changes)) {
      if (p.usb_changes.length === 0 && event.payload.reason !== "start") return null;
      if (p.usb_changes.length > 0) {
        return p.usb_changes.map((c) => `${c.change}: ${c.product || c.id} (${c.id}, USB port ${c.slot})`).join("; ");
      }
    }
    const n = p.boards.length;
    const what = n === 0 ? "no board attached" : p.boards.map((b) => `${b.name ? `${b.name} ` : ""}${b.serial} ${b.mode}`).join(", ");
    return `attached: ${what}`;
  }
  if (event.type === "devices.live_unavailable") return `live updates unavailable: ${p.reason}`;
  if (event.type === "devices.read_failed") return `could not read the devices: ${p.error}`;
  return event.type;
}

export default function App() {
  const name = useMemo(benchName, []);
  const [theme, setTheme] = useState(currentTheme);
  const [session, setSession] = useState(null);
  const [expired, setExpired] = useState(false);
  const [page, setPage] = useState("home");
  const [topic, setTopic] = useState("start");                   // the Help topic that is open
  const [helpDirect, setHelpDirect] = useState(false);          // a ? opens its topic itself; the Help tab opens the list of topics
  const go = useCallback((id) => { setHelpDirect(false); setPage(id); window.scrollTo(0, 0); }, []);
  const openHelp = useCallback((id) => { setTopic(id || "start"); setHelpDirect(true); setPage("help"); window.scrollTo(0, 0); }, []);
  const nav = useMemo(() => ({ go, help: openHelp }), [go, openHelp]);
  const tabs = useRef(null);

  const [feed, setFeed] = useState({ state: "connecting", at: null, detail: "" });
  const [boards, setBoards] = useState(null);
  const [boardsAt, setBoardsAt] = useState(null);
  const [unavailable, setUnavailable] = useState("");
  const [ticker, setTicker] = useState([]);
  const [activity, setActivity] = useState({ rows: [], lost: 0, reset: false });
  const [running, setRunning] = useState([]);                    // started and not yet ended, from the activity feed
  const [info, setInfo] = useState({ commands: null, capabilities: null, streams: null, status: null });
  const [links, setLinks] = useState(null);                    // the declared console links with their state, or null before they load
  const [consoleLines, setConsoleLines] = useState({});         // {link: [line]}, from the console feed
  const [consoleFeed, setConsoleFeed] = useState({ state: "connecting", at: null, detail: "" });
  const [linksTick, setLinksTick] = useState(0);                // bumped when a link's state changes, to read the links again
  const [usbTick, setUsbTick] = useState(0);                    // bumped on every USB change the service reports (plugged, unplugged, a port appearing)
  const [flashing, setFlashing] = useState(null);
  const [flashInitial, setFlashInitial] = useState(null);        // a package chosen on the Firmware page, or none
  const openFlash = useCallback((board, initial = null) => { setFlashInitial(initial); setFlashing(board); }, []);
  const feedRef = useRef(null);

  const refreshSession = useCallback(async () => {
    try {
      const r = await loadSession();
      if (r.ok) setSession(r.body);
      else setSession({ login_required: true, authenticated: false, caller: null, access: "token", unreachable: r.status });
    } catch (e) {
      setSession({ login_required: true, authenticated: false, caller: null, access: "token", unreachable: 0 });
    }
  }, []);

  useEffect(() => { refreshSession(); }, [refreshSession]);

  const onPage = useCallback((feedName, p) => {
    if (feedName === "devices") {
      if (p.events.length) {
        const real = p.events.filter((e) => e.type === "devices.changed");
        if (real.length) {
          setBoards(real[real.length - 1].payload.boards);
          setBoardsAt(real[real.length - 1].wall);
          setUsbTick((n) => n + 1);                       // something was plugged or unplugged: the lists that read USB read it again
        }
        const gone = p.events.find((e) => e.type === "devices.live_unavailable");
        if (gone) setUnavailable(gone.payload.reason);
        setTicker((old) => [...p.events.map((e) => ({ key: `${p.epoch}-${e.cursor}`, wall: e.wall, text: tickerText(e) })).filter((t) => t.text).reverse(), ...old].slice(0, TICKER_LINES));
      }
    } else if (feedName === "activity") {
      // The feed carries two kinds: a command that STARTED and the result that ENDS it, joined by `run`. The
      // page lists the results; what is running is the starts that have no result yet. A new epoch (a service
      // restart) replays from the start, so it clears what was running.
      const results = p.events.filter((e) => e.type === "command.result");
      setActivity((old) => {
        const base = p.reset ? [] : old.rows;
        const rows = base.concat(results).slice(-KEEP_ROWS);
        return { rows, lost: p.reset ? 0 : (old.lost || 0) + (p.lost || 0), reset: p.reset || old.reset };
      });
      setRunning((old) => {
        let now = p.reset ? [] : old;
        for (const e of p.events) {
          if (e.type === "command.started") {
            now = now.concat({ run: e.payload.run, caller: e.payload.caller, command: e.payload.command, args: e.payload.args, wall: e.wall });
          } else if (e.type === "command.result" && e.payload.run != null) {
            now = now.filter((r) => r.run !== e.payload.run);
          }
        }
        return now;
      });
    }
  }, []);

  // The console has its own feed on its own socket, opened for the whole session when the service has the console, so
  // lines keep arriving while another page is open. A state change of a link says "read the links again".
  const onConsolePage = useCallback((feedName, p) => {
    if (feedName !== "console") return;
    setConsoleLines((old) => addLines(old, p.events, p.epoch, p.reset));
    if (p.events.some((e) => e.type === "console.link")) setLinksTick((n) => n + 1);
  }, []);

  const loadInfo = useCallback(async () => {
    const get = async (path) => { try { const r = await readJson(path); return r.ok ? r.body : null; } catch (e) { return null; } };
    const [commands, capabilities, streams, status] = await Promise.all([
      get("api/commands"), get("api/capabilities"), get("api/streams"), get("api/dashboard/status"),
    ]);
    setInfo({ commands: commands ? commands.commands : [], capabilities, streams, status });
  }, []);

  const authenticated = Boolean(session && session.authenticated);

  // The title says it too: the person watching is often in another tab. The service sets the bench name in the
  // title before any script runs, so the first value seen here is the base.
  const baseTitle = useRef(null);
  useEffect(() => {
    if (baseTitle.current === null) baseTitle.current = document.title;
    document.title = running.length > 0 ? `Working · ${baseTitle.current}` : baseTitle.current;
  }, [running]);

  useEffect(() => {
    if (!authenticated) return undefined;
    loadInfo();
    const f = createFeed({ feeds: ["devices", "activity"], onPage, onState: setFeed });
    feedRef.current = f;
    f.start();
    return () => { f.stop(); feedRef.current = null; };
  }, [authenticated, loadInfo, onPage]);

  const linksRow = (info.commands || []).find((c) => c.name === "console.links") || null;
  const consoleOn = Boolean(authenticated && linksRow && info.streams && info.streams.console);
  const linksPath = linksRow ? linksRow.path : null;
  const gpioOn = Boolean(authenticated && (info.commands || []).some((c) => c.name === "gpio.header"));   // only on a Pi with a pin-map folder

  useEffect(() => {
    if (!consoleOn) return undefined;
    const f = createFeed({ feeds: ["console"], onPage: onConsolePage, onState: setConsoleFeed });
    f.start();
    return () => f.stop();
  }, [consoleOn, onConsolePage]);

  useEffect(() => {
    if (!consoleOn || !linksPath) return undefined;
    let live = true;
    readJson(linksPath).then((r) => {
      if (live && r.ok && r.body && r.body.data) setLinks(r.body.data.links || []);
    }).catch(() => {});
    return () => { live = false; };
  }, [consoleOn, linksPath, linksTick]);

  // After any command, and after a name is saved (a file raises no kernel event), the bench is read again. Never on a timer.
  const [espTick, setEspTick] = useState(0);
  const overview = useOverview(authenticated ? info.commands : null, [usbTick, linksTick, espTick]);
  const refresh = useCallback(() => setEspTick((n) => n + 1), []);

  // A write that came back 401 means the session ended (expiry, restart, another login): show the login again.
  const onCommandDone = useCallback((r) => {
    setEspTick((n) => n + 1);
    if (r && r.status === 401) { setExpired(true); refreshSession(); }
  }, [refreshSession]);

  // The current tab is kept in view when the row of tabs is wider than the screen.
  useEffect(() => {
    const el = tabs.current && tabs.current.querySelector("[aria-current='page']");
    if (el && el.scrollIntoView) el.scrollIntoView({ inline: "center", block: "nearest" });
  }, [page, authenticated]);

  if (session === null) return <p role="status" className="p-6 text-sm text-slate-600 dark:text-slate-400">Loading…</p>;
  if (!authenticated) {
    return <Login name={name} expired={expired} onDone={() => { setExpired(false); refreshSession(); }} />;
  }

  const host = overview.data ? overview.data.host : null;
  const live = feed.state === "live";
  return (
    <NavContext.Provider value={nav}>
      <div className="mx-auto max-w-5xl px-4 pb-12">
        <header className="sticky top-0 z-20 -mx-4 bg-slate-100/95 px-4 pt-3 backdrop-blur dark:bg-slate-950/95">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold">{name}</h1>
            <div className="ml-auto flex items-center gap-2">
              <Button tone="quiet" onClick={() => setTheme(nextTheme(theme))} title="Light, dark, or follow the system" className="min-h-9! px-2!">Theme: {theme}</Button>
              {session.login_required && (
                <Button tone="quiet" className="min-h-9! px-2!" onClick={async () => { await logout(); setExpired(false); setBoards(null); setActivity({ rows: [], lost: 0, reset: false }); setRunning([]); setLinks(null); setConsoleLines({}); refreshSession(); }}>Log out</Button>
              )}
            </div>
          </div>
          <p className="mt-0.5 flex items-center gap-2 text-[13px] text-slate-600 dark:text-slate-400">
            <Lamp state={live ? "connected" : "attention"} size={12} />
            <span className="min-w-0 truncate">{host && host.model ? `${host.model}, ` : ""}{window.location.host}{live ? "" : `, live updates ${feed.state}`}</span>
            {(feed.state === "dropped" || feed.state === "unavailable") && (
              <button type="button" className="shrink-0 underline" onClick={() => feedRef.current && feedRef.current.reconnect()}>Reconnect</button>
            )}
          </p>
          <nav aria-label="Pages" ref={tabs} className="-mx-4 mt-2 flex gap-1 overflow-x-auto px-4 pb-2 [scrollbar-width:none]">
            {PAGES.filter(([id]) => (id !== "console" || consoleOn) && (id !== "gpio" || gpioOn)).map(([id, label]) => (
              <button
                key={id}
                onClick={() => go(id)}
                aria-current={page === id ? "page" : undefined}
                className={cx(
                  "min-h-11 shrink-0 rounded-md px-3 text-[15px] font-medium",
                  page === id
                    ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                    : "text-slate-700 hover:bg-slate-200 dark:text-slate-300 dark:hover:bg-slate-800",
                )}
              >
                {label}
              </button>
            ))}
          </nav>
        </header>

        <div className="mt-3 space-y-4">
          <Working running={running} feedLive={live} />
          {page === "home" && <Home overview={overview} session={session} feed={feed} activity={activity} info={info} />}
          {page === "devices" && <Devices overview={overview} commands={info.commands} onFlash={openFlash} onChanged={refresh} onDone={onCommandDone} />}
          {page === "firmware" && <Firmware commands={info.commands} boards={boards} onFlash={openFlash} onDone={onCommandDone} refreshKey={espTick + usbTick} />}
          {page === "wiring" && <Wiring commands={info.commands} refreshKey={linksTick + usbTick} onOpenPage={go} />}
          {page === "console" && consoleOn && <Console commands={info.commands} links={links} lines={consoleLines} activity={activity} feed={consoleFeed} onDone={onCommandDone} />}
          {page === "gpio" && gpioOn && <Gpio commands={info.commands} overview={overview} onDone={onCommandDone} />}
          {page === "activity" && <Activity activity={activity} />}
          {page === "commands" && <Commands commands={info.commands} overview={overview} onDone={onCommandDone} />}
          {page === "help" && <Help topic={topic} direct={helpDirect} onTopic={setTopic} onGo={go} />}
        </div>

        {flashing && <FlashDialog board={flashing} commands={info.commands || []} initial={flashInitial} onDone={onCommandDone} onClose={() => setFlashing(null)} />}
      </div>
    </NavContext.Provider>
  );
}

applyTheme(currentTheme());
