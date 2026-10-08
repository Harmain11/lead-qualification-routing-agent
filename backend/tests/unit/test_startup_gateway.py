from __future__ import annotations

import http.client
import json
import runpy
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

gateway_module = runpy.run_path(
    str(Path(__file__).resolve().parents[3] / "scripts" / "startup_gateway.py")
)
StartupGateway = gateway_module["StartupGateway"]


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass

    def do_GET(self) -> None:
        status = 500 if self.path == "/broken" else 200
        body = b"upstream application"
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "text/plain")
        self.send_header("Set-Cookie", "session=example; Secure; HttpOnly; SameSite=None")
        self.send_header("Set-Cookie", "csrf=example; Secure; SameSite=None")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = json.dumps({
            "body": data.decode(),
            "host": self.headers.get("Host"),
            "cookie": self.headers.get("Cookie"),
            "csrf": self.headers.get("X-CSRF-Token"),
            "workspace": self.headers.get("X-Workspace-ID"),
            "proto": self.headers.get("X-Forwarded-Proto"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def servers(tmp_path: Path):
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    marker = tmp_path / "ready"
    gateway = StartupGateway(("127.0.0.1", 0), upstream.server_port, marker)
    for server in (gateway, upstream):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield gateway, marker
    finally:
        for server in (gateway, upstream):
            server.shutdown()
            server.server_close()


def request(gateway, path="/", *, method="GET", body=None, headers=None):
    client = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
    try:
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        return response.status, response.getheaders(), response.read()
    finally:
        client.close()


def test_cold_start_root_is_usable_but_api_remains_unready(servers):
    gateway, _marker = servers
    status, headers, body = request(gateway, "/?view=leads&lead_tab=ai")
    assert status == 200
    assert b"Corral is starting" in body
    assert b"location.reload()" in body
    assert dict(headers)["Cache-Control"] == "no-store"
    assert request(gateway, "/health/ready")[0] == 503
    assert request(gateway, "/v1/leads")[0] == 503
    status, _headers, body = request(gateway, "/_corral/startup")
    assert status == 503 and json.loads(body) == {"ready": False}


def test_ready_backend_switches_without_gateway_restart(servers):
    gateway, marker = servers
    assert request(gateway)[0] == 200
    marker.touch()
    status, headers, body = request(gateway)
    assert status == 200 and body == b"upstream application"
    assert len([value for key, value in headers if key == "Set-Cookie"]) == 2
    assert json.loads(request(gateway, "/_corral/startup")[2]) == {"ready": True}
    # Application errors must remain visible, not be disguised as startup success.
    assert request(gateway, "/broken")[0] == 500


def test_proxy_preserves_auth_csrf_body_and_public_origin(servers):
    gateway, marker = servers
    marker.touch()
    status, _headers, body = request(gateway, "/v1/leads", method="POST",
                                    body='{"name":"Example"}', headers={
        "Host": "corral.example.test",
        "Cookie": "session=example",
        "X-CSRF-Token": "csrf-example",
        "X-Workspace-ID": "workspace-example",
        "X-Forwarded-Proto": "https",
        "Content-Type": "application/json",
    })
    assert status == 200
    assert json.loads(body) == {
        "body": '{"name":"Example"}', "host": "corral.example.test",
        "cookie": "session=example", "csrf": "csrf-example",
        "workspace": "workspace-example", "proto": "https",
    }


def test_startup_head_has_no_body(servers):
    gateway, _marker = servers
    status, headers, body = request(gateway, method="HEAD")
    assert status == 200 and body == b""
    assert int(dict(headers)["Content-Length"]) > 0
