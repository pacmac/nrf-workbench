// The sample "service" behind the Wiring preview: a small in-memory store. It lives at module level so the page keeps
// its state while you move between pages, and it is reset by a reload. No timer, no network, nothing written anywhere.
import { useSyncExternalStore } from "react";
import { NEW_ADAPTER, initialState } from "./model.js";

let state = initialState();
const listeners = new Set();

function set(next) {
  state = next;
  listeners.forEach((l) => l());
}

export function useWiring() {
  return useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l); }, () => state);
}

export const wiringActions = {
  connect(device, spec) {
    const id = `c${state.seq}`;
    set({ ...state, seq: state.seq + 1, connections: [...state.connections, { id, device, spec }] });
    return id;
  },
  change(id, spec) {
    set({ ...state, connections: state.connections.map((c) => (c.id === id ? { ...c, spec } : c)) });
  },
  disconnect(id) {
    set({ ...state, connections: state.connections.filter((c) => c.id !== id) });
  },
  // The sample "a USB adapter was plugged in" event.
  plugIn() {
    if (state.adapters.some((a) => a.id === NEW_ADAPTER.id)) return;
    set({ ...state, adapters: [...state.adapters, NEW_ADAPTER], dismissed: state.dismissed.filter((d) => d !== NEW_ADAPTER.id) });
  },
  dismiss(adapterId) {
    set({ ...state, dismissed: [...state.dismissed, adapterId] });
  },
  reset() {
    set(initialState());
  },
};
