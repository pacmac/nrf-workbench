"""The /ws/events websocket: one connection carries every live feed (docs/specs/api.md).

        client:  {"want": {"devices": {"cursor": 0, "epoch": null}, "activity": {"cursor": 17, "epoch": "ab12"}}}
        server:  {"type": "events", "feed": "devices", "page": {events, lost, reset, latest, epoch}}

No polling anywhere. Each bus calls this connection's callback when something is published; the callback
only wakes the loop (thread-safely: publishers are other threads), and the loop then RESUMES BY CURSOR, so
an out-of-order or coalesced wake-up can never lose or repeat an event. A slow client is never a reason to
drop one silently: events stay in the bus, and the next page says how many were lost (or that the epoch
changed, which is a reset). The first page for a `want` is sent even when empty, so the client learns the
epoch and how far it is caught up. An unknown feed or a malformed message is an `error` message, never a
disconnect. The websocket is authenticated like a request, and an `Origin` that is not this host is refused
(a browser always sends one, so another site cannot drive it).
"""
import asyncio
import json

import anyio
from starlette.websockets import WebSocketDisconnect

from .auth import origin_allowed  # noqa: F401  (one definition, shared with the cookie check; tests reach it here)

CLOSE_FORBIDDEN_ORIGIN = 4403
CLOSE_UNAUTHORIZED = 4401


def feeds_of(streams):
    """{feed name: (bus, event types)} for the retained streams this service runs."""
    feeds = {}
    if streams is not None and streams.events is not None:
        feeds["devices"] = (streams.events, ("devices.changed", "devices.live_unavailable", "devices.read_failed"))
    if streams is not None and streams.activity is not None:
        # both types: the live feed shows what is running now (the start) as well as what happened (the result);
        # the history endpoint, ActivityLog.since, stays results-only
        feeds["activity"] = (streams.activity.bus, (streams.activity.STARTED, streams.activity.TYPE))
    return feeds


def make_endpoint(feeds, auth):
    async def endpoint(websocket):
        if not origin_allowed(websocket.headers):
            await websocket.close(code=CLOSE_FORBIDDEN_ORIGIN)
            return
        allowed, _caller = auth.authorise(websocket.headers, "read")
        if not allowed:
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        await websocket.accept()
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()

        def notify(_event):
            loop.call_soon_threadsafe(wake.set)         # called from a publisher's thread

        subscriptions = []
        for bus in set(bus for bus, _types in feeds.values()):
            subscriptions.append((bus, bus.subscribe(notify)))
        wants = {}
        send_lock = anyio.Lock()                               # the receiver and the pusher both send

        async def say(body):
            async with send_lock:
                await websocket.send_json(body)

        async def receive_loop(scope):
            while True:
                try:
                    text = await websocket.receive_text()
                except WebSocketDisconnect:
                    scope.cancel()
                    return
                try:
                    message = json.loads(text)
                    want = message["want"]
                    if not isinstance(want, dict):
                        raise ValueError("want must be an object")
                except (ValueError, KeyError, TypeError):
                    await say({"type": "error", "message": 'send {"want": {"<feed>": {"cursor": N, "epoch": E}}}'})
                    continue
                for feed, spec in want.items():
                    spec = spec if isinstance(spec, dict) else {}
                    cursor = spec.get("cursor", 0)
                    if feed not in feeds:
                        await say({"type": "error", "feed": feed,
                                   "message": "no such feed here (this service offers: %s)" % ", ".join(sorted(feeds))})
                    elif not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
                        await say({"type": "error", "feed": feed, "message": "cursor must be a whole number, 0 or more"})
                    else:
                        wants[feed] = {"cursor": cursor, "epoch": spec.get("epoch"), "sent": False}
                wake.set()

        async def push_loop(scope):
            while True:
                await wake.wait()
                wake.clear()
                for feed, w in list(wants.items()):
                    bus, types = feeds[feed]
                    page = bus.since(w["cursor"], w["epoch"], types)
                    if not (page.events or page.lost or page.reset or not w["sent"]):
                        continue
                    try:
                        await say({"type": "events", "feed": feed, "page": page.to_dict()})
                    except Exception:                                  # noqa: BLE001 - the socket is gone
                        scope.cancel()
                        return
                    w["cursor"], w["epoch"], w["sent"] = page.latest, page.epoch, True

        try:
            async with anyio.create_task_group() as group:
                group.start_soon(receive_loop, group.cancel_scope)
                group.start_soon(push_loop, group.cancel_scope)
        finally:
            for bus, token in subscriptions:
                bus.unsubscribe(token)
            try:
                await websocket.close()
            except Exception:                                          # noqa: BLE001 - already closed
                pass

    return endpoint
