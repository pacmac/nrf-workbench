import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useEffect, useState } from "react";
import { readJson, writeFile, writeJson } from "./api.js";
import { DEFAULT_PROJECT, ESP_BAUDS, PROJECT_NAME, PROJECT_RULE_TEXT, bytesText, espFlashBoundS, when } from "./format.js";
import { Button, Card, Chip, Field, PageHeader, SlugNote, inputClass } from "./ui.jsx";
import { ResultView } from "./Commands.jsx";
import EspTargets, { useEspTargets } from "./Esp32.jsx";

// The package store as folders (docs/specs/dashboard.md, "The Firmware page"). Every action is a command the
// service listed, sent to the path it listed, and every answer is shown whole. The page adds no rule of its own:
// a refusal (in_use, project_not_empty, bad_project) is the service's. Radix is used for two things only, the
// delete confirm (an alert dialog) and a row's actions (a dropdown menu); the rest is native.

const NONE = "";

const menuClass = "z-50 min-w-48 rounded-lg border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-900";
const itemClass = "flex min-h-10 cursor-default select-none items-center rounded-md px-3 py-2 text-sm outline-none data-[highlighted]:bg-slate-100 dark:data-[highlighted]:bg-slate-800";
const dangerItemClass = "flex min-h-10 cursor-default select-none items-center rounded-md px-3 py-2 text-sm text-red-700 outline-none data-[highlighted]:bg-red-50 dark:text-red-300 dark:data-[highlighted]:bg-red-950";

