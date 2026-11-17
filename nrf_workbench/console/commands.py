"""Registers the console commands (docs/specs/console.md, "slice 1") in the core registry.

Each handler is a plain function `handler(ctx, **args) -> Result` over the broker the service owns (`host.console`) and
the macro store, so the same function serves HTTP, the CLI and the MCP. The refusals are made HERE, by the service: a
page that disables its Send button is a courtesy, never the guard.

`host` additionally carries (set by service.build):
  host.console        the Broker, or None where no service runs (the CLI's local mode)
  host.console_links  [Link] loaded from links.yaml, and host.console_problems, the file's own problems
"""
from ..core.registry import Arg, Command, UsageError
from ..core.result import Result
from . import links as linkmod
from .broker import ABSENT, ERROR, HELD, LINE_CAP_BYTES, OPEN, UNUSABLE
from .macros import MacroError, MacroStore, check_line, check_macro
from .port import PortError, PortGone

CONSOLE = ("console",)
# A problem code that is also a Result code is passed through; anything else is a plain `not_a_declared_link`.
_RESULT_CODES = ("not_a_declared_link", "baud_refused", "dtr_rts_unsafe", "link_rx_only", "voltage_unverified")


def _macros(host):
    return host.service("console_macros", lambda: MacroStore(host.config["paths"]["console"]))


def _link_state(host, name):
    """(state dict, None) or (None, a refusal). The refusals every command on a link shares."""
    broker = host.console
    st = broker.state(name) if broker is not None else None
    if st is None:
        return None, Result.refused("not_a_declared_link", "no link is declared with that name", {"link": name})
    if st["state"] == UNUSABLE:
        problems = st["problems"]
        code = next((p["code"] for p in problems if p["code"] in _RESULT_CODES), "not_a_declared_link")
        return None, Result.refused(code, "the link is not usable: " + st["reason"], {"link": name, "problems": problems})
    return st, None


def _need_service(ctx):
    if ctx.host.console is None:
        return Result.refused("service_not_running", "the console log and the adapters belong to the running service")
    return None


# -- reading --------------------------------------------------------------------------------------
def _links(ctx):
    host = ctx.host
    if host.console is not None:
        states = host.console.states()
        live, why = host.console.live, host.console.reason
    else:                                              # local mode: what is declared and whether the adapter is attached
        states = []
        for link in host.console_links:
            d = link.to_dict()
            found, reason = linkmod.resolve(link, host.sysfs, host.devroot)
            d.update({"state": UNUSABLE if not link.usable else (ABSENT if found is None else "present"),
                      "reason": reason, "node": found["node"] if found else None})
            states.append(d)
        live, why = False, "no service is running"
    return Result.success({"links": states, "problems": list(host.console_problems), "live": live, "live_reason": why},
                          "%d link%s declared" % (len(states), "" if len(states) == 1 else "s"))


def _since(ctx, link, cursor=0):
    refusal = _need_service(ctx)
    if refusal is not None:
        return refusal
    if cursor < 0:
        raise UsageError("cursor is 0 or more", "cursor")
    st, refusal = _link_state(ctx.host, link)
    if refusal is not None:
        return refusal
    page = ctx.host.console.since(link, cursor)
    return Result.success(dict(page, link=link, state=st["state"]),
                          "%d line%s" % (len(page["lines"]), "" if len(page["lines"]) == 1 else "s"))


# -- sending --------------------------------------------------------------------------------------
def _send_line(host, link, text, bound_s):
    """Send one line on a link, with every guard. A Result either way."""
    why = check_line(text)
    if why:
        raise UsageError(why, "text")
    st, refusal = _link_state(host, link)
    if refusal is not None:
        return refusal
    if st["state"] == HELD:
        return Result.refused("port_held", st["reason"], {"link": link})
    if st["state"] in (ABSENT, ERROR):
        return Result.refused("link_absent", st["reason"] or "the adapter is not open", {"link": link, "state": st["state"]})
    ok, code, message = next(l for l in host.console_links if l.name == link).can_send()
    if not ok:
        return Result.refused(code, message, {"link": link, "voltage": st["voltage"]})
    try:
        out = host.console.send(link, text, bound_s)
    except PortGone as exc:
        return Result.refused("link_absent", str(exc), {"link": link})
    except PortError as exc:
        return Result.failed("write_failed", str(exc), {"link": link})
    if out["waited"] and out["reply"] is None:
        return Result.failed("no_reply", "the line was sent and no reply line came within %s s" % bound_s,
                             {"link": link, "sent": out["sent"]})
    return Result.success({"link": link, "sent": out["sent"], "reply": out["reply"]}, "sent")


def _send(ctx, link, text, bound_s=None):
    refusal = _need_service(ctx)
    if refusal is not None:
        return refusal
    if bound_s is not None and bound_s <= 0:
        raise UsageError("bound_s is more than 0", "bound_s")
    return _send_line(ctx.host, link, text, bound_s)


