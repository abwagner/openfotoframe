# Development reference

Use the [local stock-photo demo](LOCAL_DEMO.md) for screenshots. Native app setup, signing and simulator tests are in the [iOS guide](../ios/README.md).

## Development checks

Run the backend suite with `pytest`. With Node.js installed, run
`node --test tests/*.test.mjs` to check seeded geometry, varied
sizes, complete polygon coverage, whole circles, and shape controls.

## File Structure

```
openfotoframe/
├── app.py                  # Flask application
├── requirements.txt        # Python dependencies
├── Dockerfile              # Docker image definition
├── docker-compose.yml      # Docker Compose config (app + Caddy)
├── Caddyfile               # Caddy reverse proxy config (HTTPS)
├── .env.example            # HTTPS configuration template
├── caddy/
│   └── Dockerfile          # Custom Caddy build (DNS plugins)
├── scripts/
│   ├── install.sh          # One-command setup script
│   ├── deploy.sh           # Manual deploy script
│   ├── uninstall.sh        # Complete removal script
│   ├── cloudflare-ddns.sh  # DDNS updater (cron, every 6h)
│   └── restart-chromium.sh # Daily Chromium restart (cron)
├── .github/workflows/
│   └── deploy.yml          # CI/CD pipeline (test + deploy)
├── tests/                  # Test suite
├── uploads/                # Uploaded photos
│   └── thumbnails/         # Auto-generated 400px thumbnails
├── data/                   # Settings, users, gallery data
│   ├── settings.json
│   ├── users.json
│   ├── gallery.json
│   └── .secret_key
└── templates/
    ├── login.html
    ├── upload.html
    ├── gallery.html
    ├── display.html
    ├── admin_users.html
    ├── backup.html
    ├── change_password.html
    └── error.html
```

## Architecture

The multi-display design is described in [MULTI_DISPLAY_DESIGN.md](MULTI_DISPLAY_DESIGN.md).
The isolated deployed test instance is described in [TEST_INSTANCE.md](TEST_INSTANCE.md).

[Back to overview](../README.md)