export default function Firmware({ commands, boards, onFlash, onDone, refreshKey = 0 }) {
  const find = (name) => (commands || []).find((c) => c.name === name) || null;
  const list = find("firmware.list");
  const put = find("firmware.put");
  const del = find("firmware.delete");
  const move = find("firmware.move");
  const projNew = find("firmware.project_new");
  const projDel = find("firmware.project_delete");

  const [store, setStore] = useState(null);                       // null while loading
  const [storeError, setStoreError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [project, setProject] = useState(NONE);                   // the project being looked at; nothing is chosen for you
  const [newName, setNewName] = useState("");
  const [file, setFile] = useState(null);
  const [fileKey, setFileKey] = useState(0);                      // remounts the file input after an upload
  const [label, setLabel] = useState("");
  const [outcome, setOutcome] = useState(null);                   // the whole Result of the last write
  const [ask, setAsk] = useState(null);                           // the delete being confirmed
  const [boardSerial, setBoardSerial] = useState(NONE);
  // The ESP32 boards (docs/specs/esp32.md). A package is flashed to the one board there is, or the one the person chose:
  // none is chosen for them, as with the nRF boards.
  const espList = find("esp32.targets");
  const espFlash = find("esp32.flash");
  const esp = useEspTargets(espList, attempt + refreshKey);              // refreshKey: "Look again" in the Attached box
  const [espName, setEspName] = useState(NONE);
  const [espAsk, setEspAsk] = useState(null);                     // the ESP32 package whose flash is being confirmed
  const [espBaud, setEspBaud] = useState(ESP_BAUDS[0]);
  const [espBound, setEspBound] = useState(null);                 // null = derived from the package and the baud
  const espTargets = esp.targets || [];
  const espTarget = espTargets.length === 1 ? espTargets[0] : espTargets.find((t) => t.name === espName) || null;

  useEffect(() => {
    if (!list) return undefined;
    let live = true;
    // a refresh keeps what is on screen until the answer comes: the list is not swapped for "Loading"
    readJson(list.path).then((r) => {
      if (!live) return;
      if (r.ok && r.body && r.body.data) {
        const items = r.body.data.items || [];
        const projects = r.body.data.projects || [...new Set(items.map((i) => i.project || DEFAULT_PROJECT))].sort();
        setStore({ items, projects });
        setStoreError("");
      } else {
        setStore((old) => old || { items: [], projects: [] });
        setStoreError(`HTTP ${r.status}` + (r.body && r.body.message ? `: ${r.body.message}` : ""));
      }
    }).catch((e) => {
      if (live) { setStore((old) => old || { items: [], projects: [] }); setStoreError(e && e.message ? String(e.message) : String(e)); }
    });
    return () => { live = false; };
  }, [list, attempt]);

  const items = store ? store.items : [];
  const projects = store ? store.projects : [];
  const inProject = items.filter((s) => (s.project || DEFAULT_PROJECT) === project);
  const pending = Boolean(outcome && outcome.pending);
  const known = project !== NONE && projects.includes(project);
  const flashBoard = boards && boards.length === 1 ? boards[0] : (boards || []).find((b) => b.serial === boardSerial) || null;
  const newOk = PROJECT_NAME.test(newName.trim());

  // One way to write: show "working", then the whole Result, and refresh the list when the service said yes.
  async function write(request) {
    setOutcome({ pending: true });
    try {
      const r = await request;
      setOutcome(r);
      if (onDone) onDone(r);
      if (r.ok) setAttempt((n) => n + 1);
      return r;
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
      return null;
    }
  }

  async function createProject(event) {
    event.preventDefault();
    const name = newName.trim();
    if (!projNew || !PROJECT_NAME.test(name) || pending) return;
    const r = await write(writeJson(projNew.path, { project: name }));
    if (r && r.ok) { setProject(name); setNewName(""); }
  }

  async function upload(event) {
    event.preventDefault();
    if (!put || !file || !known || pending) return;
    const r = await write(writeFile(put.path, { project, name: label.trim() || file.name }, file));
    if (r && r.ok) { setFile(null); setLabel(""); setFileKey((n) => n + 1); }
  }

  async function removeProject() {
    const r = await write(writeJson(projDel.path, { project }));
    if (r && r.ok) setProject(NONE);
  }

  const espImageBytes = espAsk ? espAsk.image_bytes || espAsk.bytes : 0;
  const espBoundValue = espBound !== null ? espBound : espAsk ? String(espFlashBoundS(espImageBytes, Number(espBaud))) : "";
  const espWhat = (s) => (s.kind === "esp-image" ? "ESP32 whole-flash image" : `ESP32 bundle, ${s.parts} parts${s.chip ? `, ${s.chip}` : ""}${s.elf ? ", with its ELF" : ""}`);
  const openEspFlash = (s) => { setEspBound(null); setEspAsk(s); };
  const runEspFlash = () => write(writeJson(espFlash.path, {
    target: espTarget.name, firmware: espAsk.sha256, flash_bound_s: Number(espBoundValue), baud: Number(espBaud),
  }));

  const askDeletePackage = (s) => setAsk({
    title: `Delete ${s.name || s.sha256.slice(0, 8)}?`,
    body: `${s.name || "This package"} (${s.sha256.slice(0, 8)}, ${bytesText(s.bytes)}) will be removed from the project ${project}. This cannot be undone: if it is the only copy, it is gone.`,
    label: "Delete package",
    run: () => write(writeJson(del.path, { id: s.sha256 })),
  });
  const askDeleteProject = () => setAsk({
    title: `Delete the project ${project}?`,
    body: `The empty project ${project} will be removed.`,
    label: "Delete project",
    run: removeProject,
  });

  if (commands && !list) {
    return <div className="space-y-4"><PageHeader title="Firmware" topic="flashing" /><Card><p className="text-[13px] text-slate-600 dark:text-slate-400">The package store is off on this host.</p></Card></div>;
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Firmware" topic="flashing" />
      {outcome && (
        <Card title="Last result">
          <ResultView outcome={outcome} pendingText="Working…" />
        </Card>
      )}

      {storeError && (
        <div className="space-y-2 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100" role="alert">
          <p>The package store could not be listed: <span className="font-mono">{storeError}</span>.</p>
          <Button onClick={() => setAttempt((n) => n + 1)}>Try again</Button>
        </div>
      )}

      {espList && <EspTargets commands={commands} targets={esp.targets} native={esp.native} error={esp.error} onChanged={() => setAttempt((n) => n + 1)} onDone={onDone} />}

      {store === null ? (
        <Card title="Firmware"><p className="text-sm text-slate-600 dark:text-slate-400">Loading the store…</p></Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-[18rem_minmax(0,1fr)]">
          <Card title="Projects">
            {projects.length === 0 ? (
              <p className="mb-3 text-[13px] text-slate-600 dark:text-slate-400">No project yet.</p>
            ) : (
              <ul className="mb-3 space-y-1" aria-label="Projects">
                {projects.map((p) => (
                  <li key={p}>
                    <button
                      type="button"
                      aria-pressed={p === project}
                      onClick={() => setProject(p)}
                      className={"flex min-h-10 w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm " + (p === project
                        ? "bg-blue-600 font-medium text-white dark:bg-blue-500 dark:text-slate-950"
                        : "bg-slate-100 text-slate-800 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700")}
                    >
                      <span className="break-all">{p}</span>
                      <span>{items.filter((s) => (s.project || DEFAULT_PROJECT) === p).length}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {projNew && (
              <form onSubmit={createProject} className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-800">
                <Field label="New project">
                  <input className={inputClass} type="text" value={newName} onChange={(e) => setNewName(e.target.value.toLowerCase())} aria-label="New project name" autoComplete="off" spellCheck="false" />
                </Field>
                <SlugNote value={newName} />
                <Button type="submit" disabled={!newOk || pending}>Create project</Button>
              </form>
            )}
          </Card>

          <Card
            title={known ? project : "Project"}
            aside={known && <Chip>{inProject.length} {inProject.length === 1 ? "package" : "packages"}</Chip>}
          >
            {!known ? (
              <p className="text-sm text-slate-600 dark:text-slate-400">Choose a project to see its packages.</p>
            ) : (
              <div className="space-y-4">
                {put && (
                  <form onSubmit={upload} className="space-y-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                    <p className="text-sm font-medium">Upload into <span className="font-mono">{project}</span></p>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <Field label="Package file">
                        <input key={fileKey} className={inputClass} type="file" accept=".zip,.bin,application/zip,application/octet-stream" aria-label="Package file" onChange={(e) => setFile(e.target.files[0] || null)} />
                      </Field>
                      <Field label="Name (optional)">
                        <input className={inputClass} type="text" value={label} onChange={(e) => setLabel(e.target.value)} aria-label="Package name" autoComplete="off" />
                      </Field>
                    </div>
                    <Button type="submit" disabled={!file || pending}>Upload</Button>
                  </form>
                )}

                {boards && boards.length > 1 && (
                  <Field label="Flash to board">
                    <select className={inputClass} value={flashBoard ? boardSerial : NONE} onChange={(e) => setBoardSerial(e.target.value)} aria-label="Flash to board">
                      <option value={NONE}>Choose a board…</option>
                      {boards.map((b) => <option key={`${b.serial}-${b.mode}`} value={b.serial}>{b.name ? `${b.name}, ` : ""}{b.serial} ({b.mode})</option>)}
                    </select>
                  </Field>
                )}

                {espFlash && espTargets.length > 1 && inProject.some((s) => s.kind) && (
                  <Field label="Flash to ESP32 board">
                    <select className={inputClass} value={espTarget ? espName : NONE} onChange={(e) => setEspName(e.target.value)} aria-label="Flash to ESP32 board">
                      <option value={NONE}>Choose a board…</option>
                      {espTargets.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
                    </select>
                  </Field>
                )}

                {inProject.length === 0 ? (
                  <p className="text-sm text-slate-600 dark:text-slate-400">This project is empty.</p>
                ) : (
                  <ul className="space-y-2" aria-label={`Packages in ${project}`}>
                    {inProject.map((s) => {
                      const name = s.name || "unnamed";
                      const others = projects.filter((p) => p !== project);
                      return (
                        <li key={s.sha256} className="flex flex-wrap items-start gap-x-3 gap-y-2 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                          <div className="min-w-0 flex-1">
                            <p className="break-words font-medium">{name}</p>
                            <p className="text-xs text-slate-600 dark:text-slate-400">
                              <span className="font-mono" title={s.sha256}>{s.sha256.slice(0, 8)}</span> · {bytesText(s.image_bytes || s.bytes)} · {s.kind ? espWhat(s) : `${s.packets} packets · SoftDevice ${(s.softdevice_req || []).join(", ") || "none"}`}
                            </p>
                            <p className="text-xs text-slate-600 dark:text-slate-400">
                              uploaded {when(s.uploaded_at ? Date.parse(s.uploaded_at) / 1000 : null)}{s.uploaded_by ? ` by ${s.uploaded_by}` : ""}
                            </p>
                          </div>
                          <div className="flex items-center gap-2">
                            {s.kind ? (
                              <Button
                                tone="quiet"
                                disabled={!espFlash || !espTarget || pending}
                                title={!espFlash ? "Flashing an ESP32 is not available on this host" : espTargets.length === 0 ? "No ESP32 board is declared yet" : !espTarget ? "Choose a board first" : `Flash ${espTarget.name}`}
                                onClick={() => openEspFlash(s)}
                                aria-label={`Flash ${name} to an ESP32`}
                              >
                                Flash to ESP32…
                              </Button>
                            ) : (
                            <Button
                              tone="quiet"
                              disabled={!flashBoard || pending}
                              title={!boards || boards.length === 0 ? "No board is attached" : !flashBoard ? "Choose a board first" : `Flash ${flashBoard.serial}`}
                              onClick={() => onFlash(flashBoard, { project, id: s.sha256 })}
                              aria-label={`Flash ${name}`}
                            >
                              Flash…
                            </Button>
                            )}
                            <DropdownMenu.Root>
                              <DropdownMenu.Trigger asChild>
                                <Button tone="quiet" disabled={pending} aria-label={`More actions for ${name}`}>
                                  More
                                  <svg aria-hidden="true" viewBox="0 0 12 12" className="ml-1 inline h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 4l4 4 4-4" /></svg>
                                </Button>
                              </DropdownMenu.Trigger>
                              <DropdownMenu.Portal>
                                <DropdownMenu.Content align="end" sideOffset={4} className={menuClass}>
                                  {move && (
                                    <>
                                      <DropdownMenu.Label className="px-3 py-1 text-xs font-medium text-slate-600 dark:text-slate-400">Move to</DropdownMenu.Label>
                                      {others.length === 0 ? (
                                        <div className="px-3 py-2 text-xs text-slate-600 dark:text-slate-400">No other project. Make one first.</div>
                                      ) : others.map((p) => (
                                        <DropdownMenu.Item key={p} className={itemClass} onSelect={() => write(writeJson(move.path, { id: s.sha256, project: p }))}>{p}</DropdownMenu.Item>
                                      ))}
                                      <DropdownMenu.Separator className="my-1 h-px bg-slate-200 dark:bg-slate-700" />
                                    </>
                                  )}
                                  {del && <DropdownMenu.Item className={dangerItemClass} onSelect={() => askDeletePackage(s)}>Delete…</DropdownMenu.Item>}
                                </DropdownMenu.Content>
                              </DropdownMenu.Portal>
                            </DropdownMenu.Root>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}

                {projDel && (
                  <div className="border-t border-slate-200 pt-3 dark:border-slate-800">
                    <Button tone="quiet" disabled={inProject.length > 0 || pending} onClick={askDeleteProject}>Delete project</Button>
                    {inProject.length > 0 && (
                      <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
                        It holds {inProject.length} {inProject.length === 1 ? "package" : "packages"}. Delete or move {inProject.length === 1 ? "it" : "them"} first.
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}
          </Card>
        </div>
      )}

      <AlertDialog.Root open={Boolean(espAsk && espTarget)} onOpenChange={(open) => { if (!open) setEspAsk(null); }}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 bg-black/50" />
          {espAsk && espTarget && (
            <AlertDialog.Content className="fixed left-1/2 top-1/2 w-[min(30rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-slate-300 bg-white p-4 text-slate-900 shadow-xl dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100">
              <AlertDialog.Title className="text-base font-semibold">Erase and flash {espTarget.name}?</AlertDialog.Title>
              <AlertDialog.Description className="mt-2 text-sm">
                {espAsk.name || "This package"} ({espAsk.sha256.slice(0, 8)}, {bytesText(espImageBytes)}) is written to <span className="font-mono">{espTarget.name}</span>, replacing what is on it.
                The board is reset when the write is done, and its console is let go for the length of the flash.
              </AlertDialog.Description>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <Field label="Speed">
                  <select className={inputClass} value={espBaud} onChange={(e) => setEspBaud(Number(e.target.value))} aria-label="ESP32 flash speed">
                    {ESP_BAUDS.map((b) => <option key={b} value={b}>{b} baud</option>)}
                  </select>
                </Field>
                <Field label="Give up after (seconds)">
                  <input className={inputClass} type="number" step="any" min="0" value={espBoundValue} onChange={(e) => setEspBound(e.target.value)} aria-label="ESP32 flash bound in seconds" />
                </Field>
              </div>
              <div className="mt-4 flex justify-end gap-2">
                <AlertDialog.Cancel asChild><Button tone="quiet">Cancel</Button></AlertDialog.Cancel>
                <AlertDialog.Action asChild><Button tone="danger" disabled={!(Number(espBoundValue) > 0)} onClick={runEspFlash}>Erase and flash {espTarget.name}</Button></AlertDialog.Action>
              </div>
            </AlertDialog.Content>
          )}
        </AlertDialog.Portal>
      </AlertDialog.Root>

      <AlertDialog.Root open={Boolean(ask)} onOpenChange={(open) => { if (!open) setAsk(null); }}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 bg-black/50" />
          {ask && (
            <AlertDialog.Content className="fixed left-1/2 top-1/2 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-slate-300 bg-white p-4 text-slate-900 shadow-xl dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100">
              <AlertDialog.Title className="text-base font-semibold">{ask.title}</AlertDialog.Title>
              <AlertDialog.Description className="mt-2 text-sm">{ask.body}</AlertDialog.Description>
              <div className="mt-4 flex justify-end gap-2">
                <AlertDialog.Cancel asChild><Button tone="quiet">Cancel</Button></AlertDialog.Cancel>
                <AlertDialog.Action asChild><Button tone="danger" onClick={() => ask.run()}>{ask.label}</Button></AlertDialog.Action>
              </div>
            </AlertDialog.Content>
          )}
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </div>
  );
}
