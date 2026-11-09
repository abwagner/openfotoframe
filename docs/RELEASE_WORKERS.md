# Durable release workers and schema version 002

The web process queues exports and explicit administrator photo purges. It never
runs those expensive jobs inline or starts a worker at import/startup. Queue
records, attempt counts, retry deadlines and leases live in the relational store;
a process restart does not erase them. The optional supervisor is a local command,
not an installed service. No production worker, timer, container or server change
was made while preparing this code.

## Explicit schema preparation

For a fresh isolated database, initialize the normal metadata/RLS/application roles
with `scripts/release_init_schema.py` first. Then run the versioned additive worker
upgrade using a private `OPENFOTOFRAME_SCHEMA_DATABASE_URL`:

```sh
python scripts/release_upgrade_workers.py --install-dispatch-role
```

For an existing rehearsal database, stop its rehearsal HTTP/worker processes and
take a consistent private snapshot first. The same upgrade creates the new
`media_purges` table, explicitly alters old `jobs` tables (metadata `create_all`
alone cannot do that), creates queue/expiry indexes, reapplies current RLS,
installs the bounded worker functions and records `worker_schema_version: 2` in
one transaction. SQLite and PostgreSQL both support repeat application. A failed
final version write rolls back DDL and policy changes; local tests exercise this
failure window on both engines. The application role is refused as migration
identity before DDL. No HTTP request or worker automatically runs this migration.

The default HTTP role is `openfotoframe_app`, dispatcher role
`openfotoframe_dispatch`. For reviewed custom role names pass `--app-role NAME`
and `--dispatch-role NAME`; only lowercase SQL identifiers are accepted. The
upgrade grants the HTTP role the new purge table and storage-usage helper while
preserving RLS. `--install-dispatch-role` needs role-administration authority; a
schema owner without it can omit that flag and have the cluster operator review
and apply `postgres-dispatch-role.sql` separately. Set role passwords privately.
Do not grant role membership between the dispatcher, HTTP, billing and schema
identities. The dispatcher validator rejects broad table/column permissions,
role memberships, superuser/BYPASSRLS, replication, CREATEROLE and CREATEDB.

Upgrade rollback is an operational snapshot restore, not an automatic downgrade.
Stop workers before restoring a database snapshot and reconcile private generated
export objects separately. Object deletion already completed by an explicitly
confirmed photo purge cannot be reversed by a database-only rollback. Rehearse
with synthetic/copied data; never point these commands at the live source.

## Bounded dispatch and recovery

```sh
python scripts/release_worker.py --once
python scripts/release_worker.py --supervise --poll-seconds 5
```

Both modes use the configured ordinary application `DATABASE_URL` for execution.
Hosted PostgreSQL additionally requires private `DISPATCH_DATABASE_URL` for the
separate execute-only queue role. That role has no SELECT or mutation rights on
content, credentials, jobs or purge rows. Its fixed-search-path definer functions
claim only one indexed candidate from each queue, reserve the oldest, and return
only kind/job/household/requester/action/lease identifiers. Normal workers then
revalidate the requester, membership, resource permissions and lease under the
application role before reading or deleting media. Missing/revoked authority
never becomes a successful job through a dispatcher bypass.

Claims use `FOR UPDATE SKIP LOCKED` on PostgreSQL and SQLite's serialized write
reservation. A lease lasts ten minutes. A crash leaves a durable reservation;
a subsequent process can reclaim it after expiry. Executor row locks prevent a
second claim while media IO is active, and stale lease tokens cannot execute or
settle a reclaimed job. Manual exact-ID export execution remains available but
refuses an active dispatch lease. Ordinary errors retry with exponential backoff
(60, 120 seconds), with at most three execution attempts. Repeated crashes become
terminal `worker_attempts_exhausted`; permanent permission/format/size failures
are not repeatedly retried. No client-visible error includes storage exceptions,
private filenames or credentials. SIGTERM/SIGINT stop polling after the current
bounded job, which remains recoverable if the process is killed.

Exports use the existing 24-hour expiry. Expiry cleanup deletes only the generated
`HOUSEHOLD/jobs/JOB/export.zip` artifact, then removes its queue row. It retries
storage failures with capped backoff, including after a crash between deleting the
object and removing metadata. An orphan from a crash after export-object creation
is safely replaced under the still-authorized queued-job lock on retry. Cleanup
never reads a client-supplied path, follows arbitrary result keys or deletes photo
objects. Photo originals/thumbnails are deleted only for explicit purge requests;
there is no automatic photo-retention deadline. Purge retries retain metadata and
quota until all captured object deletions and the final transaction succeed.

The current archive size cap, per-requester outstanding-job cap and expiry bound
individual job resources. They do **not** establish a household-wide temporary
artifact allowance across unlimited members. Household-wide queue/storage capacity
and global admission limits remain a hosted capacity gate before opening paid
registration at scale. Do not describe temporary exports as part of an invented
paid storage allowance.

## Optional container supervision

`compose.worker.yml` is a separate opt-in rehearsal template with a `workers`
profile, no published ports, read-only root, bounded process/memory settings and
`restart: unless-stopped`. It uses an explicitly selected existing rehearsal data
volume and a private worker environment file. It is never included in the normal
web compose startup. Populate its image/env/volume references and validate the
concrete configuration before choosing to start it. A container configured this
way resumes polling after a host/container restart; running `--once` alone does
not install persistent supervision. Hosted workers need the same private bucket
permissions and ordinary application role configuration as the HTTP process,
plus only the bounded dispatcher credential. No mail or payment credentials are
needed unless deliberately configuring those unrelated adapters in that process.

Tests cover SQLite and actual disposable PostgreSQL role isolation, concurrent
claims, stale token rejection, crash recovery, finite retries, revoked requesters,
explicit purge recovery, expiry cleanup and migration rollback. Provider-level
storage integration, supervised restart drills and capacity monitoring remain
staging checks before rollout.
