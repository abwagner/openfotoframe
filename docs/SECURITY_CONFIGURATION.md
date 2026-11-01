# Authentication and security configuration

For vulnerability reporting and the security policy, see [SECURITY.md](../SECURITY.md).

## First Login

New installations generate a cryptographically random one-time administrator
password. `scripts/install.sh` consumes it from the protected data volume, deletes
the plaintext file, and prints it once to the local terminal. The username is
`admin`. You must replace the one-time password with one of at least 12 characters
before accessing application features.

Every user creation, password change, and administrator reset uses the same
12-character minimum without composition rules. Security events are appended as
structured JSON to `/app/data/security-events.jsonl` (mode `0600`); passwords,
tokens, cookies, recovery material, and CSRF values are never included.

## Security

- Passwords are hashed with bcrypt
- Session keys are randomly generated
- HTTPS via Caddy (self-signed, Cloudflare, or DuckDNS Let's Encrypt)
- Secure cookies enabled behind the reverse proxy
- Display access uses an expiring HttpOnly browser session established by one-time enrollment
- Non-root user in Docker container

### Display Access

The display (`/display`) is accessible:
- From localhost (the machine itself)
- With a valid display session cookie established at `/display/enroll`
- When logged in

The enrollment secret is submitted once in a POST body. It is never accepted in a
query string and is not stored in browser history, image URLs, kiosk scripts, or
Chromium arguments. Rotating it from the administrator API immediately invalidates
existing display sessions. Display sessions default to 30 days and can be configured
with `DISPLAY_SESSION_LIFETIME_SECONDS`.

`BEHIND_PROXY=1` is safe only when Flask is reachable exclusively through a trusted
reverse proxy that overwrites forwarded headers. Do not enable it when Flask is
directly exposed to untrusted clients. The bundled Caddy/Compose layout meets this
requirement because only Caddy publishes host ports.

## Multi-factor authentication

MFA defaults to disabled for upgrade compatibility. Administrators configure it
through `GET/POST /api/admin/security-settings` with these values:

- `mfa_mode`: `disabled`, `optional`, `required_admins`, or `required_all`.
- `mfa_methods`: `totp`, `passkey`, or `either`.
- `webauthn_rp_id`: the stable HTTPS hostname, such as `frame.example.com`.
- `webauthn_origin`: the exact canonical origin, such as `https://frame.example.com`.

Users enroll at `/mfa`. TOTP secrets are encrypted with a protected local Fernet
key, verified before enrollment, and guarded against timestep replay. Recovery
codes are displayed once and only their hashes are stored. Passkeys support multiple
named credentials and last-used tracking. Passkey enrollment is disabled unless the
RP ID and canonical origin form a matching, stable HTTPS deployment; raw IP,
localhost, HTTP, and self-signed-warning workflows should use TOTP instead.

When both methods are enrolled and allowed, either TOTP/recovery or a passkey can
satisfy the second factor. Administrators can reset MFA with the CSRF-protected
`DELETE /api/admin/users/<username>/mfa` endpoint; existing secrets are never returned.

## Browser security policy

Flask sets `nosniff`, `no-referrer`, deny-framing, restricted permissions, private
no-store caching for sensitive responses, and an enforced self-only Content Security
Policy. Page scripts and stylesheet blocks are versioned static assets; executable
inline event attributes are not used. Dynamic photo sizing/cropping requires the
narrow `style-src-attr` exception, while style elements and scripts remain self-only
and script attributes are disabled.

HSTS is off by default. Enable `ENABLE_HSTS=1` only together with
`TRUSTED_HTTPS_HOSTNAME=photos.example.com` when that exact stable hostname uses a
publicly trusted certificate. Do not enable it for raw IPs, localhost, or self-signed
certificate modes.

## Image decoding limits

Uploads retain the 50 MB request cap and are fully decoded and verified before
perceptual hashing, thumbnailing, snapshots, or metadata changes. Pillow
decompression-bomb warnings, corrupt data, decoded images over 80 million pixels,
or either dimension over 20,000 pixels return a friendly 400 and leave no partial
file. The limits can be adjusted with `MAX_IMAGE_PIXELS` and `MAX_IMAGE_DIMENSION`;
the defaults accommodate ordinary modern phone photos.

[Back to overview](../README.md)
