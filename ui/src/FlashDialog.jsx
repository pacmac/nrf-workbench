import { useEffect, useRef, useState } from "react";
import { readJson, writeFile, writeJson } from "./api.js";
import { ENTER_BOUND_S, bytesText, clock, expectedRangeS, flashBoundS, rangeText, seconds, THROUGHPUT_FASTEST_BPS, THROUGHPUT_SLOWEST_BPS, when } from "./format.js";
import { Button, Chip, Field, inputClass } from "./ui.jsx";
import { ResultView } from "./Commands.jsx";

// "Flash…" on a board. A person knows three things: which board, which package, and whether they are sure. So
// the board is fixed, the package is CHOSEN (from the store, with nothing preselected, or a file from their
// computer), and the rest is derived and tucked away (docs/specs/dashboard.md, "The flash flow"). It adds no
// flash logic: the guards (profile check before any touch, one touch per enumeration, content verdict) are the
// service's, and the whole Result is shown.

const NONE = "";

export function verdictLine(result, expected) {
  if (!result || !result.data || typeof result.data !== "object") return null;
  const d = result.data;
  const parts = [];
  if (typeof d.duration_s === "number") {                      // recorded, not judged (programmer spec)
    parts.push(`took ${seconds(d.duration_s)}` + (expected[1] > 0 ? ` (measured range for this size: ${rangeText(expected).replace("about ", "")})` : ""));
  }
  if (d.packets != null) parts.push(`${d.packets} packets`);
  if (d.entered_bootloader === true) parts.push("entered the bootloader itself");
  if (d.entered_bootloader === false) parts.push("was already in the bootloader");
  if (d.app_after && d.app_after.product) parts.push(`came back as “${d.app_after.product}”`);
  return parts.length ? parts.join(" · ") : null;
}

