# Offline migration rehearsal

The release application uses a relational authorization model. These tools prepare
a new destination; they never activate release mode, change a live server, run TV
automation, or deploy an app. A dry run is the default. The legacy app remains the
authority until an explicitly scheduled cutover.

## Inputs and identity decisions

Use a consistent, write-paused backup containing `data/` and `uploads/`. Include
`users.json`, `gallery.json`, `settings.json`, originals, thumbnails and snapshots.
Enrolled authenticators also require the legacy `.mfa_key`. Keep the backup and
encryption keys in private storage with a verified restore path. Never commit the
snapshot, login map, key files or generated database.

The operator must select an existing legacy administrator as initial owner and
provide a private JSON map from **every** legacy username to a unique first-party
login email. Matching destination emails do not merge accounts. A collision stops
the import for explicit reconciliation. Email ownership is not assumed verified
merely because the operator supplied a mapping. Existing usernames remain login
aliases, password hashes and legacy SHA-256 salts are preserved, and authenticator
secrets are re-encrypted with the destination MFA key. Recovery-code hashes and
passkey records are preserved; actual passkey login still requires compatible
origin/association support in the release authentication implementation.

A known legacy administrator default password blocks the entire import before
any destination metadata or media writes. A required web password change is not
an adequate gate: someone knowing that password could claim the owner account.
Keep the original snapshot/hash privately for audit; the operator must replace
the credential offline in a separate prepared snapshot, retain its MFA material,
then generate a new plan/fingerprint and repeat import/verification. Rejected
imports create no account, login challenge, activation manifest or ready result.

The chosen administrator becomes household owner; other former administrators
become household administrators. Ordinary users become members with explicit
editor access to **only** the imported legacy shared collection and controller
access to **only** the imported existing frames. Future collections and frames
receive no automatic grants. Legacy `uploaded_by` remains attribution; an unknown
username does not create an account or permission.

No browser sessions, device access/refresh tokens, shared display enrollment
secrets, or CEC credentials are copied. Re-enroll each frame and power-control
agent with distinct credentials during the later cutover. No token is inferred
from LAN access, localhost, VPN reachability, or an external proxy header.

## Rehearse against a new destination

Install `requirements-release.txt` in an isolated environment. The shared database
layer uses SQLAlchemy for SQLite/PostgreSQL parity and psycopg for PostgreSQL;
these dependencies are confined to the opt-in release application.

Use environment variables supplied from a private secret store:

- `OPENFOTOFRAME_SCHEMA_DATABASE_URL`: separate offline schema-owner connection.
- `OPENFOTOFRAME_DATABASE_URL`: offline import connection, never the live HTTP role.
- `OPENFOTOFRAME_MFA_ENCRYPTION_KEY`: destination Fernet key, distinct from sessions.

Initialize PostgreSQL explicitly with `scripts/release_init_schema.py`; use its
`--install-postgres-roles` option only in a reviewed new staging database. Configure
role passwords separately in the secret manager. The HTTP process must not run as
the schema owner or a role with `BYPASSRLS`. SQLite can initialize a new destination
schema as part of the offline import.

First run the planner against a synthetic fixture or private copied backup:

```sh
python scripts/release_migrate.py --source /private/backup-copy \
  --migration-id rehearsal-001 --owner legacy-owner \
  --email-map /private/login-map.json
```

The output contains only counts, generated tenant IDs, fingerprint, byte totals,
and whether frame reenrollment is needed. The in-memory plan contains credential
material and must not be serialized/logged. Do not use these example paths or
identifiers as actual production configuration.

After reviewing the redacted plan, a rehearsal import requires both `--apply` and
`--acknowledge-write-pause`, plus a new `--media-root` outside the backup. Then run
the same command with `--verify` instead of `--apply`. Verification checks tenant
resource counts, exact asset metadata and every original's SHA-256. Treat source
fingerprints/counts and destination verification results as private runbook data.

## Atomicity and retries

Users, household, memberships, grants, collections, assets, layouts, frame
settings/schedules and the completed manifest commit in one metadata transaction.
Every original, thumbnail and preserved derived render is copied under generated
private keys with streaming SHA-256 verification. Settings, crop, mat overrides,
image order, display profile content and schedules remain intact in JSON columns;
legacy presentation layouts remain inside their owning collection.
Only recognized visual/display settings enter collection/frame configuration.
Operator security, origin and provider settings are listed by key for private
review rather than copying credentials into a household API response. The manifest
records the old MFA requirement mode and allowed methods, and the importer preserves
both in household authentication policy so required factors are not weakened.
Other server-wide authentication/provider secrets require private operator review.

Repeating the same migration ID and snapshot is idempotent. Reusing an ID for
different bytes or identity decisions fails. Source media changed during copying,
invalid MFA keys, missing media, path traversal, symbolic links, malformed groups,
and identity collisions fail without activating partial database state. A crash
may leave unreferenced immutable files; matching bytes can be adopted on retry.
They must not be served publicly and can be removed later through an audited
orphan sweep after confirming there is no database reference.

## Cutover and rollback gates

Before any live change, separately verify collection/frame isolation, migration
counts/hashes, native login/MFA, saved-server upgrade behavior, advanced web editor
parity, TV playback/fullscreen, local wake automation and display/CEC credential
reenrollment. All legacy compatibility routes must use scoped relational adapters;
do not enable a second writable JSON store alongside the new database.

Rehearse restoring the consistent old snapshot and its keys into an isolated
legacy instance. Define the write cutoff: once new relational writes are accepted,
old code must not resume writing stale JSON. Roll back with compatible new code or
restore the complete pre-cutover snapshot under a write pause, accounting for
newly accepted changes before switching traffic. These tools do not perform that
traffic switch or approve data loss.

For self-hosted-to-cloud moves, use explicit destination ownership verification
and a scoped content export/import with remapped IDs. Do not copy accounts,
sessions, invitations, billing records or frame credentials into another household.
This legacy importer intentionally refuses identity collisions rather than acting
as an automatic cross-server account merger.
