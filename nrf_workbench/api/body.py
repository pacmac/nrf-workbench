"""A synchronous `read(n)` over an asynchronous request body (docs/specs/firmware.md, upload).

A handler that stores a file reads it as a stream with `read(n)` and knows the declared `length`; it must
be the same plain function whether it is called from the console or from a request. The handler runs in a
worker thread; each `read` hands the wait back to the event loop, so a large upload is never held in
memory: at most one network chunk beyond what was asked for is buffered.

A client that goes away mid-body surfaces as ConnectionResetError (an OSError), which the store already
treats as a body that failed, so nothing is left behind.
"""
import anyio
from starlette.requests import ClientDisconnect


class RequestBody(object):
    def __init__(self, request, length):
        self.length = length
        self._chunks = request.stream().__aiter__()
        self._buffer = b""
        self._done = False

    async def _read(self, n):
        while not self._done and (n is None or n < 0 or len(self._buffer) < n):
            try:
                chunk = await self._chunks.__anext__()
            except StopAsyncIteration:
                self._done = True
                break
            except ClientDisconnect:
                raise ConnectionResetError("the client disconnected before the body was complete")
            self._buffer += chunk
        if n is None or n < 0:
            out, self._buffer = self._buffer, b""
        else:
            out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out

    def read(self, n=-1):
        """Called from a worker thread (anyio.to_thread). Returns up to n bytes, b'' at the end."""
        return anyio.from_thread.run(self._read, n)
