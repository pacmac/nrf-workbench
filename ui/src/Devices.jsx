import { Button, Card, Chip } from "./ui.jsx";
import { clock } from "./format.js";

const MODE_TONE = { app: "green", bootloader: "amber" };

const STATE_TEXT = {
  app: "running its application",
  bootloader: "in its bootloader, waiting for a package",
  two: "attached in BOTH modes at once",
  absent: "not attached",
};

// One card per board, with everything the service knows about it. Nothing is hidden behind "ready": the state is
// what sysfs says, and a flash will say exactly why it refuses if it does.
export default function Devices({ boards, boardsAt, onFlash }) {
  if (boards === null) return <Card title="Devices"><p className="text-sm text-slate-600 dark:text-slate-400">Waiting for the first report from the service.</p></Card>;
  if (boards.length === 0) {
    return (
      <Card title="Devices">
        <p className="text-sm">No board is attached. <span className="text-slate-600 dark:text-slate-400">As of {clock(boardsAt)}.</span></p>
      </Card>
    );
  }
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {boards.map((b) => (
        <Card key={`${b.serial}-${b.mode}-${b.sysfs}`} title={b.product || b.id} aside={<Chip tone={MODE_TONE[b.mode] || "slate"}>{b.mode}</Chip>}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-slate-600 dark:text-slate-400">Serial</dt>
            <dd className="break-all font-mono">{b.serial}</dd>
            <dt className="text-slate-600 dark:text-slate-400">Profile</dt>
            <dd>{b.profile}</dd>
            <dt className="text-slate-600 dark:text-slate-400">State</dt>
            <dd>{STATE_TEXT[b.state] || b.state}</dd>
            <dt className="text-slate-600 dark:text-slate-400">USB id</dt>
            <dd className="font-mono">{b.id}</dd>
            <dt className="text-slate-600 dark:text-slate-400">Port</dt>
            <dd className="font-mono">{b.sysfs}</dd>
            <dt className="text-slate-600 dark:text-slate-400">Enumeration</dt>
            <dd className="font-mono">{b.enum}</dd>
            <dt className="text-slate-600 dark:text-slate-400">Reported</dt>
            <dd>{clock(boardsAt)}</dd>
          </dl>
          <div className="mt-3">
            <Button tone="quiet" onClick={() => onFlash(b)}>Flash this board…</Button>
          </div>
        </Card>
      ))}
    </div>
  );
}
