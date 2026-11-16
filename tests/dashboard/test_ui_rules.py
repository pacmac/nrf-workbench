#!/usr/bin/env python3
"""Source rules for the dashboard UI (docs/specs/dashboard.md, "Files for the UI").

The rules are functions over {path: text}, so each one is proved twice: the real `ui/src` passes it, and a
crafted bad source FAILS it (a rule that never fires proves nothing).

  * no timers: no setInterval and no setTimeout, even in a comment (no polling, no ticking ages);
  * no address: no scheme URL, no absolute-path URL, so the dashboard works on any port and path;
  * the only write URLs are login and logout; everything else a write sends to is a path the server listed;
  * only api.js does network I/O (fetch), and only feed.js opens the one websocket;
  * the token and the CSRF value are never stored (no storage, no cookie access outside the theme choice);
  * every light colour utility has a dark partner in the same string (both themes);
  * the committed build is the build of the committed source (the stamp), and its page keeps the server's
    placeholder and uses only relative asset URLs.
Run:  python3 tests/dashboard/test_ui_rules.py
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_ui  # noqa: E402

UI = os.path.join(ROOT, "ui")
SRC = os.path.join(UI, "src")
STATIC = os.path.join(ROOT, "nrf_workbench", "dashboard", "static")

TIMER = re.compile(r"\bset(?:Interval|Timeout)\b")
SCHEME = re.compile(r"\b(?:https?|wss?)://", re.I)
ABSOLUTE = re.compile(r"""\b(?:fetch|resolve|readJson|writeJson|writeFile|WebSocket|URL)\(\s*["'`]/""")
WRITE_LITERAL = re.compile(r"""\bwrite(?:Json|File)\(\s*["'`]""")
STORAGE = re.compile(r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie)\b")
NETWORK = re.compile(r"\b(?:fetch\(|XMLHttpRequest|sendBeacon|EventSource)")
RESOLVE_LITERAL = re.compile(r"""\bresolve\(\s*["']([^"']*)["']\s*\)""")
METHOD = re.compile(r"""\bmethod:\s*["']([A-Z]+)["']""")
STRING = re.compile(r'"((?:\\.|[^"\\\n])*)"|\'((?:\\.|[^\'\\\n])*)\'|`((?:\\.|[^`\\])*)`')
APPLY = re.compile(r"@apply\s+([^;]+);")
COLOUR = re.compile(r"^(bg|text|border)-(?:white|slate-\d+|red-\d+|amber-\d+|emerald-\d+|blue-\d+|purple-\d+)$")

WRITE_TARGETS = {"login", "logout"}
WRITES_IN_API = 4                  # writeJson, writeFile, login, logout


def read_tree(root):
    files = {}
    for base, _dirs, names in os.walk(root):
        for name in sorted(names):
            if name.endswith((".js", ".jsx", ".css")):
                path = os.path.join(base, name)
                with open(path) as fh:
                    files[os.path.relpath(path, root).replace(os.sep, "/")] = fh.read()
    return files


def check_timers(files):
    return ["%s: %s" % (p, m.group(0)) for p, t in sorted(files.items()) for m in TIMER.finditer(t)]


def check_addresses(files):
    out = []
    for p, t in sorted(files.items()):
        out += ["%s: scheme URL %s" % (p, m.group(0)) for m in SCHEME.finditer(t)]
        out += ["%s: absolute path in %s" % (p, m.group(0)) for m in ABSOLUTE.finditer(t)]
    return out


def check_write_urls(files):
    out = []
    for p, t in sorted(files.items()):
        out += ["%s: a write to a literal URL: %s" % (p, m.group(0)) for m in WRITE_LITERAL.finditer(t)]
        if p != "api.js" and METHOD.search(t):
            out.append("%s: sets an HTTP method itself" % p)
    api = files.get("api.js")
    if api is None:
        return out + ["api.js is missing"]
    literals = set(RESOLVE_LITERAL.findall(api))
    for lit in sorted(literals):
        if lit not in WRITE_TARGETS and not lit.startswith("api/"):
            out.append("api.js: resolve(%r) is neither a session route nor a read under api/" % lit)
    posts = [m for m in METHOD.findall(api) if m != "GET"]
    if any(m != "POST" for m in posts) or len(posts) != WRITES_IN_API:
        out.append("api.js: expected exactly %d POST writes, found %r" % (WRITES_IN_API, posts))
    return out


def check_network_confinement(files):
    out = []
    for p, t in sorted(files.items()):
        if p != "api.js" and NETWORK.search(t):
            out.append("%s: network I/O outside api.js" % p)
        if p != "feed.js" and re.search(r"\bnew WebSocket\(", t):
            out.append("%s: opens a websocket outside feed.js" % p)
    feed = files.get("feed.js", "")
    if len(re.findall(r"\bnew WebSocket\(", feed)) != 1:
        out.append("feed.js must open exactly one websocket")
    for p in ("api.js", "feed.js"):
        if "document.baseURI" not in files.get(p, ""):
            out.append("%s: must build its URLs from document.baseURI" % p)
    return out


def check_secrets_not_stored(files):
    return ["%s: %s" % (p, m.group(0)) for p, t in sorted(files.items())
            if p != "theme.js" for m in STORAGE.finditer(t)]


def literals(path, text):
    if path.endswith(".css"):
        return [m.group(1) for m in APPLY.finditer(text)]
    return [next(g for g in m.groups() if g is not None) for m in STRING.finditer(text)]


def check_dark_partners(files):
    out = []
    for p, t in sorted(files.items()):
        for lit in literals(p, t):
            tokens = lit.split()
            for token in tokens:
                m = COLOUR.match(token)
                if m and not any(x.startswith("dark:%s-" % m.group(1)) for x in tokens):
                    out.append("%s: %r has no dark: partner for %s" % (p, lit[:60], token))
    return out


RULES = (("timers", check_timers), ("addresses", check_addresses), ("write urls", check_write_urls),
         ("network confinement", check_network_confinement), ("secrets not stored", check_secrets_not_stored),
         ("dark partners", check_dark_partners))


class RealSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = read_tree(SRC)

    def test_the_source_exists(self):
        for name in ("api.js", "feed.js", "App.jsx", "Commands.jsx", "Login.jsx", "Band.jsx", "index.css"):
            self.assertIn(name, self.files)

    def test_every_rule_passes_on_the_real_source(self):
        for name, rule in RULES:
            self.assertEqual(rule(self.files), [], name)

    def test_the_login_page_never_stores_the_token(self):
        login = self.files["Login.jsx"]
        self.assertIn('type="password"', login)
        self.assertIn('setToken("")', login, "the typed token is cleared as soon as it is sent")

    def test_the_commands_page_sends_only_to_paths_the_server_listed(self):
        text = self.files["Commands.jsx"]
        for call in ("readJson(command.path", "writeFile(command.path", "writeJson(command.path"):
            self.assertIn(call, text)


class BadSource(unittest.TestCase):
    """Each rule must fire on a source that breaks it."""

    def good(self):
        return {"api.js": 'const b = document.baseURI;\n'
                          'resolve("login"); resolve("logout"); resolve("api/session");\n'
                          'a({ method: "POST" }); b({ method: "POST" }); c({ method: "POST" }); d({ method: "POST" });',
                "feed.js": "const b = document.baseURI; new WebSocket(u);", "theme.js": "localStorage.getItem(1)",
                "x.jsx": 'const a = "bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100";'}

    def test_the_baseline_is_clean(self):
        for name, rule in RULES:
            self.assertEqual(rule(self.good()), [], name)

    def with_(self, **extra):
        files = self.good()
        files.update(extra)
        return files

    def test_timers_are_found_even_in_a_comment(self):
        self.assertTrue(check_timers(self.with_(**{"x.jsx": "setInterval(f, 1)"})))
        self.assertTrue(check_timers(self.with_(**{"x.jsx": "// never setTimeout(f)"})))
        self.assertTrue(check_timers(self.with_(**{"x.jsx": "window.setTimeout(f)"})))

    def test_addresses(self):
        for bad in ('fetch("http://h/x")', 'a = "https://h"', 'new WebSocket("ws://h/ws")', 'wss://h',
                    'fetch("/api/x")', 'readJson("/api/x")', 'new URL("/x", b)', "resolve('/x')"):
            self.assertTrue(check_addresses(self.with_(**{"x.jsx": bad})), bad)
        self.assertEqual(check_addresses(self.with_(**{"x.jsx": 'readJson("api/x")'})), [])

    def test_a_write_to_a_literal_url_or_a_new_post_is_found(self):
        self.assertTrue(check_write_urls(self.with_(**{"x.jsx": 'writeJson("api/firmware/delete", {})'})))
        self.assertTrue(check_write_urls(self.with_(**{"x.jsx": "writeFile('api/x', {}, f)"})))
        self.assertTrue(check_write_urls(self.with_(**{"x.jsx": 'fetch(u, { method: "POST" })'})))
        self.assertTrue(check_write_urls(self.with_(**{"api.js": self.good()["api.js"] + 'resolve("api2/x");'})))
        self.assertTrue(check_write_urls(self.with_(**{"api.js": self.good()["api.js"] + 'e({ method: "POST" });'})))
        self.assertTrue(check_write_urls(self.with_(**{"api.js": self.good()["api.js"].replace('"POST" });', '"DELETE" });', 1)})))
        self.assertTrue(check_write_urls({"x.jsx": ""}), "no api.js is itself a finding")
        self.assertEqual(check_write_urls(self.with_(**{"x.jsx": "writeJson(command.path, sent)"})), [])

    def test_network_io_and_websockets_stay_in_their_files(self):
        self.assertTrue(check_network_confinement(self.with_(**{"x.jsx": "fetch(u)"})))
        self.assertTrue(check_network_confinement(self.with_(**{"x.jsx": "new XMLHttpRequest()"})))
        self.assertTrue(check_network_confinement(self.with_(**{"x.jsx": "navigator.sendBeacon(u)"})))
        self.assertTrue(check_network_confinement(self.with_(**{"x.jsx": "new WebSocket(u)"})))
        self.assertTrue(check_network_confinement(self.with_(**{"feed.js": "const b = document.baseURI;"})))
        self.assertTrue(check_network_confinement(self.with_(**{"feed.js": "document.baseURI; new WebSocket(a); new WebSocket(b);"})))
        self.assertTrue(check_network_confinement(self.with_(**{"api.js": "fetch(u)"})))

    def test_the_token_is_never_stored(self):
        for bad in ('localStorage.setItem("t", token)', "sessionStorage.x", "document.cookie = c", "indexedDB.open(1)"):
            self.assertTrue(check_secrets_not_stored(self.with_(**{"x.jsx": bad})), bad)
        self.assertEqual(check_secrets_not_stored(self.good()), [], "the theme choice may use storage")

    def test_a_light_colour_without_its_dark_partner_is_found(self):
        for bad in ('"bg-white text-sm"', '"text-slate-900"', '"border-slate-200 dark:text-slate-100"',
                    '"bg-blue-600 text-white dark:text-slate-950"', "`bg-red-100`", "'text-amber-900'"):
            self.assertTrue(check_dark_partners(self.with_(**{"x.jsx": "const a = %s;" % bad})), bad)
        self.assertTrue(check_dark_partners(self.with_(**{"x.css": "body { @apply bg-slate-50 text-slate-900; }"})))
        for good in ('"bg-white dark:bg-slate-900"', '"text-sm"', '"hover:bg-slate-200 dark:hover:bg-slate-800"',
                     '"rounded-xl p-4"'):
            self.assertEqual(check_dark_partners(self.with_(**{"x.jsx": "const a = %s;" % good})), [], good)
        self.assertEqual(check_dark_partners(self.with_(**{"x.css": "body { @apply bg-slate-50 dark:bg-slate-950; }"})), [])


class BuildMatchesSource(unittest.TestCase):
    def test_the_committed_build_is_the_build_of_the_committed_source(self):
        with open(os.path.join(STATIC, "source.sha256")) as fh:
            stamped = fh.read().strip()
        self.assertEqual(stamped, build_ui.source_digest(UI),
                         "ui/ changed after the last build: run python3 scripts/build_ui.py and commit the result")

    def test_the_digest_covers_every_file_the_build_reads_and_changes_with_each(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        try:
            ui = os.path.join(tmp, "ui")
            shutil.copytree(UI, ui, ignore=shutil.ignore_patterns("node_modules"))
            base = build_ui.source_digest(ui)
            self.assertEqual(base, build_ui.source_digest(UI))
            touched = build_ui.source_files(ui)
            self.assertIn("src/api.js", touched)
            self.assertIn("package-lock.json", touched)
            self.assertIn("index.html", touched)
            self.assertIn("vite.config.js", touched)
            for rel in touched:
                path = os.path.join(ui, rel)
                with open(path, "rb") as fh:
                    original = fh.read()
                with open(path, "wb") as fh:
                    fh.write(original + b" ")
                self.assertNotEqual(build_ui.source_digest(ui), base, rel)
                with open(path, "wb") as fh:
                    fh.write(original)
            with open(os.path.join(ui, "src", "added.js"), "w") as fh:
                fh.write("x")
            self.assertNotEqual(build_ui.source_digest(ui), base, "a new file changes it")
            os.remove(os.path.join(ui, "src", "added.js"))
            os.rename(os.path.join(ui, "src", "api.js"), os.path.join(ui, "src", "api2.js"))
            self.assertNotEqual(build_ui.source_digest(ui), base, "a rename changes it")
        finally:
            shutil.rmtree(tmp)

    def test_node_modules_is_not_part_of_the_digest(self):
        self.assertFalse([f for f in build_ui.source_files(UI) if "node_modules" in f])

    def test_the_built_page_keeps_the_servers_placeholder_and_uses_relative_assets_only(self):
        with open(os.path.join(STATIC, "index.html")) as fh:
            page = fh.read()
        self.assertEqual(page.count("{{BENCH_NAME}}"), 2, "the title and the meta tag")
        refs = re.findall(r"""(?:src|href)=["']([^"']+)["']""", page)
        icons = [r for r in refs if r.startswith("data:image/")]       # the inline icon: no request, no 404
        self.assertEqual(len(icons), 1)
        refs = [r for r in refs if r not in icons]
        self.assertEqual(len(refs), 2, "one script and one stylesheet")
        for ref in refs:
            self.assertTrue(ref.startswith("./assets/"), ref)
        for ref in refs:
            self.assertTrue(os.path.isfile(os.path.join(STATIC, ref[2:])), ref + " is referenced but not committed")

    def test_the_static_directory_holds_nothing_else(self):
        names = sorted(os.listdir(STATIC))
        self.assertEqual(names, ["assets", "index.html", "source.sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
