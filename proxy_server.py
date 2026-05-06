"""
Local HTTP/CONNECT proxy with direct SOCKS5 implementation.
Routes through Tor (SOCKS5) or direct depending on current mode.
SOCKS5 is hand-rolled so .onion hostnames are NEVER passed to local DNS.
"""

import json
import os
import socket
import select
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

from config.settings import TOR_SOCKS_HOST, TOR_SOCKS_PORT
from url_utils import is_onion_host

LOCAL_HOST = "127.0.0.1"
TUNNEL_IDLE_TIMEOUT = 120

TOR_HOST = TOR_SOCKS_HOST
TOR_PORT = TOR_SOCKS_PORT

REWRITE_CONTENT_TYPES = (
    "text/html",
    "text/css",
    "javascript",
    "application/json",
    "text/plain",
)

# CSP directives that break HTTP subresources on onion pages — remove only these
_CSP_BLOCKING_DIRECTIVES = frozenset({
    "upgrade-insecure-requests",
    "block-all-mixed-content",
})

HOME_HOST = "index.new"
HOME_PAGE = os.path.join(os.path.dirname(__file__), "ui", "home.html")

_lock = threading.Lock()
_port: int = 0
_state = None
_tor_client = None


def get_port() -> int:
    return _port


def set_tor_mode(enabled: bool):
    # Kept for backwards-compat with tests that don't use BrowserState.
    # In normal app usage BrowserState is the source of truth.
    pass


def is_tor_mode() -> bool:
    if _state is not None:
        return _state.is_tor_mode()
    return False


def configure_state(state):
    global _state
    _state = state


def configure_tor_client(tor_client):
    global _tor_client
    _tor_client = tor_client


def is_tor_connected() -> bool:
    if _tor_client is None:
        return False
    try:
        return bool(_tor_client.is_connected())
    except Exception:
        return False


def _status_payload() -> bytes:
    mode_is_tor = is_tor_mode()
    return json.dumps({
        "mode": "tor" if mode_is_tor else "clear",
        "tor_mode": mode_is_tor,
        "tor_connected": is_tor_connected(),
    }).encode()


# ── SOCKS5 implementation ──────────────────────────────────────────────────

def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise RuntimeError("Connection closed during SOCKS5 handshake")
        buf += chunk
    return buf


def _set_tcp_options(s: socket.socket):
    try:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass


def _socks5_connect(host: str, port: int) -> socket.socket:
    """
    Open a connection to host:port through Tor's SOCKS5 proxy.
    The hostname is sent as raw bytes to Tor (ATYP=0x03 / DOMAINNAME),
    so .onion addresses are NEVER resolved locally — Tor handles all DNS.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(120)  # Tor circuits can be slow; 120s prevents mid-transfer cuts
    _set_tcp_options(s)
    s.connect((TOR_HOST, TOR_PORT))

    # Step 1 — greeting: VER=5, NMETHODS=1, METHOD=0x00 (no auth)
    s.sendall(b"\x05\x01\x00")
    reply = _recv_exact(s, 2)
    if reply[0] != 0x05 or reply[1] != 0x00:
        raise RuntimeError(f"SOCKS5 auth negotiation failed: {reply.hex()}")

    # Step 2 — CONNECT request with domain name type
    host_b = host.encode("ascii")
    if len(host_b) > 255:
        raise ValueError(f"Hostname too long ({len(host_b)} bytes): {host}")

    req = (
        b"\x05"                  # VER = 5
        b"\x01"                  # CMD = CONNECT
        b"\x00"                  # RSV
        b"\x03"                  # ATYP = DOMAINNAME
        + bytes([len(host_b)])   # 1-byte length prefix
        + host_b                 # hostname (sent verbatim to Tor, no DNS lookup)
        + port.to_bytes(2, "big")
    )
    s.sendall(req)

    # Step 3 — read response
    resp = _recv_exact(s, 4)
    if resp[1] != 0x00:
        _err = {
            0x01: "general SOCKS server failure",
            0x02: "connection not allowed",
            0x03: "network unreachable",
            0x04: "host unreachable",
            0x05: "connection refused",
            0x06: "TTL expired",
            0x07: "command not supported",
            0x08: "address type not supported",
        }
        raise RuntimeError(f"Tor refused: {_err.get(resp[1], f'code {resp[1]:02x}')}")

    # Step 4 — skip the bound address field
    atyp = resp[3]
    if atyp == 0x01:       # IPv4: 4 + 2
        _recv_exact(s, 6)
    elif atyp == 0x03:     # domain: 1-byte len + domain + 2
        dlen = _recv_exact(s, 1)[0]
        _recv_exact(s, dlen + 2)
    elif atyp == 0x04:     # IPv6: 16 + 2
        _recv_exact(s, 18)

    return s


def _direct_connect(host: str, port: int) -> socket.socket:
    """Plain TCP connection using system DNS (clear mode)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(30)
    _set_tcp_options(s)
    s.connect((host, port))
    return s


