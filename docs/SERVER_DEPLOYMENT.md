# Swagner Server: Tailscale and Forgejo deployment

Production runs as `openfotoframe` in Compose project `swagner-home`, using
`/home/awagner/Swagner-Server/stacks/home.compose.yml`. Do not run this repository's
standalone Compose file on this server: Traefik already owns ports 80 and 443.
The test instance is independent and is not a deployment target.

## Private access

Open **https://frame.tail5e8630.ts.net/upload** on a Tailscale-connected device.
The existing TSDProxy container supplies HTTPS and the authenticated Tailscale
identity. There is no Funnel/public exposure of this endpoint. The production
service has these Docker labels:

```yaml
tsdproxy.enable: 'true'
tsdproxy.name: frame
tsdproxy.port.1: 443/https:5000/http
```

The server's production Compose definitions map Andrew's Tailscale login to the
existing local `admin` account through `AUTHELIA_USER_MAP`, alongside the existing
Authelia mappings. With the current SSO-aware development version, Tailscale
access receives the application's ordinary user role because TSDProxy does not
assert Authelia's `admins` group. Administrative settings remain available through
the normal SSO route at **https://frame.swagner.tech**. Unmapped identities are
rejected. Versions without the SSO bridge use the application's own login.

The canonical `frame.swagner.tech` URL also supports the server's existing
Tailscale split DNS; see the parent repository's `tailscale/README.md`.

## Deployment trigger

`.forgejo/workflows/deploy.yml` tests pull requests and pushes to `main` with
Python 3.14. After tests pass, pushes/merges to `main` and manual main-branch
workflow dispatches invoke the server over SSH through the runner's private
Docker network. Pull requests cannot invoke deployment.

`OPENFOTOFRAME_DEPLOY_SSH_KEY` is a Forgejo repository Actions secret. Its key is
restricted in the server's `~/.ssh/authorized_keys` to the deployment command and
connections from Docker's `172.20.0.0/16` subnet. The workflow pins the server's
SSH host key. It cannot obtain an interactive shell or forward ports.

The installed script is `~/.local/state/openfotoframe-cd/deploy`, copied from
`scripts/deploy-from-forgejo.sh`. Reviewed script updates must be installed at
that path explicitly. Configuration and state live alongside it:

- `branch`: allowed production branch, initially `main`.
- `minimum-sha`: production commit that new deployments must contain. This
  prevents rolling production back to an older main branch before its current
  development work has been merged. Update only after deliberately reviewing
  the production baseline.
- `pending-sha`: tested commit deferred by the TV schedule.
- `deployed-sha`: last commit whose container passed health checks.
- `releases/<sha>/`: source archives; the development checkout is never reset.
- `deploy.log`: output from scheduled maintenance-window retries.

The script serializes deployments, verifies the requested SHA is still the
branch tip, archives that exact commit, builds a revision-tagged image, and runs
pytest inside an isolated container without production volumes. It then recreates
only `photo-frame`, retaining the existing upload/data volumes, routing, and
environment. A failed start or health check restores the prior image. Other
services and the test instance are untouched.

When TV schedules disallow deployment, the SHA stays queued and a user cron job
retries every five minutes. Newer tested commits replace older pending commits;
superseded revisions are skipped. Failed tests and health checks return errors.

## Server recovery

Restore the existing server Compose configuration, Docker volumes, Tailscale
state, and `~/.local/state/openfotoframe-cd` with private file permissions. Restore
the deployment key restriction and Forgejo Actions secret if needed. Install the
script with:

```bash
install -m 700 scripts/deploy-from-forgejo.sh \
  /home/awagner/.local/state/openfotoframe-cd/deploy
```

The user crontab should contain:

```cron
*/5 * * * * /home/awagner/.local/state/openfotoframe-cd/deploy --pending >> /home/awagner/.local/state/openfotoframe-cd/deploy.log 2>&1
```

After a host SSH key rotation, update the pinned public host key in the workflow.
After a Docker subnet change, update both the SSH source restriction and workflow
connection address. Private keys and application data must never be committed.
