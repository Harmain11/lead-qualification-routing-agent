"""Fetch only reviewed public HTTPS pages, with pinned public DNS and bounded input."""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit

from lead_agent.domain.operations import safe_website


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, text: str) -> None:
        if not self.hidden and text.strip():
            self.parts.append(text.strip())


def public_address(host: str) -> str:
    addresses = {str(info[4][0]) for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise ValueError("Company research can access only public Internet addresses")
    return sorted(addresses)[0]


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str) -> None:
        super().__init__(host, timeout=10, context=ssl.create_default_context())
        self.public_ip = address

    def connect(self) -> None:
        # Pin the validated IP while keeping certificate validation and SNI on the hostname.
        raw = socket.create_connection((self.public_ip, 443), timeout=self.timeout)
        try:
            self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def fetch_company_page(url: str) -> dict[str, str]:
    safe_website(url)
    parts = urlsplit(url)
    if not parts.hostname:
        raise ValueError("A valid company hostname is required")
    host = parts.hostname.encode("idna").decode("ascii")
    connection = PublicHTTPSConnection(host, public_address(host))
    started = time.monotonic()
    try:
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        connection.request(
            "GET",
            path,
            headers={
                "Host": host,
                "Accept": "text/html",
                "Accept-Encoding": "identity",
                "User-Agent": "CorralCompanyResearch/1.0",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(
                "The reviewed website did not return a page. Update its "
                "canonical URL; redirects are not followed."
            )
        if "text/html" not in (response.getheader("Content-Type") or "").lower():
            raise ValueError("Company research requires an HTML page")
        if response.getheader("Content-Encoding") not in {None, "", "identity"}:
            raise ValueError("Compressed website responses are not accepted")
        content = bytearray()
        while len(content) <= 262144:
            if time.monotonic() - started > 20:
                raise ValueError("Company page exceeded the research time limit")
            block = response.read(8192)
            if not block:
                break
            content.extend(block)
        if len(content) > 262144:
            raise ValueError("Company page is too large; review a smaller company/about page")
        parser = PageText()
        parser.feed(bytes(content).decode("utf-8", errors="replace"))
        text = " ".join(parser.parts)[:16000]
        if len(text) < 80:
            raise ValueError("No usable company evidence was found on this page")
        return {"url": url, "text": text}
    finally:
        connection.close()
