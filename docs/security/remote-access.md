# Remote access: OpenVPN to the greenhouse LAN, TLS to the application (UI-06)

Status: written for this release, not yet performed. The home-to-greenhouse check is **NOT_RUN** until
the controller is installed and your OpenVPN reaches its network. The route test
(`aquacrop netcheck`) is tested against a local HTTPS endpoint only (`tests/integration/test_netcheck.py`).

## The three layers

| Layer | What it does | What it does not do |
|---|---|---|
| Your existing **OpenVPN** | carries your PC's traffic into the greenhouse network | identify you to the application or authorize any command |
| **TLS proxy** (Caddy) on the controller, port 443 | encrypts the browser connection and proves the controller's name | expose anything else; the application stays on 127.0.0.1:8000 |
| **Application sign-in** | your own account (viewer, operator or administrator) | care whether you are at home or in the greenhouse: the same role has the same rights |

Plain HTTP is never exposed beyond the controller itself: the server refuses a non-loopback bind
without TLS. No port forwarding on any router. The internet is not needed for local operation: on
the greenhouse LAN the same HTTPS address works with no internet at all (the certificate comes from
the controller's own private authority).

Hardware writes stay impossible remotely as they are locally (`REAL — READ ONLY`; a bench lease
needs a commissioning user on an isolated bench, ADR-0003).

## What has to be known on site (fill in; do not guess)

| Item | Example only | Your value |
|---|---|---|
| Where your OpenVPN server runs (greenhouse router, a firewall, a server elsewhere with a site link) | greenhouse router | |
| Greenhouse LAN subnet | 192.168.50.0/24 | |
| OpenVPN client subnet | 10.8.0.0/24 | |
| Controller's LAN address (fixed: DHCP reservation or static) | 192.168.50.20 | |
| Name for the application | `aquacrop.lan` | |
| Who gets a VPN profile (one per person, never shared) | | |

## 1. OpenVPN: reach the greenhouse LAN

Keep your existing server; only these points matter for AquaCrop.

- **Route**: clients must get a route to the greenhouse LAN. On a routed (`dev tun`) server:
  `push "route 192.168.50.0 255.255.255.0"`. Do not push `redirect-gateway` just for this.
- **Return path**: the controller must reach the VPN client subnet. Either the VPN server is the LAN's
  default gateway, or add a route on the LAN router towards the VPN server for the client subnet.
- **Name**: either push a LAN DNS server that resolves `aquacrop.lan`
  (`push "dhcp-option DNS 192.168.50.1"`), or add one line to each client's hosts file:
  `192.168.50.20  aquacrop.lan` (Windows: `C:\Windows\System32\drivers\etc\hosts`).
- **Profiles**: one client certificate per person, revocable. A lost laptop means revoking its VPN
  certificate and its application sessions (Settings, Users, revoke sessions).

## 2. Controller: TLS proxy and firewall

After the application is installed (`deploy/aquacrop-cm4.py install`):

```text
sudo apt install caddy                                     # once; needs internet
sudo python3 deploy/aquacrop-cm4.py tls --hostname aquacrop.lan
```

That writes `/etc/caddy/Caddyfile` (`tls internal`, reverse proxy to 127.0.0.1:8000), and a systemd
drop-in that starts the application with `--behind-tls-proxy`. Forwarded headers are trusted only
from 127.0.0.1. Then, by hand:

1. In the installation config add `aquacrop.lan` to `security.allowed_hosts` and
   `https://aquacrop.lan` to `security.allowed_origins`. Validate it
   (`python -m aquacrop config validate --config ...`), then restart: `sudo systemctl restart aquacrop-greenhouse`.
2. Copy Caddy's root certificate
   `/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt` to each operator PC.
   - Windows: double-click it, *Install Certificate*, *Local Machine*, *Trusted Root Certification
     Authorities*.
   - Record its expiry. The root lasts about ten years; leaf certificates renew themselves offline.
3. Firewall: allow 443 only from the LAN and the VPN client subnet, and SSH only from where you
   administer. Use `deploy/nftables-aquacrop.conf.example` and fill in your subnets.

## 3. Route test (from the LAN, then from home)

On a PC with the root certificate saved as `root.crt`, with the VPN connected when testing from home:

```text
python -m aquacrop netcheck --url https://aquacrop.lan/ --ca-file root.crt
```

| Check | What a PASS means |
|---|---|
| HTTPS address | the address is `https://` |
| name resolves | the VPN's DNS or the hosts file gives the controller's address |
| TCP connection | the route to the greenhouse LAN and the firewall let you through to 443 |
| TLS certificate | the certificate is for this name and comes from your controller's authority |
| certificate validity | days left (WARN under 30) |
| application health | the application itself answers through the proxy |
| no plain HTTP | port 80 does not serve the application without TLS |

The tool sends no credentials and changes nothing. Then sign in with your own account and check that
your role behaves the same as on the LAN: a viewer cannot change anything, an operator can
acknowledge, and so on.

Record the result, the date, the PC and the network (LAN or VPN) in `CP-00_platform_record.md`. The
real check stays NOT_RUN until it has been done there.

## Alternative not chosen: Tailscale

The earlier draft documented Tailscale Serve. Your existing OpenVPN is the chosen route. If Tailscale
is ever used instead, the same rules hold: only Tailscale Serve to 127.0.0.1:8000, **never Funnel**
(public), access restricted to named users, and application sign-in unchanged. Identity headers from
any proxy are ignored by the application.
