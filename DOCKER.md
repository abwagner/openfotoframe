# 🐳 Docker Deployment Guide

This guide explains how to run OpenFotoFrame in a Docker container.

## Prerequisites

- Docker Engine installed
- Docker Compose (usually included with Docker Desktop)
- For Raspberry Pi: Docker installed via `curl -fsSL https://get.docker.com | sh`

## Quick Start

### Option 1: Using Docker Compose (Recommended)

```bash
# Clone or copy the files to your server
cd off

# Start the container
docker compose up -d

# View logs
docker compose logs -f

# Stop the container
docker compose down
```

### Option 2: Using Docker CLI

```bash
# Build the image
docker build -t openfotoframe .

# Run the container
docker run -d \
  --name openfotoframe \
  --restart unless-stopped \
  -p 5000:5000 \
  -v photoframe_uploads:/app/uploads \
  -v photoframe_data:/app/data \
  openfotoframe
```

## Access the Application

Once running, access the photo frame at:

| Page | URL |
|------|-----|
| **Login** | http://your-server:5000/login |
| **Upload** | http://your-server:5000/upload |
| **Gallery** | http://your-server:5000/gallery |
| **TV Display** | http://your-server:5000/display |

### One-Time Administrator Credential

The username is `admin`. On a new installation, the application generates a random
one-time password in `/app/data/.initial_admin_password` with mode `0600`. The
installer consumes, deletes, and prints it once. For a manual Compose deployment:

```bash
docker compose exec photo-frame sh -c 'cat /app/data/.initial_admin_password; rm /app/data/.initial_admin_password'
```

Change it on first login to a password of at least 12 characters.

---

## Raspberry Pi Setup

### Install Docker on Raspberry Pi

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh

# Add your user to the docker group (logout/login after)
sudo usermod -aG docker $USER

# Install Docker Compose plugin
sudo apt-get install docker-compose-plugin
```

### Deploy on Raspberry Pi

```bash
# Copy files to Pi (from your computer)
scp -r off pi@raspberrypi.local:~/

# SSH into Pi
ssh pi@raspberrypi.local

# Start the container
cd off
docker compose up -d
```

### Auto-start Chromium Kiosk (on Pi Desktop)

Create a script to open the display in kiosk mode:

```bash
# Create autostart directory
mkdir -p ~/.config/autostart

# Create desktop entry
cat > ~/.config/autostart/photo-frame-kiosk.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=OpenFotoFrame Kiosk
Exec=/home/pi/start-kiosk.sh
X-GNOME-Autostart-enabled=true
EOF

# Create kiosk script
cat > ~/start-kiosk.sh << 'EOF'
#!/bin/bash
sleep 10  # Wait for Docker to start

# Disable screen blanking
xset s off
xset s noblank
xset -dpms

# Hide cursor
unclutter -idle 0.5 -root &

# Open Chromium in kiosk mode
chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --no-first-run \
    http://localhost:5000/display
EOF

chmod +x ~/start-kiosk.sh
```

---

## Configuration

### Environment Variables

Set these in `docker-compose.yml` or pass via `-e` flag:

| Variable | Description | Default |
|----------|-------------|---------|
| `TZ` | Timezone | `UTC` |
| `DISPLAY_SESSION_LIFETIME_SECONDS` | Display session lifetime | `2592000` |
| `BEHIND_PROXY` | Trust one proxy hop; only safe when Flask is exclusively behind a trusted proxy | unset |
| `SECURE_COOKIES` | Always mark session cookies Secure | unset |
| `AUTHELIA_URL` | Authelia portal URL; enables SSO when `BEHIND_PROXY=1` | unset |
| `AUTHELIA_USER_MAP` | Comma-separated `authelia_user=local_user` mappings | same username |
| `AUTHELIA_ADMIN_GROUPS` | Authelia groups granted the application admin role | `admins` |
| `CEC_AGENT_TOKEN` | Dedicated display-side CEC bearer token; generated in the data volume when unset | generated |
| `MAX_IMAGE_PIXELS` | Maximum decoded pixels per uploaded image | `80000000` |
| `MAX_IMAGE_DIMENSION` | Maximum decoded width or height | `20000` |

The bundled Compose configuration enables `BEHIND_PROXY=1` safely because the Flask
container does not publish a host port and is reachable externally only through Caddy.
Do not enable it for a directly exposed Flask process or container.

When Authelia is enabled, the reverse proxy must inject the `Remote-User` header
after successful forward-auth. Configure `AUTHELIA_USER_MAP` when Authelia and
OpenFotoFrame use different usernames; unmapped identities receive `403`.

MFA policy is managed by the administrator security-settings API. Passkeys require
a stable publicly trusted HTTPS hostname and matching canonical origin. Self-signed,
raw-IP, and localhost deployments can use TOTP but intentionally cannot enroll passkeys.

Set `ENABLE_HSTS=1` and `TRUSTED_HTTPS_HOSTNAME` only for a stable hostname with a
publicly trusted certificate. Leave HSTS disabled in the default self-signed mode and
for raw-IP or localhost access.

### Volumes

| Volume | Purpose |
|--------|---------|
| `photoframe_uploads` | Stores uploaded images and auto-generated thumbnails |
| `photoframe_data` | Stores settings, users, gallery metadata |

### Custom Port

To use a different port, modify `docker-compose.yml`:

```yaml
ports:
  - "8080:5000"  # Access on port 8080
