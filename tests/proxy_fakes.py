"""Local test servers for the proxy tests: a TLS "provider" with a throw-away test CA, and a CONNECT proxy.

Everything runs on 127.0.0.1. The certificates are generated with the ``openssl`` command line tool into a temp
folder and are used only by these tests (clearly labelled mock infrastructure, not a real provider).
"""

from __future__ import annotations

import base64
import json
import shutil
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OPENSSL = shutil.which("openssl")


def make_pki(folder: Path) -> dict:
    """CA + a leaf valid for localhost/127.0.0.1 + a leaf valid only for other.example (wrong host)."""
    def run(*args):
        subprocess.run([OPENSSL, *args], cwd=folder, check=True, capture_output=True)

    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "2",
        "-subj", "/CN=VYRXONE Test CA", "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    out = {"ca": str(folder / "ca.pem")}
    for name, san in (("good", "DNS:localhost,IP:127.0.0.1"), ("wrong", "DNS:other.example")):
        run("req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key", "-out", f"{name}.csr",
            "-subj", f"/CN={name}")
        (folder / f"{name}.ext").write_text(f"subjectAltName={san}\nbasicConstraints=CA:FALSE\n"
                                            "extendedKeyUsage=serverAuth\n")
        run("x509", "-req", "-in", f"{name}.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
            "-out", f"{name}.pem", "-days", "2", "-extfile", f"{name}.ext")
        out[name] = (str(folder / f"{name}.pem"), str(folder / f"{name}.key"))
    return out


class TlsProvider:
    """HTTPS server that answers like a Gemini-style endpoint and records what it received."""

    def __init__(self, cert: tuple[str, str], answer: str = "mock provider answer", redirect: bool = False) -> None:
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _handle(self):
                n = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(n) if n else b""
                outer.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                       "body": body})
                if redirect:
                    self.send_response(302)
                    self.send_header("Location", "https://other.example/steal")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                payload = json.dumps({"candidates": [{"content": {"parts": [{"text": answer}]},
                                                      "finishReason": "STOP"}],
                                      "models": [{"name": "models/mock-1"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*cert)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"https://localhost:{self.port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class ConnectProxy:
    """A minimal HTTP proxy that only understands CONNECT. Modes: ok | auth | status:<code> | silent | trailing."""

    def __init__(self, mode: str = "ok", user: str = "", password: str = "") -> None:
        self.mode, self.user, self.password = mode, user, password
        self.connects: list[str] = []          # raw CONNECT request heads
        self.relayed = bytearray()             # every byte that passed through the tunnel (client -> server)
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self._stop = False
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self) -> None:
        while not self._stop:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c: socket.socket) -> None:
        try:
            c.settimeout(5)
            buf = b""
            while b"\r\n\r\n" not in buf:
                d = c.recv(4096)
                if not d:
                    return
                buf += d
            head = buf.split(b"\r\n\r\n")[0].decode("latin-1")
            self.connects.append(head)
            lines = head.split("\r\n")
            target = lines[0].split(" ")[1]
            headers = {k.lower(): v for k, v in (l.split(": ", 1) for l in lines[1:] if ": " in l)}
            if self.mode == "silent":
                time.sleep(5)
                return
            if self.mode.startswith("status:"):
                code = int(self.mode.split(":")[1])
                c.sendall(f"HTTP/1.1 {code} Mock\r\nLocation: https://elsewhere.example/\r\n"
                          "Content-Length: 0\r\n\r\n".encode())
                return
            if self.mode == "auth":
                want = "Basic " + base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
                if headers.get("proxy-authorization") != want:
                    c.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                              b"Proxy-Authenticate: Basic realm=\"test\"\r\nContent-Length: 0\r\n\r\n")
                    return
            host, port = target.rsplit(":", 1)
            up = socket.create_connection((host, int(port)), timeout=5)
            reply = b"HTTP/1.1 200 Connection established\r\n\r\n"
            if self.mode == "trailing":
                reply += b"\x16\x03\x01injected"
            c.sendall(reply)
            c.settimeout(None)

            def pipe(a, b, record):
                try:
                    while True:
                        d = a.recv(65536)
                        if not d:
                            break
                        if record:
                            self.relayed.extend(d)
                        b.sendall(d)
                except OSError:
                    pass
                finally:
                    for s in (a, b):
                        try:
                            s.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass

            t = threading.Thread(target=pipe, args=(up, c, False), daemon=True)
            t.start()
            pipe(c, up, True)
        except OSError:
            pass
        finally:
            try:
                c.close()
            except OSError:
                pass

    def close(self) -> None:
        self._stop = True
        try:
            self.srv.close()
        except OSError:
            pass


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
