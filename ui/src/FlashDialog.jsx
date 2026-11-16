import { useEffect, useRef } from "react";
import { Button } from "./ui.jsx";
import { CommandForm } from "./Commands.jsx";

// "Flash…" on a board opens the SAME generated form as the Commands page, for programmer.flash, with the
// board's serial filled in. It adds no flash logic: the guards (profile check before any touch, one touch per
// enumeration, content verdict) are the service's, and the whole Result is shown.
export default function FlashDialog({ board, command, onClose, onDone }) {
  const ref = useRef(null);
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className="m-auto w-[min(40rem,calc(100vw-2rem))] rounded-xl border border-slate-300 bg-white p-4 text-slate-900 shadow-xl backdrop:bg-black/50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Flash {board.product || board.id}</h2>
        <Button tone="quiet" onClick={onClose}>Close</Button>
      </div>
      {command ? (
        <CommandForm command={command} preset={{ serial: board.serial }} onDone={onDone} />
      ) : (
        <p className="text-sm">Flashing is not available on this host (programmer.flash is absent; see the Bench page for why).</p>
      )}
    </dialog>
  );
}