def _make_connection(host: str, port: int) -> socket.socket:
    # .onion addresses can only be reached through Tor — always use SOCKS5
    # regardless of the mode flag so the user doesn't have to manually toggle.
    if is_tor_mode() or is_onion_host(host):
        return _socks5_connect(host, port)
    return _direct_connect(host, port)


def _split_http_response(response: bytes) -> tuple[bytes, bytes]:
    marker = b"\r\n\r\n"
    idx = response.find(marker)
    if idx == -1:
        return response, b""
    return response[:idx], response[idx + len(marker):]


def _decode_chunked_body(body: bytes) -> bytes:
    decoded = bytearray()
    pos = 0
    while True:
        line_end = body.find(b"\r\n", pos)
        if line_end == -1:
            raise ValueError("Invalid chunked response")
        size_line = body[pos:line_end].split(b";", 1)[0].strip()
        size = int(size_line, 16)
        pos = line_end + 2
        if size == 0:
            break
        decoded.extend(body[pos:pos + size])
        pos += size
        if body[pos:pos + 2] != b"\r\n":
            raise ValueError("Invalid chunk terminator")
        pos += 2
    return bytes(decoded)


def _header_value(headers: bytes, name: str) -> str:
    prefix = name.lower().encode("ascii") + b":"
    for line in headers.split(b"\r\n")[1:]:
        if line.lower().startswith(prefix):
            return line.split(b":", 1)[1].strip().decode("latin-1", "replace")
    return ""


def _replace_header(headers: bytes, name: str, value: str) -> bytes:
    prefix = name.lower().encode("ascii") + b":"
    lines = []
    found = False
    for line in headers.split(b"\r\n"):
        if line.lower().startswith(prefix):
            if not found:
                lines.append(f"{name}: {value}".encode("latin-1"))
                found = True
        else:
            lines.append(line)
    if not found:
        lines.append(f"{name}: {value}".encode("latin-1"))
    return b"\r\n".join(lines)


def _remove_header(headers: bytes, name: str) -> bytes:
    prefix = name.lower().encode("ascii") + b":"
    return b"\r\n".join(
        line for line in headers.split(b"\r\n")
        if not line.lower().startswith(prefix)
    )


def _strip_csp_blocking_directives(headers: bytes, name: str) -> bytes:
    """Remove only upgrade-insecure-requests and block-all-mixed-content from a CSP header.
    Preserves the rest of the policy (XSS protection, etc.)."""
    prefix = name.lower().encode("ascii") + b":"
    lines = []
    for line in headers.split(b"\r\n"):
        if line.lower().startswith(prefix):
            value = line.split(b":", 1)[1].decode("latin-1", "replace")
            kept = [
                d.strip() for d in value.split(";")
                if d.strip() and d.strip().lower() not in _CSP_BLOCKING_DIRECTIVES
            ]
            if kept:
                lines.append(f"{name}: {'; '.join(kept)}".encode("latin-1"))
            # drop the header entirely if nothing survives
        else:
            lines.append(line)
    return b"\r\n".join(lines)


