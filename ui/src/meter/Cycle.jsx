import { chargeText, energyText, lapse, si } from "./format.js";

// The Sleep and Wake table of a recording's cycle (docs/specs/meter-page.md, "The cycle table"). `cyc` is cycle.js's answer when it found one. Everything is over the same whole
// cycles as the battery-life estimate, so the Wake and Sleep rows add up to the Cycle row. `volts` is the supply voltage when it is known (source mode, output on), else null.

const pct = (x) => (x < 0.1 ? "under 0.1 %" : x < 10 ? `${x.toFixed(1)} %` : `${Math.round(x)} %`);

function Cell({ main, sub, head = false }) {
  const Tag = head ? "th" : "td";
  return (
    <Tag scope={head ? "row" : undefined} className="py-2 pr-3 align-top font-normal">
      <div className={head ? "font-semibold" : "text-[15px] font-medium"}>{main}</div>
      {sub && <div className="text-xs text-slate-600 dark:text-slate-400">{sub}</div>}
    </Tag>
  );
}

export function CycleTable({ cyc, unit, volts }) {
  if (!cyc || !cyc.found) return null;
  const { wake, sleep } = cyc.phases;
  const perCycle = cyc.charge / cyc.cycles;
  const single = wake.widest === 1;                             // every wake filled one bucket: its time and average are limited by the bucket, its charge and peak are not
  const bound = Math.min(100, ((2 * cyc.stride) / cyc.period) * 100);
  const wakeShare = (wake.time / cyc.period) * 100;
  const chargeShare = (wake.charge / perCycle) * 100;
  const wakeTime = single ? `under ${lapse(2 * cyc.stride)}` : `about ${lapse(wake.time)}`;
  const varies = !single && cyc.cycles > 1 && wake.longest - wake.shortest > 2 * cyc.stride ? `${lapse(wake.shortest)} to ${lapse(wake.longest)}` : null;
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[20rem] text-left" aria-label="Sleep and wake">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-600 dark:border-slate-800 dark:text-slate-400">
              <th scope="col" className="py-1 pr-3 font-normal"><span className="sr-only">Phase</span></th>
              <th scope="col" className="py-1 pr-3 font-normal">Time per cycle</th>
              <th scope="col" className="py-1 pr-3 font-normal">Average current</th>
              <th scope="col" className="py-1 pr-3 font-normal">Charge per cycle</th>
              {volts ? <th scope="col" className="py-1 pr-3 font-normal">Energy per cycle</th> : null}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
            <tr>
              <Cell head main="Wake" sub={`${cyc.wakes} found`} />
              <Cell main={wakeTime} sub={[single ? `under ${pct(bound)} of the cycle` : `${pct(wakeShare)} of the cycle`, varies].filter(Boolean).join(", ")} />
              <Cell main={`${single ? "at least " : ""}${si(wake.mean, unit)}`} sub={`peak ${si(wake.peak, unit)}`} />
              <Cell main={chargeText(wake.charge)} sub={`${pct(chargeShare)} of the charge`} />
              {volts ? <Cell main={energyText(wake.charge, volts)} /> : null}
            </tr>
            <tr>
              <Cell head main="Sleep" />
              <Cell main={single ? `over ${lapse(Math.max(0, cyc.period - 2 * cyc.stride))}` : lapse(sleep.time)}
                sub={`${single ? "over " : ""}${pct(single ? 100 - bound : 100 - wakeShare)} of the cycle`} />
              <Cell main={sleep.mean === null ? "—" : si(sleep.mean, unit)} />
              <Cell main={chargeText(sleep.charge)} sub={`${pct(100 - chargeShare)} of the charge`} />
              {volts ? <Cell main={energyText(sleep.charge, volts)} /> : null}
            </tr>
            <tr>
              <Cell head main="Cycle" sub={`${cyc.cycles} counted`} />
              <Cell main={lapse(cyc.period)} />
              <Cell main={si(cyc.mean, unit)} />
              <Cell main={chargeText(perCycle)} />
              {volts ? <Cell main={energyText(perCycle, volts)} /> : null}
            </tr>
          </tbody>
        </table>
      </div>
      {single && (
        <p className="text-[13px] text-slate-600 dark:text-slate-400">
          Each wake fills a single point of the chart ({lapse(cyc.stride)}), so its length and average are limited by that: the length is under two points and the average is at least what is shown.
          Its charge, and the peak, are exact.
        </p>
      )}
      {!volts && <p className="text-[13px] text-slate-600 dark:text-slate-400">No energy: the supply voltage is not known here. It is, when the profiler is powering the board in source mode.</p>}
    </div>
  );
}
