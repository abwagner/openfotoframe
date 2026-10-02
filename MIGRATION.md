# Migration Guide: Single-Pi → Split Backend/Display

This guide covers migrating from a single Raspberry Pi running both the backend
(Flask + Caddy) and a Chromium kiosk display, to a split architecture where the
backend runs on a separate server and the Pi runs in display-only mode.

## Security release checklist

For upgrades from tokenized kiosk releases:

1. Back up the data and uploads volumes. The existing `.display_token` is retained
   only as the one-time enrollment secret.
2. Rebuild the backend, then update every kiosk to launch the clean `/display` URL.
3. Enter the display enrollment secret once on each remote display. Rotate it after
   all displays are migrated; rotation immediately invalidates earlier sessions.
4. Retrieve the separate CEC agent token, install it at
   `/etc/openfotoframe/cec-agent-token` with mode `0600`, and remove old display-token
   arguments from services and scripts.
5. Remove tokenized URLs from browser/shell history, generated scripts, service files,
   and retained proxy logs.
6. Consume the random one-time administrator password locally on new installations;
   all new and reset passwords must contain at least 12 characters.
7. Review MFA mode/method settings. Passkeys require a stable, matching trusted HTTPS
   RP ID and origin; TOTP remains available for self-signed and raw-IP deployments.
8. Leave HSTS disabled unless the configured exact hostname has a stable publicly
   trusted certificate. Enable `BEHIND_PROXY` only when Flask is exclusively behind
   a trusted proxy that overwrites forwarded headers.

Query-string display and CEC credentials are rejected; there is no indefinite
compatibility mode.

## Why split?

- Run the backend on a more powerful machine (server, NAS, VPS)
- Dedicate the Pi purely to displaying photos
- Access the admin UI from the server's domain with a proper TLS certificate
- Multiple display Pis pointing at one backend

## Architecture after migration

```
your-domain.com  (DNS A record, kept updated by Cloudflare DDNS)
       ↓
  Backend Server  ←────────────────────  Pi (display-only)
  Flask + Caddy                           Chromium kiosk
  Docker volumes                          points at your-domain.com/display
  (photos, settings, users)               optional: CEC agent for TV control
```

## Prerequisites

- The backend server must be reachable from the internet (or your local network,
  depending on where the Pi lives)
- Docker installed on the backend server (the install script handles this)
- If using Cloudflare for DNS: an API token with `Zone:DNS:Edit` permission and
  your zone ID (both available from the Cloudflare dashboard)

---

## Phase 1 — Stand up the backend server

> The Pi keeps running as normal during this phase. No downtime yet.

**1. Clone or copy the project to the server**

```bash
git clone https://github.com/your-repo/off.git
cd off
```

**2. Create your `.env` file**

```bash
cp .env.example .env
```

Edit `.env` with your values:

```
DOMAIN=photos.yourdomain.com
CLOUDFLARE_API_TOKEN=your-cloudflare-api-token
CLOUDFLARE_ZONE_ID=your-cloudflare-zone-id
```

If you are not using Cloudflare, omit `CLOUDFLARE_ZONE_ID` and point your DNS
A record at the server manually.

**3. Run the install script**

```bash
./scripts/install.sh
# Choose mode 1: Backend server
# Choose HTTPS mode 2: Let's Encrypt via Cloudflare (or 3 for DuckDNS)
```

The script will install Docker, build the containers, configure Caddy with a
trusted TLS certificate, and set up a Cloudflare DDNS cron job if a zone ID
was provided.

**4. Verify the backend is reachable**

Open `https://photos.yourdomain.com` in a browser. You should see the login
page. Default credentials: `admin` / `password`.

---

## Phase 2 — Migrate data from the Pi

> This phase causes a brief outage (roughly 5–10 minutes) while data is
> transferred. Do it during a time when the display being off is acceptable.

**On the Pi — stop the backend and export volumes**

```bash
cd ~/off
docker compose stop

docker run --rm \
  -v photoframe_uploads:/uploads \
  -v photoframe_data:/data \
  -v /tmp:/out \
  alpine tar czf /out/photoframe-backup.tar.gz uploads data
```

**Copy the archive to the server**

```bash
scp /tmp/photoframe-backup.tar.gz user@your-server:/tmp/
```

**On the server — restore into the new volumes**

```bash
cd /path/to/off
docker compose stop

docker run --rm \
  -v photoframe_uploads:/uploads \
  -v photoframe_data:/data \
  -v /tmp:/out \
  alpine sh -c "cd / && tar xzf /out/photoframe-backup.tar.gz"

docker compose start
```

This transfers all photos, settings, user accounts, and the display enrollment
secret. Existing tokenized display URLs are intentionally no longer accepted; each
remote display must enroll once to receive its new browser session.

**Verify the migration**

Log in to `https://photos.yourdomain.com` and confirm your photos and settings
are intact.

---

## Phase 3 — Switch the Pi to display-only mode

**Get the display enrollment secret from the server**

```bash
docker exec openfotoframe cat /app/data/.display_token
```

**On the Pi — run the install script in display-only mode**

```bash
cd ~/off
./scripts/install.sh
# Choose mode 2: Display only
# Backend URL: https://photos.yourdomain.com
# Chromium opens a clean /display URL and prompts for the secret once.
```

The script configures Chromium to launch a clean `/display` URL. Enter the secret
on the first-launch enrollment page; it is exchanged through POST and is not stored
in the generated script or Chromium process arguments.

Remove any old `?token=...` URLs from browser history, generated kiosk scripts,
desktop/service files, shell history, and retained reverse-proxy access logs.

---

## Phase 4 — Clean up the Pi

Remove the old backend containers to free resources. The kiosk setup is kept.

```bash
cd ~/off
docker compose down --rmi all --volumes
```

> **Note:** `--volumes` removes the local Docker volumes on the Pi. The data
> now lives on the server. Only do this after verifying the migration succeeded.

---

## Notes

### CEC TV power control

If you use the TV schedule feature (auto on/off via HDMI-CEC), be aware that
`/dev/cec0` is physically attached to the Pi, not the server. After migration,
the CEC agent runs on the Pi in display-only mode and polls the backend for
scheduled commands to execute locally. See the display-only install prompts
for setup. Retrieve the dedicated token from `GET /api/cec/agent-token` as an
administrator. The installer stores it in `/etc/openfotoframe/cec-agent-token`,
owned by the kiosk user with mode `0600`; it is never shared with display enrollment
or included in URLs, generated scripts, logs, or process arguments.

### Multiple display Pis

Each Pi runs `install.sh` in display-only mode pointing at the same backend
URL. Each display enrolls into its own signed session. Only one Pi should have CEC enabled
(the one physically connected to the TV you want to control).

### Maintenance window (deploy.sh)

The `deploy.sh` script checks `/api/maintenance-window` to avoid deploying
while the TV is on. This continues to work correctly — schedule data lives on
the backend and the endpoint is time-based.

### DNS

If you provided `CLOUDFLARE_ZONE_ID` in `.env`, the install script added a
cron job on the server that updates the Cloudflare A record every 6 hours.
You can also run it immediately:

```bash
./scripts/cloudflare-ddns.sh
```

If you want additional subdomains (e.g. `frigate.yourdomain.com`) to resolve
to the same server, add CNAME records in Cloudflare pointing at your main
domain — they will follow the DDNS-updated A record automatically.