```

---

## Maintenance

### View Logs

```bash
docker compose logs -f
```

### Restart Container

```bash
docker compose restart
```

### Update to New Version

```bash
# Pull latest code
git pull

# Rebuild and restart
docker compose down
docker compose build --no-cache
docker compose up -d
```

### Backup Data

The app has built-in **Dropbox backup** via the `/backup` page (admin only). See the [Backup section in the README](README.md#backup-dropbox) for setup instructions.

For a manual local backup of Docker volumes:

```bash
# Backup uploads and settings
docker run --rm \
  -v photoframe_uploads:/uploads \
  -v photoframe_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/photoframe-backup.tar.gz /uploads /data

# Restore
docker run --rm \
  -v photoframe_uploads:/uploads \
  -v photoframe_data:/data \
  -v $(pwd):/backup \
  alpine tar xzf /backup/photoframe-backup.tar.gz -C /
```

### Reset to Defaults

⚠️ **This deletes all photos and settings!**

```bash
docker compose down -v  # Remove containers AND volumes
docker compose up -d    # Fresh start
```

---

## Reverse Proxy (Optional)

### Nginx Example

```nginx
server {
    listen 80;
    server_name photos.yourdomain.com;

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        client_max_body_size 50M;  # Match Flask's max upload size
    }
}
```

### Traefik Labels

Add to `docker-compose.yml`:

```yaml
services:
  photo-frame:
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.photoframe.rule=Host(`photos.yourdomain.com`)"
```

---

## Troubleshooting

### Container won't start

```bash
# Check logs
docker compose logs

# Common issues:
# - Port 5000 already in use: change port in docker-compose.yml
# - Permission issues: check volume permissions
```

### Can't upload large files

The default max upload size is 50MB. If using a reverse proxy, ensure it also allows large uploads.

### Display page not loading

1. Check container is running: `docker compose ps`
2. Check logs: `docker compose logs`
3. Ensure port 5000 is not blocked by firewall

### Reset admin password

```bash
# Generate a new random one-time administrator password
docker compose exec photo-frame rm /app/data/users.json
docker compose restart
docker compose exec photo-frame sh -c 'cat /app/data/.initial_admin_password; rm /app/data/.initial_admin_password'
```

---

## Security Recommendations

1. **Consume the one-time password locally and change it** immediately after setup
2. **Use HTTPS** via reverse proxy in production
3. **Restrict network access** if only used locally
4. **Regular backups** of the data volume
5. **Keep Docker updated** for security patches

---

## Resource Usage

Typical resource consumption on Raspberry Pi 4:

- **Memory**: ~100-150MB
- **CPU**: <5% idle, spikes during uploads
- **Disk**: Base image ~200MB + your photos

---

## Architecture

```
┌─────────────────────────────────────────┐
│  Docker Container                       │
│  ┌─────────────────────────────────┐   │
│  │  Gunicorn (WSGI Server)         │   │
│  │  └── Flask Application          │   │
│  │      ├── /upload (auth req.)    │   │
│  │      ├── /gallery (auth req.)   │   │
│  │      ├── /display (open/token)  │   │
│  │      └── /admin/* (admin only)  │   │
│  └─────────────────────────────────┘   │
│                                         │
│  Volumes:                               │
│  /app/uploads ←→ photoframe_uploads     │
│  /app/data    ←→ photoframe_data        │
└─────────────────────────────────────────┘
         │
         ▼ Port 5000
┌─────────────────┐
│  Browser / TV   │
└─────────────────┘
```
