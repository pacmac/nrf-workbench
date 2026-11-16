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
export default function Band({ feed, boards, boardsAt, unavailable, ticker, onReconnect, onFlash }) {
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
        <p className="text-sm">No board attached. <span className="text-slate-600 dark:text-slate-400">As of {clock(boardsAt)}.</span></p>
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
