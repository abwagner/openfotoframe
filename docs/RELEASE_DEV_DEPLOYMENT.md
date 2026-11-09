# Private development deployment

Use a separate Compose project and immutable image to test this release. Updating
production in place is not an appropriate first rehearsal: account credentials,
metadata, media authorization and background processing differ from the legacy
service. No production migration is implied by a dev deployment.

`deploy/release/compose.dev.yml` is a standalone private self-hosted dev template.
It has a distinct project, image, volume and loopback port, no public Traefik route,
first-party authentication, and bounded CPU/memory/log usage. An existing private
Tailscale reverse proxy creates a separate HTTPS service using the configured
unique name. The application has no VPN discovery or prerequisite flow; failed
connections appear as connection errors. Shared-host resources and reverse-proxy
availability are shared failure domains, so this is environment isolation rather
than a separate physical server.

Configure the immutable image, private env-file path and unique private service
name outside source control. The private application env file needs explicit
`RELEASE_MODE=self_hosted`, canonical HTTPS `PUBLIC_ORIGIN`, a fresh random
`SECRET_KEY`, `MFA_ENCRYPTION_KEY`, `BOOTSTRAP_SECRET`, local SQLite `DATABASE_URL`,
private `MEDIA_ROOT` and `REGISTER_ENABLED=false`. Do not copy production secrets,
Authelia configuration or existing data volumes. This mode does not exercise real
hosted subscriptions or hosted PostgreSQL/object-storage integrations.

Build the release Dockerfile from the exact reviewed source artifact. Initialize
its new database offline using `scripts/release_init_schema.py`, then apply
`scripts/release_upgrade_workers.py` with the explicit schema connection. Start
only the `openfotoframe-dev` project with this file. The health check connects to
loopback using the configured origin's Host header, matching application host
validation without probing the public hostname.

Bootstrap a fresh dev owner through the loopback API, with the configured canonical
Host header. Keep any generated setup password in an explicitly private file and
require changing it at first login; never put credentials or session responses in
chat, git, deployment logs or a URL. Leave registration disabled and use invites
for additional users. Use an empty gallery or deliberate test copies, never live
production volume mounts. Dev frame enrollment must not replace an existing TV's
credentials or startup automation.

Before/after deployment, record existing production and older-test container IDs,
image IDs, startup times and volume names. They must remain identical. Verify the
new dev HTTPS endpoint, first-party login gate, resource isolation, health and
worker operation. Stop or remove only the dev project if checks fail; never run a
parent production Compose restart or a shared-volume prune.

Hosted staging is a later separate configuration: provision a new PostgreSQL
cluster/database and restricted roles, a new private never-versioned/Object-Lock-
free primary bucket, and sandbox-only payment/mail configuration. Do not enable
`TESTING`, fake provider callbacks, reuse live bucket prefixes or substitute a
public bucket to make staging start.

Publishing source to a remote with a public mirror requires review and approval
of the exact content/destination. A private source-artifact deployment does not
require pushing or merging a production branch first. The existing main-branch
production deploy workflow remains unchanged.
