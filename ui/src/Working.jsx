import { Spinner } from "./ui.jsx";
import { clock, workingText } from "./format.js";

// What is running on the bench RIGHT NOW, whoever started it (docs/specs/dashboard.md, "The in-progress
// indicator"). It comes from the live feed: a command that has started and not yet ended. It is not tied to the
// button that was pressed in this tab, so a flash started by the CLI, the MCP or another session shows here too,
// and a page opened in the middle of one shows it at once.
//
// The container is always present and only its content comes and goes, which is what lets a screen reader
// announce the change. It is not given role="status" (the Commands page's results are the page's statuses).
// There is no elapsed-time counter, because the page has no timers: the start time is the honest figure.
export default function Working({ running, feedLive }) {
  return (
    <div aria-live="polite" aria-label="Activity in progress">
      {running.length > 0 && (
        <section className="rounded-xl border border-blue-300 bg-blue-50 p-3 text-sm text-blue-950 dark:border-blue-700 dark:bg-blue-950 dark:text-blue-100">
          <ul className="space-y-1">
            {running.map((r) => (
              <li key={r.run} className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <Spinner />
                <strong>{workingText(r)}</strong>
                <span className="text-blue-800 dark:text-blue-300">by {r.caller}, started {clock(r.wall)}</span>
              </li>
            ))}
          </ul>
          {!feedLive && (
            <p className="mt-2 text-xs text-blue-800 dark:text-blue-300">
              Live updates are not arriving, so this may be out of date.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