export default function FlashDialog({ board, commands, onClose, onDone }) {
  const ref = useRef(null);
  const flash = commands.find((c) => c.name === "programmer.flash") || null;
  const stream = commands.find((c) => c.name === "programmer.flash_stream") || null;
  const list = commands.find((c) => c.name === "firmware.list") || null;

  const [store, setStore] = useState(list ? null : []);          // null while loading
  const [storeNote, setStoreNote] = useState("");
  const [source, setSource] = useState(list && flash ? "store" : "file");
  const [chosen, setChosen] = useState(NONE);                    // a stored id; nothing is preselected
  const [file, setFile] = useState(null);
  const [product, setProduct] = useState("");
  const [enterBound, setEnterBound] = useState(String(ENTER_BOUND_S));
  const [flashBound, setFlashBound] = useState(null);            // null = derived from the package
  const [outcome, setOutcome] = useState(null);
  const [startedAt, setStartedAt] = useState(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  useEffect(() => {
    if (!list) return undefined;
    let live = true;
    readJson(list.path).then((r) => {
      if (!live) return;
      if (r.ok && r.body && r.body.data) setStore(r.body.data.items || []);
      else { setStore([]); setStoreNote(`The package store could not be listed (HTTP ${r.status}).`); }
    }).catch(() => { if (live) { setStore([]); setStoreNote("The package store could not be listed."); } });
    return () => { live = false; };
  }, [list]);

  const pending = Boolean(outcome && outcome.pending);
  const entry = store ? store.find((s) => s.sha256 === chosen) : null;
  const imageBytes = source === "store" ? (entry ? entry.image_bytes || entry.bytes : null) : file ? file.size : null;
  const derived = imageBytes ? flashBoundS(imageBytes) : null;
  const flashValue = flashBound !== null ? flashBound : derived !== null ? String(derived) : "";
  const range = imageBytes ? expectedRangeS(imageBytes) : null;
  const packageName = source === "store" ? (entry ? entry.name || entry.sha256.slice(0, 8) : "") : file ? file.name : "";
  const ready = Boolean(imageBytes) && (source === "store" ? Boolean(entry && flash) : Boolean(file && stream));
  const canFlash = ready && !pending && flashValue !== "" && enterBound !== "" && Number(flashValue) > 0 && Number(enterBound) > 0;

  async function run(event) {
    event.preventDefault();
    if (!canFlash) return;
    setStartedAt(Date.now() / 1000);
    setOutcome({ pending: true });
    const args = { serial: board.serial, enter_bound_s: Number(enterBound), flash_bound_s: Number(flashValue) };
    if (product.trim()) args.expect_product = product.trim();
    try {
      let r;
      if (source === "store") r = await writeJson(flash.path, { ...args, firmware: entry.sha256 });
      else r = await writeFile(stream.path, { ...args, name: file.name }, file);
      setOutcome(r);
      if (onDone) onDone(r);
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
  }

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onCancel={(e) => { if (pending) e.preventDefault(); }}
      className="m-auto max-h-[calc(100dvh-2rem)] w-[min(40rem,calc(100vw-2rem))] overflow-auto rounded-xl border border-slate-300 bg-white p-4 text-slate-900 shadow-xl backdrop:bg-black/50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Flash a board</h2>
        <Button tone="quiet" onClick={() => ref.current && ref.current.close()} disabled={pending}>Close</Button>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-slate-100 p-3 text-sm dark:bg-slate-950">
        <Chip tone={board.mode === "app" ? "green" : "amber"}>{board.mode}</Chip>
        <span className="font-mono">{board.serial}</span>
        <span>{board.product}</span>
        <Chip>{board.profile}</Chip>
        {board.mode === "bootloader" && <span className="basis-full text-xs text-slate-600 dark:text-slate-400">Already in its bootloader: the flash goes on from there.</span>}
      </div>

      {!flash && !stream ? (
        <p className="text-sm">Flashing is not available on this host (<span className="font-mono">programmer.flash</span> is absent; the Bench page says why).</p>
      ) : (
        <form onSubmit={run} className="space-y-4">
          <fieldset className="space-y-3" disabled={pending}>
            <legend className="mb-1 text-sm font-medium">Package</legend>
            <div className="flex flex-wrap gap-4 text-sm">
              {flash && list && (
                <label className="flex items-center gap-2">
                  <input type="radio" name="source" checked={source === "store"} onChange={() => setSource("store")} /> From the store
                </label>
              )}
              {stream && (
                <label className="flex items-center gap-2">
                  <input type="radio" name="source" checked={source === "file"} onChange={() => setSource("file")} /> A file from this computer
                </label>
              )}
            </div>

            {source === "store" ? (
              store === null ? (
                <p className="text-sm text-slate-600 dark:text-slate-400">Loading the store…</p>
              ) : store.length === 0 ? (
                <p className="text-sm text-slate-600 dark:text-slate-400">
                  {storeNote || "The store is empty."}{stream && " Choose “A file from this computer” to upload one."}
                </p>
              ) : (
                <Field label="Stored package" hint="Newest first. Nothing is chosen for you.">
                  <select className={inputClass} value={chosen} onChange={(e) => setChosen(e.target.value)} aria-label="Stored package">
                    <option value={NONE}>Choose a package…</option>
                    {store.map((s) => (
                      <option key={s.sha256} value={s.sha256}>
                        {(s.name || "unnamed")} · {bytesText(s.image_bytes || s.bytes)} · {s.sha256.slice(0, 8)} · {when(s.uploaded_at ? Date.parse(s.uploaded_at) / 1000 : null)}
                      </option>
                    ))}
                  </select>
                </Field>
              )
            ) : (
              <Field label="Package file (.zip)" hint="Uploaded to the service's store, then flashed, in one step. It stays in the store afterwards.">
                <input className={inputClass} type="file" accept=".zip,application/zip" aria-label="Package file" onChange={(e) => setFile(e.target.files[0] || null)} />
              </Field>
            )}
          </fieldset>

          <Field label="The board must come back as (optional)" hint="The USB product string the new firmware announces. Leave empty to skip the check. It is not the board's current product, which belongs to the old firmware.">
            <input className={inputClass} type="text" value={product} onChange={(e) => setProduct(e.target.value)} disabled={pending} autoComplete="off" />
          </Field>

          <details className="rounded-lg border border-slate-200 p-3 text-sm dark:border-slate-800">
            <summary className="cursor-pointer font-medium">Advanced: time limits</summary>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <Field label="Enter the bootloader within (s)" hint={`3 x the worst measured handover (3 x 0.589 s), rounded up: ${ENTER_BOUND_S} s.`}>
                <input className={inputClass} type="number" step="any" min="0" value={enterBound} onChange={(e) => setEnterBound(e.target.value)} disabled={pending} />
              </Field>
              <Field
                label="Write and come back within (s)"
                hint={imageBytes ? `2 x ${bytesText(imageBytes)} / ${THROUGHPUT_SLOWEST_BPS.toLocaleString("en-US")} B/s (the slowest measured) = ${derived} s.` : "Derived from the package once one is chosen."}
              >
                <input className={inputClass} type="number" step="any" min="0" value={flashValue} onChange={(e) => setFlashBound(e.target.value)} disabled={pending || !imageBytes} />
              </Field>
            </div>
          </details>

          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100" role="note">
            {ready ? (
              <>
                <strong>This erases <span className="font-mono">{board.serial}</span> ({board.product}) and writes {packageName} ({bytesText(imageBytes)}).</strong>{" "}
                The board enters its bootloader by itself; no button. Expect {rangeText(range)}
                ({(THROUGHPUT_SLOWEST_BPS / 1000).toFixed(2)} to {(THROUGHPUT_FASTEST_BPS / 1000).toFixed(2)} kB/s measured).
              </>
            ) : (
              <>Choose a package. Nothing is flashed until you press the button.</>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" tone="danger" disabled={!canFlash}>{pending ? "Flashing…" : `Erase and flash ${board.serial}`}</Button>
            {pending && startedAt && (
              <span role="status" className="text-sm text-slate-600 dark:text-slate-400">
                Started {clock(startedAt)}. The service answers when the board is back; the list behind this dialog follows the board live. Keep this open.
              </span>
            )}
          </div>
        </form>
      )}

      {outcome && !outcome.pending && (
        <div className="mt-4 space-y-2 border-t border-slate-200 pt-4 dark:border-slate-800">
          {outcome.body && outcome.body.class === "ok" && verdictLine(outcome.body, range || [0, 0]) && (
            <p role="status" className="text-sm font-medium">{verdictLine(outcome.body, range || [0, 0])}</p>
          )}
          <ResultView outcome={outcome} />
        </div>
      )}
    </dialog>
  );
}
