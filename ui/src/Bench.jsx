import { Card, Chip, Json } from "./ui.jsx";

function isLoopback(host) {
  return host === "localhost" || host === "127.0.0.1" || host === "[::1]" || host === "::1";
}

// The Bench page: what this service is, what it can do here, and what it will say plainly about its exposure.
export default function Bench({ name, session, info, feed }) {
  const { commands, capabilities, streams, status } = info;
  const plain = window.location.protocol === "http:" && !isLoopback(window.location.hostname);
  const absent = capabilities ? capabilities.absent : [];
  return (
    <div className="space-y-4">
      <Card title={name}>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-slate-600 dark:text-slate-400">Address</dt>
          <dd className="break-all font-mono">{window.location.host}</dd>
          <dt className="text-slate-600 dark:text-slate-400">Access</dt>
          <dd>{session.access === "open" ? "open: no token on this network" : session.access === "open-read" ? "reads are open, writes need the token" : "everything needs the token"}</dd>
          <dt className="text-slate-600 dark:text-slate-400">You are</dt>
          <dd>{session.caller || "not logged in"}</dd>
          <dt className="text-slate-600 dark:text-slate-400">Live feed</dt>
          <dd>{feed.state}{feed.detail ? `: ${feed.detail}` : ""}</dd>
        </dl>
        {plain && session.access === "open" && (
          <p role="note" className="mt-3 rounded-lg bg-amber-100 p-3 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
            <strong>Open to this network.</strong> Anyone who can reach this address can flash, reset and send to the boards. Use it only on a network you trust.
          </p>
        )}
        {plain && session.access !== "open" && (
          <p role="note" className="mt-3 rounded-lg bg-amber-100 p-3 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
            <strong>Unencrypted.</strong> This page is served over plain HTTP beyond this machine, so the token and the session cookie
            cross the network in clear text. Use it only on a network you trust.
          </p>
        )}
        {session.access === "open-read" && (
          <p role="note" className="mt-3 rounded-lg bg-amber-100 p-3 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
            <strong>Read access is open on this network.</strong> Anyone who can reach this address can see what is attached and what has been done.
          </p>
        )}
      </Card>

      <Card title="What this host can do">
        {capabilities ? (
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap gap-1.5">
              {capabilities.present.map((c) => <Chip key={c} tone="green">{c}</Chip>)}
              {capabilities.present.length === 0 && <span className="text-slate-600 dark:text-slate-400">nothing detected</span>}
            </div>
            {absent.length > 0 ? (
              <div>
                <p className="mb-1 font-medium">Absent here, and why</p>
                <ul className="space-y-1">
                  {absent.map((a) => (
                    <li key={a.name}><span className="font-mono">{a.name}</span> needs {a.unmet.map((u) => <Chip key={u} tone="amber">{u}</Chip>)}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-slate-600 dark:text-slate-400">Every command this service knows is available here.</p>
            )}
            <p className="text-slate-600 dark:text-slate-400">{commands.length} commands are present.</p>
          </div>
        ) : (
          <p className="text-sm text-slate-600 dark:text-slate-400">Loading…</p>
        )}
      </Card>

      <Card title="Live streams">
        {streams ? (
          <ul className="space-y-1 text-sm">
            {["events", "activity"].map((s) => (
              <li key={s} className="flex flex-wrap items-center gap-2">
                <Chip tone={streams[s] ? "green" : "amber"}>{streams[s] ? "on" : "off"}</Chip>
                <span className="font-medium">{s === "events" ? "device events" : "activity log"}</span>
                {!streams[s] && streams.absent[s] && <span className="text-slate-600 dark:text-slate-400">{streams.absent[s]}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-600 dark:text-slate-400">Loading…</p>
        )}
        <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">History is kept in the service's memory, so a restart empties it.</p>
      </Card>

      <Card title="What this dashboard can write">
        {status ? (
          <div className="space-y-2 text-sm">
            <ul className="list-disc pl-5">
              {status.write_routes.map((r) => <li key={r} className="font-mono">{r}</li>)}
              {status.write_routes.length === 0 && <li>nothing</li>}
            </ul>
            <p className="text-slate-600 dark:text-slate-400">
              These are session handling only. Everything else the dashboard does is a command from the list, through the same API and the
              same guards as the command line.
            </p>
            <Json value={status} />
          </div>
        ) : (
          <p className="text-sm text-slate-600 dark:text-slate-400">Loading…</p>
        )}
      </Card>
    </div>
  );
}
