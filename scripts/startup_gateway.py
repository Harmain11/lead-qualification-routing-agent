"""Lightweight public listener; keeps cold application imports off the public port."""

from __future__ import annotations

import argparse
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STARTUP_HTML = b"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Corral is starting</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f8f5;
color:#142a24;font-family:system-ui,sans-serif}main{max-width:440px;padding:32px}
h1{font-size:28px}p{line-height:1.6;color:#52675e}
.mark{font-weight:700;font-size:22px;color:#206b53}
button{padding:12px 20px;border:0;border-radius:8px;background:#206b53;color:white;
font:inherit;cursor:pointer}</style></head><body><main>
<div class="mark">Corral</div><h1>Corral is starting</h1>
<p role="status" id="status">The server is initializing after publishing.
This page will open automatically when it is ready.</p>
<button onclick="location.reload()">Check again</button></main><script>
let checks=0;async function check(){try{
const response=await fetch('/_corral/startup',{cache:'no-store'});
if(response.ok&&(await response.json()).ready){location.reload();return;}
}catch(error){}if(++checks===60){document.getElementById('status').textContent=
'Startup is taking longer than expected. This page is still checking automatically.';}
setTimeout(check,2000);}check();
</script></body></html>"""

HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}


class StartupGateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], upstream_port: int, ready_file: Path):
        self.upstream_port = upstream_port
        self.ready_file = ready_file
        self._checked_at = 0.0
        self._ready = False
        self._probe_lock = threading.Lock()
        super().__init__(address, GatewayHandler)

    def application_ready(self) -> bool:
        if not self.ready_file.is_file():
            return False
        with self._probe_lock:
            if time.monotonic() - self._checked_at < 1:
                return self._ready
            client = http.client.HTTPConnection("127.0.0.1", self.upstream_port, timeout=2)
            try:
                client.request("GET", "/health/live")
                response = client.getresponse()
                self._ready = response.status == 200
                response.read()
            except (OSError, http.client.HTTPException):
                self._ready = False
            finally:
                client.close()
                self._checked_at = time.monotonic()
            return self._ready


class GatewayHandler(BaseHTTPRequestHandler):
    server: StartupGateway
    protocol_version = "HTTP/1.1"
    server_version = "Corral"
    sys_version = ""

    def log_message(self, _format: str, *_args: object) -> None:
        # The API already produces structured access/error logs.
        pass

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(30)

    def respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 503:
            self.send_header("Retry-After", "2")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(body)

    def _request_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            parts: list[bytes] = []
            total = 0
            while True:
                size = int(self.rfile.readline(8192).split(b";", 1)[0].strip(), 16)
                if size < 0 or total + size > 64 * 1024 * 1024:
                    raise ValueError("request body too large")
                if size == 0:
                    while self.rfile.readline(8192).strip():
                        pass
                    return b"".join(parts)
                part = self.rfile.read(size)
                if len(part) != size or self.rfile.read(2) != b"\r\n":
                    raise ValueError("incomplete request body")
                parts.append(part)
                total += size
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > 64 * 1024 * 1024:
            raise ValueError("request body too large")
        body = self.rfile.read(size)
        if len(body) != size:
            raise ValueError("incomplete request body")
        return body

    def dispatch(self) -> None:
        path = self.path.split("?", 1)[0]
        ready = self.server.application_ready()
        if path == "/_corral/startup" and self.command in {"GET", "HEAD"}:
            self.respond(
                200 if ready else 503,
                json.dumps({"ready": ready}).encode(),
                "application/json",
            )
            return
        if not ready:
            if path == "/" and self.command in {"GET", "HEAD"}:
                self.respond(200, STARTUP_HTML, "text/html; charset=utf-8")
            else:
                self.respond(
                    503,
                    b'{"detail":"Corral is starting. Please retry shortly."}',
                    "application/json",
                )
            return
        try:
            body = self._request_body()
        except ValueError:
            self.respond(400, b'{"detail":"Invalid request body."}', "application/json")
            return
        # Preserve the public host, secure-cookie headers, CSRF headers and cookies.
        excluded = HOP_HEADERS | {
            item.strip().lower()
            for item in self.headers.get("Connection", "").split(",")
        }
        headers = {
            key: value for key, value in self.headers.items()
            if key.lower() not in excluded | {"content-length"}
        }
        headers["Content-Length"] = str(len(body))
        client = http.client.HTTPConnection("127.0.0.1", self.server.upstream_port, timeout=120)
        try:
            client.request(self.command, self.path, body=body, headers=headers)
            response = client.getresponse()
            payload = response.read()
            self.send_response(response.status)
            response_excluded = HOP_HEADERS | {
                item.strip().lower()
                for item in (response.getheader("Connection") or "").split(",")
            }
            for key, value in response.getheaders():
                if key.lower() not in response_excluded | {"content-length", "server", "date"}:
                    self.send_header(key, value)
            length = response.getheader("Content-Length") if self.command == "HEAD" else None
            self.send_header("Content-Length", length or str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if self.command != "HEAD":
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, http.client.HTTPException):
            self.respond(503, b'{"detail":"Corral is temporarily unavailable."}', "application/json")
        finally:
            client.close()

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = dispatch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--upstream-port", type=int, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--listening-file", type=Path)
    args = parser.parse_args()
    with StartupGateway((args.host, args.port), args.upstream_port, args.ready_file) as server:
        if args.listening_file:
            args.listening_file.write_text("listening")
        print(f"[startup] public startup listener is serving on {args.host}:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