def _reopen(ctx, link):
    refusal = _need_service(ctx)
    if refusal is not None:
        return refusal
    st, refusal = _link_state(ctx.host, link)
    if refusal is not None:
        return refusal
    return Result.success(ctx.host.console.reopen(link), "reopened")


# -- macros ---------------------------------------------------------------------------------------
def _store_failed(exc):
    return Result.failed("write_failed", str(exc))


def _macro_list(ctx):
    try:
        macros = _macros(ctx.host).list()
    except MacroError as exc:
        return _store_failed(exc)
    return Result.success({"macros": macros}, "%d macro%s" % (len(macros), "" if len(macros) == 1 else "s"))


def _macro_set(ctx, name, text):
    lines, why = check_macro(name, text)
    if why:
        raise UsageError(why, "name" if "name" in why else "text")
    try:
        record, created = _macros(ctx.host).set(name, text, by=ctx.caller)
    except MacroError as exc:
        return _store_failed(exc)
    except OSError as exc:
        return _store_failed(exc)
    return Result.success(dict(record, created=created), "saved" if created else "replaced")


def _macro_delete(ctx, name):
    try:
        gone = _macros(ctx.host).delete(name)
    except (MacroError, OSError) as exc:
        return _store_failed(exc)
    if not gone:
        return Result.refused("macro_unknown", "no macro has that name", {"name": name})
    return Result.success({"name": name}, "deleted")


def _macro_run(ctx, link, name, bound_s=None):
    refusal = _need_service(ctx)
    if refusal is not None:
        return refusal
    try:
        macro = _macros(ctx.host).get(name)
    except MacroError as exc:
        return _store_failed(exc)
    if macro is None:
        return Result.refused("macro_unknown", "no macro has that name", {"name": name})
    lines, _ = check_macro(name, macro["text"])
    done = []
    for line in lines:
        res = _send_line(ctx.host, link, line, bound_s)
        done.append({"line": line, "class": res.cls, "code": res.code, "reply": (res.data or {}).get("reply")})
        if not res.ok:                                 # stop at the first refusal or failure: never push on past one
            return Result(res.cls, res.code, "%s (line %d of %d: %s)" % (res.message, len(done), len(lines), line),
                          {"ran": len(done) - 1, "of": len(lines), "results": done}, dict(res.evidence, macro=name))
    return Result.success({"ran": len(done), "of": len(lines), "results": done}, "ran %d line%s" % (len(done), "" if len(done) == 1 else "s"))


_LINK = Arg("link", doc="the name of a declared link (see console.links)")


def register(registry, host):
    def add(command):
        registry.add(command, module=__name__)

    add(Command("console.links", "List the declared debug links: the adapter, its state, the wiring and what it has logged.",
                _links, requires=CONSOLE, result="links, problems (of the links file), live"))
    add(Command("console.since", "Read what a link has logged after a byte cursor.", _since,
                args=(_LINK, Arg("cursor", type="int", required=False, default=0,
                                 doc="a byte offset; the next cursor from the last call (0 reads everything retained)")),
                requires=CONSOLE, needs_service=True, result="lines (offset, bytes, text, reply), next, lost"))
    add(Command("console.send", "Send one line on a link that can transmit.", _send,
                args=(_LINK, Arg("text", doc="the line to send, at most %d bytes, no newline: the link's own line ending is added" % LINE_CAP_BYTES),
                      Arg("bound_s", type="float", required=False, unit="s",
                          doc="with a reply marker declared: wait for the reply line, and give up after this long. Omit it to return as soon as the bytes are written.")),
                requires=CONSOLE, effect="write", needs_service=True, result="sent, reply"))
    add(Command("console.reopen", "Close and reopen a link's adapter.", _reopen, args=(_LINK,),
                requires=CONSOLE, effect="write", needs_service=True, result="the link's state"))
    add(Command("console.macro_list", "List the console macros.", _macro_list, requires=CONSOLE, result="macros"))
    add(Command("console.macro_set", "Create or replace a console macro: a name and the lines it sends.", _macro_set,
                args=(Arg("name", doc="1 to 64 characters from a-z 0-9 . _ - , starting with a letter or a digit"),
                      Arg("text", doc="the lines, one per line, at most 4096 bytes in all")),
                requires=CONSOLE, effect="write", result="the macro, and whether it was new"))
    add(Command("console.macro_delete", "Delete a console macro.", _macro_delete, args=(Arg("name", doc="the macro's name"),),
                requires=CONSOLE, effect="write", result="the name deleted"))
    add(Command("console.macro_run", "Send a macro's lines on a link, in order, through every guard; stops at the first refusal.",
                _macro_run, args=(_LINK, Arg("name", doc="the macro's name"),
                                  Arg("bound_s", type="float", required=False, unit="s",
                                      doc="as for console.send, applied to each line")),
                requires=CONSOLE, effect="write", needs_service=True, result="ran, of, results"))
