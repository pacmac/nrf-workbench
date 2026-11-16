// The API client. Every URL is RELATIVE to the page (document.baseURI): the dashboard works on any port and
// behind any path, and this file contains no host and no port.
//
// Writes go only to paths the SERVER declared: a command's `path` from GET api/commands, and the two session
// routes, login and logout. There is no other write URL in this source (tests/dashboard/test_ui_rules.py
// scans for one). The token is typed once into the login form and sent once; the browser then holds only an
// HttpOnly cookie that no script can read, plus the per-session CSRF value kept in memory below.

let csrf = null;

export function setCsrf(value) {
  csrf = value || null;
}

export function resolve(path) {
  return new URL(String(path).replace(/^\//, ""), document.baseURI).toString();
}

async function parse(response) {
  let body = null;
  try {
    body = await response.json();
  } catch (e) {
    body = null;
  }
  return { status: response.status, ok: response.ok, body };
}

export async function readJson(path, query) {
  let target = resolve(path);
  if (query && Object.keys(query).length) target += "?" + new URLSearchParams(query).toString();
  return parse(await fetch(target, { credentials: "same-origin", headers: { Accept: "application/json" } }));
}

function writeHeaders(extra) {
  const headers = { Accept: "application/json", ...extra };
  if (csrf) headers["X-CSRF-Token"] = csrf;
  return headers;
}

// A JSON write to a path the server listed.
export async function writeJson(path, body) {
  return parse(await fetch(resolve(path), {
    method: "POST", credentials: "same-origin",
    headers: writeHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(body || {}),
  }));
}

// A raw-body write (an upload): the FILE is the body, the other arguments are the query. The browser declares
// the file's length itself, which the service requires.
export async function writeFile(path, query, file) {
  let target = resolve(path);
  if (query && Object.keys(query).length) target += "?" + new URLSearchParams(query).toString();
  return parse(await fetch(target, {
    method: "POST", credentials: "same-origin",
    headers: writeHeaders({ "Content-Type": "application/octet-stream" }), body: file,
  }));
}

export async function loadSession() {
  const r = await readJson("api/session");
  if (r.ok && r.body) setCsrf(r.body.csrf);
  return r;
}

export async function login(token) {
  const r = await parse(await fetch(resolve("login"), {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ token }),
  }));
  if (r.ok && r.body && r.body.csrf) setCsrf(r.body.csrf);
  return r;
}

export async function logout() {
  const r = await parse(await fetch(resolve("logout"), {
    method: "POST", credentials: "same-origin", headers: writeHeaders({}),
  }));
  if (r.ok) setCsrf(null);
  return r;
}