def _rewrite_onion_response(host: str, origin: str, response: bytes) -> bytes:
    if not is_onion_host(host):
        return response

    headers, body = _split_http_response(response)
    if not body:
        return response

    # Surgical CSP fix: only strip directives that block HTTP subresources.
    # Preserving the rest of CSP keeps XSS protection intact.
    headers = _strip_csp_blocking_directives(headers, "Content-Security-Policy")
    headers = _strip_csp_blocking_directives(headers, "Content-Security-Policy-Report-Only")

    if origin:
        headers = _replace_header(headers, "Access-Control-Allow-Origin", origin)
        headers = _replace_header(headers, "Access-Control-Allow-Credentials", "true")
        headers = _replace_header(headers, "Vary", "Origin")
    headers = _replace_header(headers, "Cache-Control", "no-store")
    headers = _replace_header(headers, "Pragma", "no-cache")

    content_type = _header_value(headers, "Content-Type").lower()
    content_encoding = _header_value(headers, "Content-Encoding").lower()
    transfer_encoding = _header_value(headers, "Transfer-Encoding").lower()
    can_rewrite_body = (
        not content_encoding and
        any(kind in content_type for kind in REWRITE_CONTENT_TYPES)
    )

    if can_rewrite_body:
        if "chunked" in transfer_encoding:
            try:
                body = _decode_chunked_body(body)
                headers = _remove_header(headers, "Transfer-Encoding")
            except (ValueError, IndexError):
                return headers + b"\r\n\r\n" + body
        host_b = host.encode("ascii")
        body = body.replace(b"https://" + host_b, b"http://" + host_b)
        body = body.replace(b"https:\\/\\/" + host_b, b"http:\\/\\/" + host_b)
        headers = _replace_header(headers, "Content-Length", str(len(body)))
    else:
        headers = _remove_header(headers, "Content-Length")

    return headers + b"\r\n\r\n" + body


# ── Proxy handler ──────────────────────────────────────────────────────────

