"""UI-06: the remote-route check (`aquacrop netcheck`) against a local HTTPS endpoint with a private CA.

A throwaway root CA and a server certificate for `localhost` are made with the openssl command (skipped
where it is missing); a small HTTPS server answers /api/health like the application behind its proxy.
This checks the tool, not a real VPN route: the home-to-greenhouse check stays NOT_RUN until the
endpoint exists."""
import json
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aquacrop.netcheck import run

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl command not available")


def _ca_and_cert(d):
    def o(*a):
        subprocess.run(["openssl", *a], check=True, capture_output=True, cwd=d)
    # Extensions as a real private CA issues them (Python 3.13 verifies X.509 strictly), each from its own
    # configuration file: the system's default openssl.cnf adds extensions of its own to `req -x509`, which
    # LibreSSL (the macOS openssl) then carries twice, and a certificate with a duplicated extension is no CA.
    (d / "ca.cnf").write_text("[req]\ndistinguished_name=dn\nprompt=no\nx509_extensions=v3_ca\n[dn]\n"
                              "CN=Test greenhouse CA\n[v3_ca]\nbasicConstraints=critical,CA:TRUE\n"
                              "keyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n", encoding="ascii")
    (d / "srv.cnf").write_text("[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=localhost\n", encoding="ascii")
    (d / "ext.cnf").write_text("subjectAltName=DNS:localhost\nbasicConstraints=critical,CA:FALSE\n"
                               "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                               "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n", encoding="ascii")
    o("req", "-config", "ca.cnf", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem",
      "-days", "30")
    o("req", "-config", "srv.cnf", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr")
    o("x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-out", "srv.pem",
      "-days", "20", "-extfile", "ext.cnf")
    return d / "ca.pem", d / "srv.pem", d / "srv.key"


class _Health(BaseHTTPRequestHandler):
    body = {"ok": True, "storage_ok": True, "acquisition_ok": True}

    def do_GET(self):
        data = json.dumps(self.body).encode() if self.path == "/api/health" else b"{}"
        self.send_response(200 if self.path == "/api/health" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def https(tmp_path):
    ca, cert, key = _ca_and_cert(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"https://localhost:{srv.server_address[1]}/", ca
    srv.shutdown()
    srv.server_close()


def _by(res):
    return {r["check"]: r for r in res}


def test_route_passes_with_the_private_root_and_reports_each_step(https):
    url, ca = https
    res = _by(run(url, ca_file=str(ca), check_http=False))
    version = subprocess.run(["openssl", "version"], capture_output=True, text=True).stdout.strip()
    assert [r["result"] for r in res.values()] == ["PASS", "PASS", "PASS", "PASS", "WARN", "PASS"], (version, res)
    assert "127.0.0.1" in res["name resolves"]["detail"] or "::1" in res["name resolves"]["detail"]
    assert "valid for localhost" in res["TLS certificate"]["detail"]
    assert "renew it soon" in res["certificate validity"]["detail"]         # a 20-day certificate
    assert '"ok": true' in res["application health"]["detail"]


def test_untrusted_certificate_plain_http_and_unreachable_port_fail_with_a_fix(https, tmp_path):
    url, _ = https
    res = _by(run(url, check_http=False))                     # without the private root
    assert res["TLS certificate"]["result"] == "FAIL" and "--ca-file" in res["TLS certificate"]["detail"]
    assert "application health" not in res                   # nothing is sent over an untrusted channel
    res = run("http://localhost:8000/")
    assert res[-1]["result"] == "FAIL" and "never exposed" in res[-1]["detail"]
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]                             # closed once the block ends
    res = _by(run(f"https://localhost:{port}/", check_http=False, timeout=2))
    assert res["TCP connection"]["result"] == "FAIL" and "VPN" in res["TCP connection"]["detail"]


def test_cli_exit_codes(https, capsys):
    from aquacrop.cli import main
    url, ca = https
    assert main(["netcheck", "--url", url, "--ca-file", str(ca), "--skip-http"]) == 0
    assert "route OK" in capsys.readouterr().out
    assert main(["netcheck", "--url", url, "--skip-http", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert any(c["result"] == "FAIL" for c in out["checks"])
