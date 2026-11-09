# Managed storage accounting and quota readiness

The hosted service includes the storage allowance explicitly configured for its
verified plan. Customers do not supply object-storage credentials. Self-hosted
installations use their operator's configured storage; no paid allowance, pricing,
retention duration or automatic deletion policy is inferred.

`GET /api/v1/households/{household}/storage` requires a current household owner or
administrator membership. Collection contributors, viewers and frame-only members
cannot retrieve household totals, even when they can upload into a shared
collection. PostgreSQL also enforces this administrative boundary in the narrowly
scoped `release_storage_usage` function. It requires both the current household
context and an active administrative membership; billing service roles cannot call
it. Resource-level access remains independent from the household's subscription.

The response declares `accounting: original_bytes`. Its `usage` contains:

- `active_bytes`: original photo bytes for photos currently in the library.
- `retained_deleted_bytes`: original photo bytes for removed photos still retained.
- `total_bytes`: active plus retained original bytes.
- `active_assets`: photos currently in the library, excluding removed photos.
- `frames`: all currently configured frames in this household.

These are customer allowance measurements, not a provider invoice. Thumbnails,
render caches, temporary collection exports, replicas, object-storage metadata,
network transfer and other server overhead are operator costs and are not counted
as customer original-photo bytes. The service must budget and monitor those costs
separately. Retained originals continue to consume allowance until an explicit
permanent deletion completes; hiding or removing a photo cannot bypass storage
quota. A queued or partially completed purge does not release allowance.

`limits` contains the configured plan's `storage_bytes`, `asset_limit` and
`frame_limit`; `remaining` contains nonnegative `storage_bytes`, `assets` and
`frames`. A known expired plan retains these reference limits in the usage view,
but its inactive entitlement still prevents new content. Self-hosted installations
and unknown/unconfigured plans report null limits and remaining values, rather
than inventing an allowance. An inactive entitlement does not remove existing
content or change collection/frame permissions.

Content-write errors distinguish:

- HTTP 402 `subscription_required`: no verified active subscription for new content.
- HTTP 409 `storage_quota_exceeded`: retained plus active originals would exceed storage.
- HTTP 409 `asset_quota_exceeded`: the active-photo allowance would be exceeded.
- HTTP 409 `frame_quota_exceeded`: the frame allowance would be exceeded.
- HTTP 503 `billing_unavailable`: required limits or billing configuration are unavailable.

Member-facing quota errors contain an actionable message but never household byte
counts, hidden collection names, or another member's photo metadata. Uploads,
restores and frame creation enforce their applicable limits in the same transaction
as the authorized write. PostgreSQL serializes quota-changing writes per household
so concurrent contributions cannot spend the same remaining allowance twice.

Usage counts derive from current metadata and original object lengths recorded by
the server. They do not scan arbitrary storage locations or trust byte counts sent
by the client. Failed storage operations roll back new metadata; an unavailable
provider may require operator reconciliation of private orphan objects. Explicit
trash/purge workflows and their worker receipts define when retained metadata can
be removed; no automatic retention policy is enabled by this preparation.

## Display delivery

The TV web display uses an in-memory playlist validator. When the authorized
playlist is unchanged, polling receives an empty HTTP 304 response rather than
downloading the whole playlist again. Media/settings/state changes produce a new
validator. Every poll still rechecks frame and collection authorization before
evaluating the tag; a revoked credential cannot reuse an old tag to receive a
successful response. Failures clear the displayed blob and validator.

This reduces repeated metadata transfer, not database authorization work, and does
not introduce a persistent client photo library or offline-cache requirement.
Originals and rendered photos continue through the managed service's private
media boundary. Real deployment traffic/rendering costs still require monitoring.


## Hosted primary S3 bucket requirements

Hosted startup now verifies all four bucket Block Public Access switches, a
**never-versioned** primary bucket, and verified absence of Object Lock. A
`Status` of `Enabled` or `Suspended` is rejected: suspending versioning does not
remove historical versions. AWS returns no versioning `Status` for a bucket that
has never enabled versioning. See the official
[GetBucketVersioning response examples](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetBucketVersioning.html).

The application accepts Object Lock as absent only when the provider returns the
documented `ObjectLockConfigurationNotFoundError`. An enabled lock, unexpected
success response, unsupported API, access denial, timeout or other provider error
blocks hosted startup with a sanitized configuration error. There is no unsafe
bypass setting. AWS's
[Object Lock SDK example](https://docs.aws.amazon.com/AmazonS3/latest/developerguide/s3_example_s3_GetObjectLockConfiguration_section.html)
distinguishes that specific absence error from other failures.

The application identity needs these read-only bucket-configuration permissions,
scoped to the exact primary bucket ARN:

- `s3:GetBucketPublicAccessBlock`
- `s3:GetBucketVersioning`
- `s3:GetBucketObjectLockConfiguration`

It also needs `s3:GetObject`, `s3:PutObject` and `s3:DeleteObject` on the configured
primary object prefix. Existing deployments that lack the configuration-read
permissions must have their private IAM policy reviewed before startup; an access
denial does not mean the feature is disabled. The application does not need
permission to enable versioning, Object Lock, public access or retention settings.
Verify action/resource scopes against the official
[S3 IAM action reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazons3.html).
These startup checks only read configuration; they do not change or provision a
bucket, policy or stored photo.

Keep these primary-bucket invariants unchanged while the service runs. A provider
that cannot prove them is unsuitable for this hosted primary-storage adapter.
Independent private backup copies may have a separately reviewed retention policy;
the primary-photo purge endpoint does not promise to erase operator backup copies.
No backup-retention duration is selected here. Customer-facing quota accounting
continues to count recorded primary original bytes, while backup and derived-copy
costs remain part of the operator's capacity budget.
