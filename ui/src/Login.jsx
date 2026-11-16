import { useState } from "react";
import { login } from "./api.js";
import { Button, Card, Field, inputClass } from "./ui.jsx";

// The token is typed here ONCE and sent ONCE. It is not stored: not in local storage, not in the page. The
// server answers with an HttpOnly cookie that script cannot read.
export default function Login({ name, onDone, expired }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(expired ? "Your session ended. Log in again." : "");

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const r = await login(token);
      setToken("");
      if (r.ok) onDone();
      else if (r.status === 401) setMessage("That token is not right.");
      else if (r.status === 403) setMessage("The service refused this page's origin.");
      else setMessage(`The login failed (HTTP ${r.status}).`);
    } catch (e) {
      setMessage("Could not reach the service.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-4 py-8">
      <div>
        <h1 className="text-2xl font-semibold">{name}</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">nrf-workbench</p>
      </div>
      <Card title="Log in">
        <form onSubmit={submit} className="space-y-4">
          <Field label="Service token" hint="The token file on the host this service runs on. It is sent once and never kept in this page.">
            <input
              className={inputClass}
              type="password"
              name="token"
              autoComplete="off"
              autoFocus
              value={token}
              onChange={(e) => setToken(e.target.value)}
              aria-describedby="login-message"
            />
          </Field>
          <p id="login-message" role="alert" className="min-h-5 text-sm text-red-700 dark:text-red-300">{message}</p>
          <Button type="submit" disabled={busy || !token}>{busy ? "Checking…" : "Log in"}</Button>
        </form>
      </Card>
    </main>
  );
}
