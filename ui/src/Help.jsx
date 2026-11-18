import { useState } from "react";
import { TOPICS, topicText } from "./help.js";
import { Lamp, PageHeader, cx, inputClass } from "./ui.jsx";

// Help is the only place that explains (docs/specs/dashboard-redesign.md, "Help"): a topic list, and one short, structured topic at a time.
// On a phone the list is the whole screen until a topic is chosen, and a Back button returns to it.

const PAGE_NAME = { home: "Home", devices: "Devices", wiring: "Wiring", console: "Console", firmware: "Firmware", gpio: "GPIO", activity: "Activity", commands: "Commands" };

function Block({ title, children }) {
  return (
    <section className="space-y-2">
      <h4 className="text-[15px] font-semibold">{title}</h4>
      {children}
    </section>
  );
}

function Topic({ t, onTopic, onGo }) {
  const by = (id) => TOPICS.find((x) => x.id === id);
  return (
    <article className="space-y-5" aria-label={t.title}>
      <div>
        <h3 className="text-xl font-semibold">{t.title}</h3>
        <p className="mt-1 max-w-[65ch] text-[15px] text-slate-700 dark:text-slate-300">{t.summary}</p>
      </div>
      {t.legend && t.legend.length > 0 && (
        <Block title="What you see">
          <ul className="space-y-2">
            {t.legend.map((l) => (
              <li key={l.term} className="flex items-start gap-3">
                <Lamp state={l.state} size={16} className="mt-0.5" />
                <span className="text-[15px]"><strong className="font-medium">{l.term}.</strong> {l.text}</span>
              </li>
            ))}
          </ul>
        </Block>
      )}
      {t.points && t.points.length > 0 && (
        <Block title="Where things are">
          <ul className="max-w-[65ch] list-disc space-y-1.5 pl-5 text-[15px]">{t.points.map((p) => <li key={p}>{p}</li>)}</ul>
        </Block>
      )}
      {t.steps && t.steps.length > 0 && (
        <Block title="Do this">
          <ol className="max-w-[65ch] list-decimal space-y-1.5 pl-5 text-[15px]">{t.steps.map((s) => <li key={s}>{s}</li>)}</ol>
        </Block>
      )}
      {t.problems && t.problems.length > 0 && (
        <Block title="If it does not work">
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {t.problems.map((p) => (
              <li key={p.symptom} className="space-y-0.5 py-2">
                <p className="text-[15px] font-medium">{p.symptom}</p>
                <p className="text-[13px] text-slate-600 dark:text-slate-400">{p.cause}</p>
                <p className="text-[15px]">{p.fix}</p>
              </li>
            ))}
          </ul>
        </Block>
      )}
      {t.codes && t.codes.length > 0 && (
        <Block title="Messages you may see">
          <dl className="space-y-1.5">
            {t.codes.map((c) => (
              <div key={c.code} className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-3 text-[15px]">
                <dt className="font-mono text-[13px] pt-0.5">{c.code}</dt>
                <dd>{c.fix}</dd>
              </div>
            ))}
          </dl>
        </Block>
      )}
      <Block title="Related">
        <div className="flex flex-wrap gap-2">
          {(t.related || []).map((id) => by(id) && (
            <button key={id} type="button" onClick={() => onTopic(id)} className="min-h-9 rounded-md bg-slate-100 px-3 text-[13px] font-medium hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700">{by(id).title}</button>
          ))}
          {t.pages.map((p) => PAGE_NAME[p] && (
            <button key={p} type="button" onClick={() => onGo(p)} className="min-h-9 rounded-md border border-slate-300 px-3 text-[13px] font-medium hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800">Open {PAGE_NAME[p]}</button>
          ))}
        </div>
      </Block>
    </article>
  );
}

export default function Help({ topic, direct, onTopic, onGo }) {
  const [query, setQuery] = useState("");
  const [reading, setReading] = useState(Boolean(direct));          // phone: false shows the list, true shows the topic
  const q = query.trim().toLowerCase();
  const shown = TOPICS.filter((t) => !q || topicText(t).includes(q));
  const current = TOPICS.find((t) => t.id === topic) || TOPICS[0];
  const choose = (id) => { onTopic(id); setReading(true); window.scrollTo(0, 0); };
  return (
    <div className="space-y-4">
      <PageHeader title="Help" />
      <div className="grid gap-4 md:grid-cols-[16rem_minmax(0,1fr)] md:items-start">
        <nav aria-label="Help topics" className={cx("space-y-2", reading && "hidden md:block")}>
          <input className={inputClass} type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search help" aria-label="Search help" autoComplete="off" />
          <ul className="rounded-md border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
            {shown.map((t) => (
              <li key={t.id} className="border-b border-slate-200 last:border-b-0 dark:border-slate-800">
                <button type="button" onClick={() => choose(t.id)} aria-current={t.id === current.id ? "true" : undefined}
                  className={cx("flex w-full min-h-12 items-center px-4 text-left text-[15px]", t.id === current.id ? "font-semibold md:bg-slate-100 md:dark:bg-slate-800" : "hover:bg-slate-50 dark:hover:bg-slate-800/60")}>
                  {t.title}
                  <span aria-hidden="true" className="ml-auto text-slate-400 md:hidden dark:text-slate-500">&rsaquo;</span>
                </button>
              </li>
            ))}
            {shown.length === 0 && <li className="px-4 py-3 text-[13px] text-slate-600 dark:text-slate-400">No topic matches.</li>}
          </ul>
        </nav>
        <div className={cx(!reading && "hidden md:block")}>
          <button type="button" onClick={() => setReading(false)} className="mb-3 min-h-9 text-[15px] underline md:hidden">Back to topics</button>
          <div className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
            <Topic t={current} onTopic={choose} onGo={onGo} />
          </div>
        </div>
      </div>
    </div>
  );
}
