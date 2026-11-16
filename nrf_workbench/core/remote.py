"""The client of the service (docs/specs/api.md): ONE definition of how a registry row becomes an HTTP
request and how a response becomes a Result. The CLI (remote mode) and the MCP server both use it, so
neither imports the other and neither can disagree with the other about the wire.

Standard library only. A row here is the dict the service lists at GET /api/commands (the same shape
`Command.to_dict()` gives), so a front end generated from rows works the same locally and remotely.

There is NO default timeout: the service decides nothing about a caller's bound, and a flash can
legitimately take tens of seconds (the caller's bound is inside the call). A caller may pass one.

An upload is streamed from the file in chunks with its length declared (the service refuses a body with
no length); if the service answers before the whole body is sent (a refusal) that answer is read and
returned, it is not mistaken for a network failure.
"""
import hashlib
import http.client
import json
import os
import shutil
import urllib.parse

from .result import Result

CHUNK = shutil.COPY_BUFSIZE         # the standard library's own copy buffer, not a limit on the file


class RemoteError(Exception):
    """The service could not be asked, or did not give a Result. `kind` is one of:
    unreachable | unauthorized | usage | not_found | internal | protocol | file."""

    def __init__(self, kind, message, status=None, arg=None):
        Exception.__init__(self, message)
        self.kind, self.status, self.arg = kind, status, arg


class _LocalFile(Exception):
    pass


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class Remote(object):
    def __init__(self, base_url, token=None, timeout=None):
        parts = urllib.parse.urlsplit(base_url or "")
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("the service address must be http://host:port or https://host:port")
        self._https = parts.scheme == "https"
        self._host = parts.hostname
        self._port = parts.port or (443 if self._https else 80)
        self._prefix = parts.path.rstrip("/")
        self.token = token or None
        self.timeout = timeout

    # -- the wire ------------------------------------------------------------------------------------
    def _headers(self):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    def _request(self, method, path, query=None, json_body=None, file_path=None):
        url = self._prefix + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = self._headers()
        size = None
        if file_path is not None:
            size = os.path.getsize(file_path)               # a local problem is raised as itself
            headers.update({"Content-Type": "application/octet-stream", "Content-Length": str(size)})
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
        conn = cls(self._host, self._port, timeout=self.timeout)
        try:
            try:
                if file_path is None:
                    conn.request(method, url, body=body, headers=headers)
                else:
                    conn.putrequest(method, url)
                    for key, value in headers.items():
                        conn.putheader(key, value)
                    conn.endheaders()
                    try:
                        with open(file_path, "rb") as fh:
                            while True:
                                try:
                                    chunk = fh.read(CHUNK)
                                except OSError as exc:
                                    raise _LocalFile(str(exc))
                                if not chunk:
                                    break
                                conn.send(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        pass                                # the service answered early: read its answer
                response = conn.getresponse()
                raw = response.read()
            except _LocalFile as exc:
                raise RemoteError("file", "the file could not be read: %s" % exc)
            except (OSError, http.client.HTTPException) as exc:
                raise RemoteError("unreachable", "the service at %s:%d could not be reached: %s"
                                  % (self._host, self._port, exc))
        finally:
            conn.close()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            raise RemoteError("protocol", "the service did not answer with JSON (HTTP %d)" % response.status,
                              response.status)
        return response.status, payload

    @staticmethod
    def _result(status, payload):
        if isinstance(payload, dict) and "class" in payload and "code" in payload:
            try:
                return Result(payload["class"], payload["code"], payload.get("message", ""),
                              payload.get("data"), payload.get("evidence"))
            except ValueError:
                raise RemoteError("protocol", "the service sent a result of an unknown class", status)
        message = payload.get("message") if isinstance(payload, dict) else None
        error = payload.get("error") if isinstance(payload, dict) else None
        if status == 401:
            raise RemoteError("unauthorized", message or "a valid token is required", status)
        if status == 400:
            raise RemoteError("usage", message or "the service refused the arguments", status,
                              payload.get("arg") if isinstance(payload, dict) else None)
        if status == 404:
            raise RemoteError("not_found", message or "no such command on this service", status)
        if error == "internal" or status >= 500:
            raise RemoteError("internal", message or "the service failed", status)
        raise RemoteError("protocol", "an unexpected answer from the service (HTTP %d)" % status, status)

    # -- what a front end needs ----------------------------------------------------------------------
    def commands(self):
        """The rows the service offers, as dicts (GET /api/commands)."""
        status, payload = self._request("GET", "/api/commands")
        if status != 200 or not isinstance(payload, dict) or "commands" not in payload:
            self._result(status, payload)                   # raises the right RemoteError
            raise RemoteError("protocol", "unexpected answer to the command list", status)
        return payload["commands"]

    def capabilities(self):
        status, payload = self._request("GET", "/api/capabilities")
        if status != 200 or not isinstance(payload, dict):
            self._result(status, payload)
            raise RemoteError("protocol", "unexpected answer to the capability list", status)
        return payload

    def call(self, row, args, file_path=None):
        """Run one command. `args` are typed values (already validated by the caller). Returns a Result,
        whatever its class; raises RemoteError only when there is no Result to return."""
        args = dict((k, v) for k, v in (args or {}).items() if v is not None)
        if row.get("body") == "raw":
            if file_path is None:
                raise ValueError("%s takes the file as its body" % row["name"])
            query = dict((k, _text(v)) for k, v in args.items())
            return self._result(*self._request("POST", row["path"], query=query, file_path=file_path))
        if row["effect"] == "read":
            query = dict((k, _text(v)) for k, v in args.items())
            return self._result(*self._request("GET", row["path"], query=query))
        return self._result(*self._request("POST", row["path"], json_body=args))
