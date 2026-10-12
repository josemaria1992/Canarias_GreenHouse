# Security and remote access

## Accounts and sessions (implemented, tested in `tests/integration/test_api.py`)

- Individual local accounts, Argon2id hashes (`argon2-cffi`), minimum 12 characters.
- No default or committed credentials. The first admin comes from
  `aquacrop users bootstrap` (`--password-stdin`, or `--generate` for a one-time password that must
  be changed before any other action).
- Roles: viewer < operator < admin, plus a separate **commissioning** flag. Operators command the
  simulation only. Bench writes need the commissioning flag *and* a lease (see below). Proposed role
  assignments for real people and any remote physical rights need owner approval (TBD).
- Server-side sessions (only a SHA-256 of the token is stored), idle expiry (30 min) and absolute
  expiry (12 h) by default, logout and admin revocation, password change revokes every session.
- Cookie: HttpOnly, SameSite=Strict, Secure whenever the server is not on plain loopback HTTP.
- Every mutation needs a valid session, an allowed `Origin`/`Referer`, an `X-CSRF-Token` equal to
  HMAC(server secret, session) and passes a per-session rate limit. These checks run before the
  request body is parsed. Login is rate limited per client and per username.
- Rejected requests are recorded as `REQUEST_REJECTED` events with path, status, reason and client
  address. Passwords, tokens and session identifiers are never written to events.
- Labels and event text are rendered with `textContent` only; the Content-Security-Policy forbids
  inline script and style. CSV exports escape cells starting with `= + - @` tab or CR.
- `/api/health` is the only unauthenticated data route and returns three booleans.
- The OpenAPI document is at `/api/openapi.json` and needs a signed-in user.

## Bench write authorization

A bench lease is the only way a physical write frame can be authorized. It requires all of:
`HARDWARE_READ_ONLY` installation; commissioning flag; the typed phrase
`PRODUCTION LOADS DISCONNECTED`; a setup description; an exact allowlist of relay outputs; duration
1–30 min; a fresh read showing all eight relay modes NORMAL on every involved module; the lease row
committed to the database. It is bound to the browser session, the server process and the
configuration version. It ends on expiry, *End lease*, logout, password change, session revocation,
or user change, and is never restored after a restart. Ending sends nothing to the modules. No
configuration file, environment variable or command-line flag can arm or renew a lease.

## Network exposure

`serve` binds `127.0.0.1` by default and refuses any other address over plain HTTP. Never expose the
app with `--host 0.0.0.0`. For other machines, put a TLS reverse proxy in front and start the app
with `--behind-tls-proxy` (still bound to 127.0.0.1). Forwarded headers are trusted only from
127.0.0.1. Identity headers from proxies (for example Tailscale's `Tailscale-User-*`) are ignored;
users always sign in with their application account.

Add the hostname you will use to `security.allowed_hosts` and its `https://` origin to
`security.allowed_origins` in the installation config.

### Local LAN access with a private certificate (works with no internet)

Example with Caddy's internal CA (any TLS proxy works the same way):

```text
# /etc/caddy/Caddyfile
aquacrop.lan {
    tls internal
    reverse_proxy 127.0.0.1:8000
}
```

Install Caddy's root certificate (`/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt`)
on each operator PC once. The internal CA renews leaf certificates automatically; the root lasts
about ten years. Record its expiry. Restrict the proxy to the greenhouse LAN interface with the host
firewall. Do not configure port forwarding on any router.

### Remote access: Tailscale Serve (private), never Funnel

```text
sudo tailscale up                               # needs internet and a tailnet account, once
sudo tailscale serve --bg http://127.0.0.1:8000   # https://<host>.<tailnet>.ts.net -> app
tailscale serve status                          # must list only the tailnet HTTPS address
tailscale funnel status                         # must show nothing. Funnel is public: never enable it
```

Restrict access in the tailnet policy to named users or a group. Example only; adapt to the
tailnet's policy syntax and tags:

```json
{ "grants": [ { "src": ["group:aquacrop-operators"], "dst": ["tag:aquacrop"], "ip": ["443"] } ] }
```

Tailscale renews the HTTPS certificate itself. Tailscale availability is not needed for
acquisition, simulation, history or local LAN sign-in; if the tailnet is down, use the LAN path
above. Reference: <https://tailscale.com/docs/features/tailscale-serve>.

### Desktop demo

Plain HTTP on 127.0.0.1 with the loopback cookie exemption is for a single desktop only. The server
refuses that combination on any non-loopback bind.

## Secrets and files

Off-device backups (ADR-0012, not activated): the rclone configuration with the OneDrive token and
the obscured crypt keys lives at `/var/lib/aquacrop/rclone/rclone.conf` (0600, user `aquacrop`), never
in git; the plain crypt password and salt are escrowed away from the controller; the rclone wrapper
allows copy operations only and refuses a remote that is not a crypt remote
(`operations/offsite-backups.md`).

`secret.key` (session/CSRF HMAC key, created with mode 0600) and the database live in the state
directory; the systemd unit creates that directory with mode 0700. Back up the database with `backup create`; the secret is not part of backups. Losing it only
signs everyone out. Backups contain the Argon2id password hashes and are written with mode 0600;
keep any off-device copy equally private.

## Not covered by software

Physical access to the cabinet, the RS485 wiring, the USB service adapter and the CM4 console gives
full control of the modules regardless of this application. Software is not an emergency stop.
