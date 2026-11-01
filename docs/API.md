# API reference

Browser-session mutations require a current CSRF token in addition to authentication. CEC-agent endpoints use a separate Bearer credential.

## API Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/upload` | POST | User | Upload images |
| `/api/images` | GET | Display/User | Get enabled images (for display) |
| `/api/gallery` | GET | User | Get all images with metadata |
| `/api/gallery/<file>` | PATCH | User | Update image metadata (scale, etc.) |
| `/api/gallery/<file>` | DELETE | User | Delete an image |
| `/api/gallery/bulk` | POST | User | Bulk enable/disable/delete |
| `/api/check-duplicates` | POST | User | Check files for duplicates before upload |
| `/api/gallery/backfill-hashes` | POST | Admin | Compute perceptual hashes for existing images |
| `/api/settings` | GET/POST | User (POST) | Get or update settings |
| `/api/tv-schedules` | GET/POST | User/Admin | Get or save TV power schedules |
| `/api/cec/status` | GET | User | Check if CEC is available |
| `/api/cec/test` | POST | Admin | Send test CEC command (on/standby) |
| `/api/cec/register` | POST | CEC Bearer | Register a display-side CEC agent |
| `/api/cec/pending` | GET | CEC Bearer | Dequeue an `on` or `standby` command |
| `/api/cec/agent-token` | GET | Admin | Retrieve the separate CEC agent token (`no-store`) |
| `/api/network-info` | GET | Admin | Get local and Tailscale IP addresses |
| `/api/maintenance-window` | GET | None | Check if deploy is safe (TV off) |
| `/api/reorder` | POST | User | Reorder images |
| `/api/display/enroll` | POST | Enrollment secret | Establish a display session |
| `/api/display/enrollment-secret` | GET | Admin | Get the enrollment secret (`no-store`) |
| `/api/display/rotate-secret` | POST | Admin | Rotate enrollment and invalidate sessions |
| `/api/display/state` | GET | Display/User | Get current slideshow state (index, paused) |
| `/api/display/control` | POST | Display/User | Control slideshow (next, prev, pause, play) |
| `/api/groups` | GET/POST | User | List or create image groups |
| `/api/groups/<id>` | PATCH/DELETE | User | Update or delete an image group |
| `/api/backup/status` | GET | Admin | Get backup configuration status |
| `/api/backup/configure` | POST/DELETE | Admin | Connect or disconnect Dropbox |
| `/api/backup/run` | POST | Admin | Run a backup now |
| `/api/backup/restore` | POST | Admin | Restore from backup |
| `/api/backup/history` | GET | Admin | Get backup history |
| `/api/backup/settings` | POST | Admin | Update backup schedule/path |
| `/api/admin/users` | POST | Admin | Create user |
| `/api/admin/users/<user>` | DELETE | Admin | Delete user |
| `/api/admin/users/<user>/password` | POST | Admin | Reset password |

### Display-side CEC agent

The CEC agent does not use the display enrollment secret or browser session. It
authenticates only with `Authorization: Bearer <CEC_AGENT_TOKEN>`. In display-only
installations, place the token in `/etc/openfotoframe/cec-agent-token`, owned by
the kiosk service user and mode `0600`. The agent reads it from that file and passes
it to curl through standard input, so the value is absent from URLs and process
arguments. Set `CEC_AGENT_TOKEN_FILE` only when using a different protected path.

Existing installations may retain the named volumes (`photoframe_*`) while recreating
the app container as `openfotoframe`; this preserves all uploaded photos and settings.
The CEC agent also falls back to the former `/etc/pi-photo-frame/cec-agent-token`
path so existing display-only Pis continue to work. New host-side configuration uses
the OpenFotoFrame name.

[Back to overview](../README.md)