class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    # ── Internal home page ────────────────────────────────────────────────

    def _is_home(self) -> bool:
        return self.headers.get("Host", "").split(":")[0] == HOME_HOST

    def _serve_home(self):
        if self.path == "/__status__":
            self._serve_status()
            return
        try:
            with open(HOME_PAGE, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self.send_error(404, "Home page not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _serve_status(self):
        body = _status_payload()
        origin = f"http://127.0.0.1:{_port}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def do_CONNECT(self):
        """HTTPS tunnel — browser sends CONNECT host:port, we bridge to origin."""
        try:
            host, port_str = self.path.rsplit(":", 1)
            port = int(port_str)
        except ValueError:
            self.send_error(400, "Bad CONNECT target")
            return

        remote = None
        try:
            remote = _make_connection(host, port)
            self.send_response(200, "Connection established")
            self.end_headers()
            self._tunnel(self.connection, remote)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                self.send_error(502, str(exc))
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            if remote:
                try:
                    remote.close()
                except OSError:
                    pass

    def do_GET(self):
        if self._is_home(): self._serve_home(); return
        self._forward_http()

    def do_POST(self):
        if self._is_home(): self._serve_home(); return
        self._forward_http()

    def do_HEAD(self):
        if self._is_home(): self._serve_home(); return
        self._forward_http()

    def do_PUT(self):
        self._forward_http()

    def _forward_http(self):
        """Forward a plain HTTP request.

        Onion hosts:
          - Force Accept-Encoding: identity so bodies can be rewritten.
          - Buffer the full response for URL rewriting before sending back.
        Clearnet hosts:
          - Forward the browser's original Accept-Encoding (gzip/brotli pass through).
          - Stream the response directly — no buffering, lower memory usage.

        WebSocket upgrade  → bidirectional _tunnel (frames go both ways)
        SSE (event-stream) → one-way stream with no socket timeout
        """
        host_header = self.headers.get("Host", "")
        if not host_header:
            self.send_error(400, "Missing Host header")
            return

        if ":" in host_header and not host_header.startswith("["):
            h, p = host_header.rsplit(":", 1)
            try:
                port = int(p)
            except ValueError:
                self.send_error(400, "Bad Host header")
                return
        else:
            h, port = host_header, 80

        # Chromium bypasses the proxy for loopback — serve internal endpoints.
        if h in ("127.0.0.1", "localhost") and port == _port:
            req_path = self.path.split("?")[0]
            if req_path == "/__status__":
                self._serve_status()
            else:
                self.send_error(403, "Direct proxy access not allowed")
            return

        # Strip scheme://host prefix → relative path for origin server
        path = self.path
        if "://" in path:
            idx = path.find("/", path.index("://") + 3)
            path = path[idx:] if idx != -1 else "/"

        is_onion = is_onion_host(h)
        is_ws = self.headers.get("Upgrade", "").lower() == "websocket"

        remote = None
        try:
            remote = _make_connection(h, port)

            remote.sendall(f"{self.command} {path} {self.request_version}\r\n".encode())

            # Forward headers. Strip hop-by-hop headers and, for onion hosts,
            # strip Accept-Encoding so we can force identity below.
            for k, v in self.headers.items():
                lower = k.lower()
                if lower in ("proxy-connection", "connection"):
                    continue
                if lower == "accept-encoding" and is_onion:
                    continue  # replaced with identity after the loop
                remote.sendall(f"{k}: {v}\r\n".encode())

            if is_onion:
                # Force plain bodies so the proxy can rewrite https:// → http://
                remote.sendall(b"Accept-Encoding: identity\r\n")
            remote.sendall(b"Connection: close\r\n")
            remote.sendall(b"\r\n")

            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                remote.sendall(self.rfile.read(content_length))

            if is_ws:
                remote.settimeout(None)
                self._tunnel(self.connection, remote)
                self.close_connection = True
            else:
                accept = self.headers.get("Accept", "")
                if "text/event-stream" in accept:
                    remote.settimeout(None)
                    while True:
                        chunk = remote.recv(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                elif is_onion:
                    # Buffer the full response so we can rewrite onion URLs.
                    chunks = []
                    while True:
                        chunk = remote.recv(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    response = b"".join(chunks)
                    response = _rewrite_onion_response(
                        h,
                        self.headers.get("Origin", ""),
                        response,
                    )
                    self.wfile.write(response)
                else:
                    # Clearnet: stream directly — no buffering, no memory spike.
                    while True:
                        chunk = remote.recv(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)

                self.close_connection = True

        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                self.send_error(502, str(exc))
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            if remote:
                try:
                    remote.close()
                except OSError:
                    pass

    @staticmethod
    def _tunnel(client: socket.socket, remote: socket.socket):
        """Bidirectional raw byte relay — used only for HTTPS CONNECT tunnels."""
        _set_tcp_options(client)
        _set_tcp_options(remote)
        client.setblocking(False)
        remote.setblocking(False)
        conns = [client, remote]
        last_activity = time.monotonic()
        while True:
            try:
                readable, _, errored = select.select(conns, [], conns, 30)
                if errored:
                    break
                if not readable:
                    if time.monotonic() - last_activity >= TUNNEL_IDLE_TIMEOUT:
                        break
                    continue
                for src in readable:
                    dst = remote if src is client else client
                    try:
                        data = src.recv(65536)
                        if not data:
                            return
                        dst.sendall(data)
                        last_activity = time.monotonic()
                    except (ConnectionResetError, BrokenPipeError, OSError):
                        return
            except Exception:
                break


# ── Server lifecycle ───────────────────────────────────────────────────────

class _ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


_server: _ThreadedServer | None = None


def start() -> int:
    global _server, _port
    # Bind to port 0 — OS picks a random free port, eliminating the fixed 8118
    # port that other local processes could predict or squat on.
    _server = _ThreadedServer((LOCAL_HOST, 0), ProxyHandler)
    _port = _server.server_address[1]
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    return _port


def stop():
    global _server
    if _server:
        server = _server
        _server = None
        server.shutdown()
        server.server_close()
