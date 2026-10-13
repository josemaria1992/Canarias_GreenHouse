"""Remote-route check: `python -m aquacrop netcheck --url https://<host>/` (UI-06).

Run it from the computer that will use the application remotely (at home, with OpenVPN connected) or
on the greenhouse LAN. It checks, in order: the address is HTTPS, the name resolves (and to which
addresses), a TCP connection reaches the port, the TLS certificate is valid for the name and trusted
(the system store or `--ca-file`, such as the greenhouse proxy's private root), how long it remains
valid, that the application's health endpoint answers over that connection, and that plain HTTP on
port 80 does not serve the application. It sends no credentials and changes nothing: VPN reachability
does not identify a user; sign-in stays with the application account.
"""
from __future__ import annotations

import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

WARN_DAYS = 30


def _line(results: list, name: str, ok: bool | None, detail: str) -> bool | None:
    results.append({"check": name, "result": "PASS" if ok else ("WARN" if ok is None else "FAIL"), "detail": detail})
    return ok


def run(url: str, *, ca_file: str | None = None, timeout: float = 5.0, check_http: bool = True) -> list[dict]:
    res: list[dict] = []
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname:
        _line(res, "HTTPS address", False, f"{url!r}: use https://<name>/ (plain HTTP is never exposed beyond this PC)")
        return res
    host, port = u.hostname, u.port or 443
    base = f"https://{u.netloc}{u.path.rstrip('/')}"
    _line(res, "HTTPS address", True, base + "/")
    try:
        addrs = sorted({ai[4][0] for ai in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except socket.gaierror as e:
        _line(res, "name resolves", False, f"{host}: {e}. On the VPN, check the pushed DNS server or the hosts file")
        return res
    _line(res, "name resolves", True, f"{host} -> {', '.join(addrs)}")
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as e:
        _line(res, "TCP connection", False, f"{host}:{port}: {e}. Check the VPN is connected, the route to the "
              "greenhouse LAN is pushed, and the controller's firewall allows the VPN subnet on this port")
        return res
    _line(res, "TCP connection", True, f"{host}:{port} in {1000 * (time.monotonic() - t0):.0f} ms")
    ctx = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw, \
                ctx.wrap_socket(raw, server_hostname=host) as tls:
            cert, version = tls.getpeercert(), tls.version()
    except ssl.SSLCertVerificationError as e:
        _line(res, "TLS certificate", False, f"{e.verify_message or e}. Install the greenhouse proxy's root certificate "
              "(or pass --ca-file) and use the exact name the certificate was issued for")
        return res
    except (ssl.SSLError, OSError) as e:
        _line(res, "TLS certificate", False, f"TLS handshake failed: {e}")
        return res
    subject = dict(x[0] for x in cert.get("subject", ()))
    issuer = dict(x[0] for x in cert.get("issuer", ()))
    expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc)
    days = (expires - datetime.now(timezone.utc)).days
    _line(res, "TLS certificate", True, f"{version}; valid for {host}; subject {subject.get('commonName', '-')}; "
          f"issuer {issuer.get('commonName', issuer.get('organizationName', '-'))}")
    _line(res, "certificate validity", True if days > WARN_DAYS else None,
          f"expires {expires:%Y-%m-%d} ({days} days)" + ("" if days > WARN_DAYS else "; renew it soon"))
    try:
        req = urllib.request.Request(base + "/api/health", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            body = json.load(r)
        ok = bool(body.get("ok"))
        _line(res, "application health", ok, f"/api/health {json.dumps(body, sort_keys=True)}" +
              ("" if ok else ": the application answers but reports a problem; sign in locally and check Settings"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        _line(res, "application health", False, f"/api/health: {e}. The proxy answers TLS but not the application: "
              "check the AquaCrop service and the proxy's upstream 127.0.0.1:8000")
    if check_http:
        try:
            with urllib.request.urlopen(f"http://{host}/api/health", timeout=timeout) as r:
                served = r.status == 200 and r.geturl().startswith("http://")
            _line(res, "no plain HTTP", not served, "port 80 serves the application without TLS: close it or "
                  "redirect it to HTTPS" if served else "port 80 redirects to HTTPS")
        except urllib.error.HTTPError as e:
            _line(res, "no plain HTTP", True, f"port 80 answers {e.code} without the application")
        except (urllib.error.URLError, OSError):
            _line(res, "no plain HTTP", True, "port 80 closed")
    return res


def cmd_netcheck(args) -> int:
    res = run(args.url, ca_file=args.ca_file, timeout=args.timeout, check_http=not args.skip_http)
    if args.json:
        print(json.dumps({"url": args.url, "checked_utc": datetime.now(timezone.utc).isoformat(), "checks": res},
                         indent=1))
    else:
        for r in res:
            print(f"{r['result']:4}  {r['check']}: {r['detail']}")
    failed = [r for r in res if r["result"] == "FAIL"]
    if not args.json:
        print("route OK: sign in with your own application account" if not failed else f"{len(failed)} check(s) failed",
              file=sys.stderr if failed else sys.stdout)
    return 1 if failed else 0


def add_parser(sub) -> None:
    n = sub.add_parser("netcheck", help="check the HTTPS route to the application (from home over the VPN, or on the LAN)")
    n.add_argument("--url", required=True, help="the application's HTTPS address, e.g. https://aquacrop.lan/")
    n.add_argument("--ca-file", help="trust this root certificate (the greenhouse proxy's private CA) for this check")
    n.add_argument("--timeout", type=float, default=5.0)
    n.add_argument("--skip-http", action="store_true", help="do not probe plain HTTP on port 80")
    n.add_argument("--json", action="store_true")
    n.set_defaults(fn=cmd_netcheck)
