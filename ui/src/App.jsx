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
import Projects from "./Projects.jsx";
import Wiring from "./wiring/Wiring.jsx";
import FlashDialog from "./FlashDialog.jsx";
import MeterPage from "./MeterPage.jsx";
import { addLines } from "./consoleLog.js";

// The daily pages first, the reference last (docs/specs/navigation.md). On a phone the first DAILY pages this host has are the bar and the
// rest are under More; on a desktop all of them are a list down the left. Nothing scrolls sideways.
const PAGES = [["home", "Home"], ["devices", "Devices"], ["projects", "Projects"], ["console", "Console"], ["firmware", "Firmware"], ["wiring", "Wiring"], ["gpio", "GPIO"], ["meter", "Meter"], ["activity", "Activity"], ["commands", "Commands"], ["help", "Help"]];
const DAILY = 4;      // the bar holds four pages and More: five equal cells at 390 px are 78 px each, past the 44 px touch target
// Pages that own the window: from 768 px up they fill the width beside the nav and the whole height, and nothing on them scrolls (docs/specs/navigation.md, "Full-height pages").
// A phone keeps the page flow until a page has its own narrow layout. A page joins this list when it has been laid out to fit.
const FULL_PAGES = ["home", "meter", "activity", "console", "commands"];

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
  const [more, setMore] = useState(false);                       // the phone's list of the other pages is open
  const go = useCallback((id) => { setHelpDirect(false); setMore(false); setPage(id); window.scrollTo(0, 0); }, []);
  const openHelp = useCallback((id) => { setTopic(id || "start"); setHelpDirect(true); setMore(false); setPage("help"); window.scrollTo(0, 0); }, []);
  const nav = useMemo(() => ({ go, help: openHelp }), [go, openHelp]);

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
  const projectsOn = Boolean(authenticated && (info.commands || []).some((c) => c.name === "project.list"));   // needs the wiring folder
  const meterOn = Boolean(authenticated && (info.commands || []).some((c) => c.name === "ppk2.show"));        // needs the PPK2 in the configuration: the page is the power profiler's (the multimeter is no longer on it)

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

  // The phone's More list closes on Escape, as well as on choosing a page and on a second press of More.
  useEffect(() => {
    if (!more) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setMore(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [more]);

  if (session === null) return <p role="status" className="p-6 text-sm text-slate-600 dark:text-slate-400">Loading…</p>;
  if (!authenticated) {
    return <Login name={name} expired={expired} onDone={() => { setExpired(false); refreshSession(); }} />;
  }

  const host = overview.data ? overview.data.host : null;
  const live = feed.state === "live";
  const full = FULL_PAGES.includes(page);
  const pages = PAGES.filter(([id]) => (id !== "console" || consoleOn) && (id !== "gpio" || gpioOn) && (id !== "projects" || projectsOn) && (id !== "meter" || meterOn));
  const daily = pages.slice(0, DAILY);
  const others = pages.slice(DAILY);
  const underMore = others.some(([id]) => id === page);
  const item = (current, extra) => cx(
    "min-h-11 min-w-0 rounded-md px-1 text-[15px] font-medium md:w-full md:px-3 md:text-left",
    current ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900" : "text-slate-700 hover:bg-slate-200 dark:text-slate-300 dark:hover:bg-slate-800",
    extra,
  );
  const pageButton = ([id, label], extra) => (
    <button key={id} type="button" onClick={() => go(id)} aria-current={page === id ? "page" : undefined} className={item(page === id, extra)}>{label}</button>
  );
  const doLogout = async () => { await logout(); setExpired(false); setBoards(null); setActivity({ rows: [], lost: 0, reset: false }); setRunning([]); setLinks(null); setConsoleLines({}); refreshSession(); };
  return (
    <NavContext.Provider value={nav}>
      <div className={cx("px-4 md:flex md:gap-4", full ? "w-full" : "mx-auto max-w-6xl md:gap-6")}>
        {/* The sidebar on a desktop: the bench and its pages, always in view, so the page gets the whole height. On a phone this is a slim strip. */}
        <div className="md:sticky md:top-0 md:flex md:h-screen md:w-52 md:shrink-0 md:flex-col md:py-4">
          <header className="py-2 md:pb-4 md:pt-0">
            <div className="flex items-baseline gap-3 md:block">
              <h1 className="text-xl font-semibold">{name}</h1>
              <p className="flex min-w-0 flex-1 items-center gap-2 text-[13px] text-slate-600 dark:text-slate-400 md:mt-1 md:items-start">
                <Lamp state={live ? "connected" : "attention"} size={12} className="md:mt-1" />
                <span className="min-w-0 truncate md:whitespace-normal md:break-words">{host && host.model ? `${host.model}, ` : ""}{window.location.host}{live ? "" : `, live updates ${feed.state}`}</span>
              </p>
            </div>
            {(feed.state === "dropped" || feed.state === "unavailable") && (
              <button type="button" className="text-[13px] underline" onClick={() => feedRef.current && feedRef.current.reconnect()}>Reconnect</button>
            )}
          </header>
          {more && <div aria-hidden="true" className="fixed inset-0 z-30 bg-slate-900/30 dark:bg-black/50 md:hidden" onClick={() => setMore(false)} />}
          {/* DOM order is the reading and tab order on both layouts: the daily pages, then the rest. On a phone the column is reversed so the
              list opens ABOVE the bar. */}
          <nav aria-label="Pages" className="fixed inset-x-0 bottom-0 z-40 flex flex-col-reverse md:static md:min-h-0 md:flex-1 md:flex-col">
            <div className="flex gap-1 border-t border-slate-200 bg-white px-2 pb-[max(0.25rem,env(safe-area-inset-bottom))] pt-1 dark:border-slate-700 dark:bg-slate-900 md:flex-col md:border-0 md:bg-transparent md:p-0 md:pb-1 md:dark:bg-transparent">
              {daily.map((p) => pageButton(p, "flex-1 text-center"))}
              {others.length > 0 && (
                <button type="button" onClick={() => setMore(!more)} aria-expanded={more} aria-haspopup="true"
                  aria-current={underMore && !more ? "page" : undefined}
                  className={cx(item(underMore && !more, "flex-1 text-center"), "md:hidden")}>More</button>
              )}
            </div>
            <div className={cx(more ? "flex" : "hidden", "max-h-[70vh] flex-col gap-1 overflow-y-auto rounded-t-xl border-t border-slate-200 bg-white p-2 shadow-lg dark:border-slate-700 dark:bg-slate-900 md:flex md:max-h-none md:flex-1 md:rounded-none md:border-0 md:bg-transparent md:p-0 md:shadow-none md:dark:bg-transparent")}>
              {others.map((p) => pageButton(p, "px-3! text-left"))}
              <div className="mt-1 flex flex-col gap-1 border-t border-slate-200 pt-2 dark:border-slate-800 md:mt-auto">
                <button type="button" onClick={() => setTheme(nextTheme(theme))} title="Light, dark, or follow the system" className={item(false, "px-3! text-left")}>Theme: {theme}</button>
                {session.login_required && <button type="button" onClick={doLogout} className={item(false, "px-3! text-left")}>Log out</button>}
              </div>
            </div>
          </nav>
        </div>

        <main className={cx("mt-1 min-w-0 flex-1 pb-24 md:mt-0", full ? "md:flex md:h-dvh md:flex-col md:gap-2 md:overflow-hidden md:py-3" : "space-y-4 md:pb-12 md:pt-4")}>
          <Working running={running} feedLive={live} />
          {page === "home" && <Home overview={overview} session={session} feed={feed} activity={activity} info={info} />}
          {page === "devices" && <Devices overview={overview} commands={info.commands} onFlash={openFlash} onChanged={refresh} onDone={onCommandDone} />}
          {page === "projects" && projectsOn && <Projects commands={info.commands} refreshKey={espTick + usbTick + linksTick} onChanged={refresh} onDone={onCommandDone} />}
          {page === "firmware" && <Firmware commands={info.commands} boards={boards} onFlash={openFlash} onDone={onCommandDone} refreshKey={espTick + usbTick} />}
          {page === "wiring" && <Wiring commands={info.commands} refreshKey={linksTick + usbTick} onOpenPage={go} />}
          {page === "console" && consoleOn && <Console commands={info.commands} links={links} lines={consoleLines} activity={activity} feed={consoleFeed} onDone={onCommandDone} />}
          {page === "gpio" && gpioOn && <Gpio commands={info.commands} overview={overview} onDone={onCommandDone} />}
          {page === "meter" && meterOn && <MeterPage commands={info.commands} onDone={onCommandDone} />}
          {page === "activity" && <Activity activity={activity} />}
          {page === "commands" && <Commands commands={info.commands} overview={overview} onDone={onCommandDone} />}
          {page === "help" && <Help topic={topic} direct={helpDirect} onTopic={setTopic} onGo={go} />}
        </main>
      </div>
      {flashing && <FlashDialog board={flashing} commands={info.commands || []} initial={flashInitial} onDone={onCommandDone} onClose={() => setFlashing(null)} />}
    </NavContext.Provider>
  );
}

applyTheme(currentTheme());
