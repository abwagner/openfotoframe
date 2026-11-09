# Included storage: trash and permanent deletion

Removing a photo from a collection hides it from media requests, frames and normal
exports. Its private original and thumbnail remain retained. Original bytes still
count toward the household's included allowance. No automatic photo expiry or
subscription-cancellation deletion period is enabled.

The release service exposes administrator-only collection trash:

- `GET /api/v1/households/{household}/collections/{collection}/trash` returns at most
  100 removed photos and a next-page `after` cursor. It never returns storage keys.
- `POST .../assets/{asset}/restore` restores a removed photo without duplicating
  its original bytes. Previous group membership is not restored. Active-photo
  quotas and the current hosted entitlement are rechecked, so an inactive
  subscription or exhausted active-photo allowance may prevent restoration.
- `POST .../assets/{asset}/purge` requires the exact JSON boolean
  `confirm_permanent_delete: true`, current owner/admin authority, authentication
  within five minutes, and browser CSRF protection. It queues permanent deletion;
  the API response is not a claim that storage has already been released.

Collection editors and contributors cannot inspect trash or request permanent
removal, including for photos they originally uploaded. Household administrators
have full household authority; selected-member grants never imply that authority.

A permanent-deletion request is an explicit irreversible action. Once queued, the
photo cannot be restored. A failed request can be retried through the same explicit
confirmation and recent-authentication checks. The trash UI shows queued/failed
receipts rather than silently treating them as completed. The requesting
administrator's current authority is checked again by the worker; revoked or
disabled accounts do not authorize private object deletion.

The worker deletes only the original and thumbnail keys captured from the matched
removed asset record, and validates the generated native-upload or offline-import
key layout before deleting either object. It does not accept paths from clients, scan arbitrary bucket
prefixes or follow legacy global backup credentials. Membership, asset and job
locks use the same order as HTTP mutations. Lease tokens prevent stale workers
from executing another worker's current claim.

Both object deletes are idempotent. The asset row and its quota usage are removed
only after those deletes succeed in the metadata transaction. A crash after one
object deletion or before database commit leaves a durable request for retry;
retained bytes continue to count in the meantime. This conservative accounting
may temporarily exceed bytes physically present after a partial failure, but it
cannot grant quota twice. Completed receipts and audit events remain available;
original filenames, bytes and credentials do not enter dispatcher logs.

The hosted primary bucket must never have versioning enabled or suspended and
must be verified free of Object Lock before startup; ambiguous/missing provider
permissions fail closed. Separate replicas and backups have their own retention. Deleting a
current S3 object can leave older versions or backup copies, so customer allowance
released is not a promise of immediate erasure of every retained provider copy or
an immediate reduction in the provider bill. Configure and document version/backup
lifecycle policies separately before launch. Do not automatically expire customer
originals to reduce cost without an explicit product retention policy.

See [storage accounting](RELEASE_STORAGE.md) for allowance semantics and the worker
operations runbook for bounded retries, expiry cleanup and restart supervision.
