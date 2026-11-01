# Backups, updates and troubleshooting

Routine administration and recovery. Keep persistent data and uploads when updating containers.

## Managing the frame

These commands are run via SSH on the host machine:

| Task | Command |
|------|---------|
| View logs | `docker compose logs -f` |
| Restart | `docker compose restart` |
| Stop | `docker compose down` |
| Update code | `git pull && docker compose up -d --build` |
| Exit kiosk temporarily | Press `Ctrl+Alt+F1` to switch to terminal |
| Return to kiosk | Press `Ctrl+Alt+F7` to switch back to desktop |

## Backup (Dropbox)

The app supports cloud backup of your photos and settings to Dropbox via [rclone](https://rclone.org/).

### Setup

1. Navigate to `/backup` (admin only)
2. Generate a Dropbox OAuth token and paste it into the configuration form
3. The app writes an rclone config and verifies the connection

### Features

- **Manual backup/restore** — Run a backup or restore from the `/backup` page
- **Scheduled backups** — Set a daily backup time (default: 3:00 AM)
- **Custom path** — Choose the Dropbox folder path for backups
- **History** — View past backup results on the `/backup` page
- **Disconnect** — Remove the Dropbox connection at any time

### Requirements

- `rclone` must be installed in the Docker container (included in the Dockerfile)
- A Dropbox account with an OAuth token

## CI/CD Deployment

For the Swagner Server production instance, use the [Tailscale and Forgejo deployment guide](SERVER_DEPLOYMENT.md). It documents private access, tested main-branch deployments, maintenance-window retries, and rollback.

Automated deployment via GitHub Actions. When you push to `main`, tests run and (if enabled) the update is deployed to your Pi.

### Setup

1. In your GitHub repository, go to **Settings > Secrets and variables > Actions**

2. Add these **secrets**:

   | Secret | Value |
   |--------|-------|
   | `PI_HOST` | Your Pi's IP (Tailscale IP recommended) |
   | `PI_USER` | SSH username (e.g., `pi`) |
   | `PI_SSH_KEY` | Private SSH key for the Pi |
   | `PI_SSH_PORT` | SSH port (optional, defaults to 22) |

3. Add this **variable**:

   | Variable | Value |
   |----------|-------|
   | `DEPLOY_ENABLED` | `true` |

### Maintenance Window

Deploys automatically check if the TV is currently scheduled to be on. If it is, the deploy is skipped to avoid interrupting the slideshow. The deploy will proceed on the next push when the TV is off.

This uses the `/api/maintenance-window` endpoint which checks TV schedules. If no schedules are configured, deploys always proceed.

### Manual Deploy

SSH into the Pi and run:

```bash
cd ~/openfotoframe
./scripts/deploy.sh
```

This also checks the maintenance window before proceeding.

## Troubleshooting

### Reset all accounts

If you cannot recover administrator access, the following reset removes **all user accounts**, not photos or gallery settings. Back up the data volume before deliberately resetting accounts. Restarting generates a new one-time administrator credential:

```bash
docker compose exec photo-frame rm /app/data/users.json
docker compose restart
docker compose exec photo-frame sh -c 'cat /app/data/.initial_admin_password; rm /app/data/.initial_admin_password'
```

The final command consumes and displays the credential once on the local terminal.

### Photos not showing on display?

1. Check the gallery - photos might be hidden
2. Ensure at least one photo is set to "visible"
3. Check browser console for errors

### Can't access from other devices?

1. Ensure devices are on the same network
2. Use the server's IP address if `.local` hostname doesn't resolve
3. If using self-signed certificates, accept the browser warning
4. If using Let's Encrypt, ensure your DNS points to the Pi's IP

## Uninstall

To completely remove OpenFotoFrame (containers, images, volumes, cron jobs, and kiosk config):

```bash
cd ~/openfotoframe
./scripts/uninstall.sh
```

[Back to overview](../README.md)
