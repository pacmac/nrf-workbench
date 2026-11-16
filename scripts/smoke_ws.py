"""smoke_ws.py - does Starlette on uvicorn serve a websocket with the installed packages?

Loopback only, an OS-assigned port, no hardware. The listening socket is created and put in
listen() state BEFORE the server starts, so the client's connect is queued by the kernel until the
server accepts; nothing here sleeps or polls for startup. Also records which websocket
implementation uvicorn chose (both `websockets` and `wsproto` may be installed).

Exits non-zero on failure and prints one RESULT line.
"""
import asyncio
import socket
import sys

import uvicorn
import websockets
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket


async def stream(ws: WebSocket):
    await ws.accept()
    for i in range(3):
        await ws.send_json({"seq": i})
    await ws.close()


app = Starlette(routes=[WebSocketRoute("/ws/programmer/monitor", stream)])


async def main():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", ws="auto"))
    task = asyncio.ensure_future(server.serve(sockets=[sock]))
    got = []
    async with websockets.connect("ws://127.0.0.1:%d/ws/programmer/monitor" % port) as c:
        async for msg in c:
            got.append(msg)
    impl = server.config.ws_protocol_class.__module__
    server.should_exit = True
    await task
    print("received:", got)
    print("ws implementation chosen by uvicorn:", impl)
    ok = got == ['{"seq":0}', '{"seq":1}', '{"seq":2}']
    print("RESULT OK" if ok else "RESULT FAIL")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
