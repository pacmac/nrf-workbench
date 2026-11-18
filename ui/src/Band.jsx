import { Button, Card, Chip } from "./ui.jsx";
import { clock, describeBoards } from "./format.js";

// What the band says about the feed, and in what colour. A feed that is not live is never drawn as live.
const FEED = {
  connecting: ["slate", "connecting"],
  live: ["green", "live"],
  dropped: ["amber", "feed dropped"],
  unauthorized: ["red", "login needed"],
  unavailable: ["red", "unavailable"],
};

const MODE_TONE = { app: "green", bootloader: "amber" };

// The band is at the top of every page: everything attached, identified, updated by EVENT. It reports each
// board's state and mode as the service sees it. It does NOT say "ready": that verdict is a dry run of the flash
// guards, and no command exposes one yet.
// ESP32 boards are not in the device feed (a USB id does not say what is behind a bridge chip, so they are declared), so they are
// read from `esp32.targets` when the page loads, when the nRF list changes and after any command, and again on "Look again".
// A board plugged in later is not announced: it appears on the next of those, and the line says when it was last looked at.
function EspRows({ esp, onRefresh }) {
  const attached = esp.targets.filter((t) => t.attached > 0);
  const away = esp.targets.filter((t) => t.attached === 0);
  return (
    <div className="mt-2 border-t border-slate-200 pt-2 dark:border-slate-800">
      <ul className="divide-y divide-slate-200 dark:divide-slate-800" aria-label="ESP32 boards attached">
        {attached.map((t) => (
          <li key={t.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
            <Chip tone="green">ESP32</Chip>
            <span className="font-mono text-sm">{t.name}</span>
            <span className="text-sm text-slate-700 dark:text-slate-300">{t.product}</span>
            <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{t.node || t.problem}</span>
            {t.console && <Chip tone={t.console.state === "open" ? "green" : "amber"} title={t.console.reason}>console {t.console.state}</Chip>}
          </li>
        ))}
        {esp.native.map((n) => (
          <li key={n.serial} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
            <Chip tone="amber">ESP32, not declared</Chip>
            <span className="font-mono text-sm">{n.serial}</span>
            <span className="text-sm text-slate-700 dark:text-slate-300">{n.product}</span>
            <span className="text-xs text-slate-600 dark:text-slate-400">Declare it on the Firmware page to flash it.</span>
          </li>
        ))}
        {away.map((t) => (
          <li key={t.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-slate-600 dark:text-slate-400">
            <Chip>ESP32</Chip>
            <span className="font-mono text-sm">{t.name}</span>
            <span className="text-sm">not attached</span>
          </li>
        ))}
      </ul>
      <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-600 dark:text-slate-400">
        {esp.targets.length === 0 && esp.native.length === 0 ? "No ESP32 board is declared or plugged in, " : "ESP32 boards "}as of {clock(esp.at)}.
        <Button tone="quiet" onClick={onRefresh}>Look again</Button>
      </p>
    </div>
  );
}

export default function Band({ feed, boards, boardsAt, unavailable, ticker, esp, onEspRefresh, onReconnect, onFlash }) {
  const [tone, label] = FEED[feed.state] || FEED.connecting;
  return (
    <Card
      title="Attached"
      aside={
        <div className="flex items-center gap-2">
          <Chip tone={tone} title={feed.detail}>{label}</Chip>
          {feed.at && feed.state !== "live" && <span className="text-xs text-slate-600 dark:text-slate-400">since {clock(feed.at / 1000)}</span>}
          {(feed.state === "dropped" || feed.state === "unavailable") && <Button tone="quiet" onClick={onReconnect}>Reconnect</Button>}
        </div>
      }
    >
      {feed.detail && feed.state !== "live" && <p className="mb-2 text-sm text-amber-800 dark:text-amber-300">{feed.detail}</p>}
      {unavailable && (
        <p className="mb-2 text-sm text-amber-800 dark:text-amber-300">
          Live device updates are not available on this host ({unavailable}). The list below is as of {clock(boardsAt)}, not live.
        </p>
      )}
      {boards === null ? (
        <p className="text-sm text-slate-600 dark:text-slate-400">Waiting for the first report from the service.</p>
      ) : boards.length === 0 ? (
        <p className="text-sm">{esp ? "No nRF board attached." : "No board attached."} <span className="text-slate-600 dark:text-slate-400">As of {clock(boardsAt)}.</span></p>
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-800">
          {boards.map((b) => (
            <li key={`${b.serial}-${b.mode}-${b.sysfs}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
              <Chip tone={MODE_TONE[b.mode] || "slate"}>{b.mode}</Chip>
              {b.state !== b.mode && <Chip tone="red" title="the same board is attached in both modes at once">state: {b.state}</Chip>}
              <span className="font-mono text-sm">{b.serial}</span>
              <span className="text-sm text-slate-700 dark:text-slate-300">{b.product}</span>
              <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{b.id}</span>
              <Chip>{b.profile}</Chip>
              <Button tone="quiet" className="ml-auto" onClick={() => onFlash(b)}>Flash…</Button>
            </li>
          ))}
        </ul>
      )}
      {esp && <EspRows esp={esp} onRefresh={onEspRefresh} />}
      {ticker.length > 0 && (
        <div className="mt-3 border-t border-slate-200 pt-2 dark:border-slate-800">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-600 dark:text-slate-400">Latest changes</p>
          <ol className="space-y-0.5 text-xs">
            {ticker.map((t) => (
              <li key={t.key} className="flex gap-2">
                <span className="font-mono text-slate-600 dark:text-slate-400">{clock(t.wall)}</span>
                <span>{t.text}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </Card>
  );
}

export { describeBoards };
