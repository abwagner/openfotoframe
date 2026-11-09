# Hosted and self-hosted operations preparation

The opt-in release application is separate from the existing legacy entry point.
Nothing in this directory is connected to deployment CI, the current server or
TV power automation. The compose files use distinct rehearsal volumes and loopback
ports; do not point them at a live data mount or a remote Docker context.

## Runtime configuration and storage

Both modes use the same versioned API, identity and authorization policies.
Self-hosting uses SQLite and private local media. Hosted mode requires PostgreSQL,
HTTPS and an application identity that does not own the schema or bypass row
security. SQLAlchemy provides the common transaction/schema layer; psycopg is the
PostgreSQL driver, both declared in `requirements-release.txt`. The optional
`requirements-cloud.txt` adds boto3 for the private S3 adapter; the shared release
container installs the cloud and optional billing dependency sets. The legacy runtime does not acquire
these dependencies implicitly. The image copies only explicit release source
directories, and its own build-context allowlist excludes local signing and data
files while retaining the shared static JSON palette.

The hosted compose file requires the private S3 storage adapter and an explicitly
configured staging bucket. Its media volume is private scratch space, not a public
media origin. Do not add
a public uploads/thumbnail/snapshot mount, Caddy `file_server`, object bucket ACL,
or cache that can bypass the authorized media endpoints. The S3 adapter uses opaque
keys, server-side encryption, conditional writes and authorized proxy reads. The
bucket must independently enforce Block Public Access and private IAM policy.
Production bucket/IAM behavior still requires integration verification before
rollout. No reusable storage URLs are issued by this implementation.

Keep session/MFA keys, role credentials, provider credentials, actual hostnames,
customer snapshots and live invitation/pairing URLs in a secret manager/private
runbook. The templates intentionally include no real domains, prices or keys.
HTTP access logs are disabled in the image because invitation/credential-bearing
paths must not be copied into general infrastructure logs; use redacted structured
security events and private bounded diagnostic logs instead.

Run the container as its unprivileged user, with a read-only root and a private
writable data volume. Configure a trusted TLS reverse proxy separately; do not
disable TLS validation or enable trust in arbitrary forwarded identity headers.
Configure explicit public/invite origins and associated-domain files before
on-device Universal Link validation. Self-hosted manual entry/QR remains supported
without a central link server or VPN detector.

## PostgreSQL schema and isolation

Initialize metadata and install `postgres-rls.sql` as a dedicated schema migration
owner through `scripts/release_init_schema.py`. `postgres-roles.sql` creates fixed
example app/billing roles only when explicitly requested; provision their passwords
separately. Never give the HTTP process schema-owner credentials, superuser,
`BYPASSRLS`, or membership in the billing/schema roles.

The store sets `app.user_id`, `app.household_id` and `app.frame_id` transaction
locally. Missing context denies tenant content; pooled connections start with
empty context. Shared policy code still enforces collection/frame actions. RLS
provides an additional tenant and collection boundary, not a replacement for API
policy checks.

Narrow security-definer functions handle operations that precede ordinary tenant
membership: frame credential lookup, new household creation and invitation
redemption. They derive/bind the current user, require the opaque invitation hash,
validate expiry/issuer/recipient/current resources, and perform a single atomic
membership/grant update. The fixed `search_path` and schema ownership are part of
the deployment contract. Function execution is revoked from PUBLIC and granted
only to the intended HTTP role.

Before a hosted rollout, run the API isolation suite using the real non-owner HTTP
role on PostgreSQL, including missing context, pooled reuse, a person in two
households, disjoint collection/frame grants and revocation. SQLite tests alone
do not establish that PostgreSQL RLS is configured correctly. Confirm all expected
tables have row security enabled after every schema change.

## Billing boundary

Billing is disabled unless the operator explicitly configures plans, trusted
provider adapters and checkout hosts. No price, storage limit, frame limit,
purchase channel or cancellation/retention policy is invented by default. Invited
members do not need their own checkout; entitlements belong to the household.

The provider-neutral boundary exposes public configured plans, owner-only checkout
and entitlement endpoints, plus verified provider event handling. A provider must
verify the signed event/receipt, audience, environment, product mapping and current
subscription state. A client `paid` flag or purchased product identifier is never
trusted as entitlement or access. A household/subscription binding begins with a
server-created checkout reference; event IDs are deduplicated, out-of-order state
cannot reactivate an expired subscription, and an active subscription cannot be replaced by another purchase channel. Each
original subscription retains its latest revision, so delayed events cannot revive
an older state; a genuinely newer verified renewal can reactivate after expiry. No event creates membership or resource grants.

Quota checks must occur **after** authorization in the same transaction, with the
household reservation held until the content write commits. Expiry blocks new
content according to the configured policy but does not delete photos or invent a
retention deadline. Existing read/export behavior and cancellation retention need
an explicit product decision before paid launch.

`BILLING_EVENT_HANDLER` is a trusted server callback receiving only a verified
normalized event and bounded raw body. In hosted mode it uses the separate bounded
billing role, which can inspect household existence and change billing tables but
cannot read content, user credentials or grants. Do not nest SQLite write
transactions inside an HTTP request transaction. The official Stripe and Apple adapters are implemented but disabled until complete
operator configuration is supplied. No purchase products, actual prices, private
keys or production endpoints are bundled.

## Release gates

Queued exports and explicit photo purges use durable bounded dispatch described in
[RELEASE_WORKERS.md](RELEASE_WORKERS.md). The opt-in worker supports one-pass and
supervisor modes with leases, finite execution retries and export expiry cleanup.
It uses a separate execute-only PostgreSQL dispatcher identity and ordinary RLS
application execution identity. Schema version 002 is an explicit offline step;
HTTP startup performs no automatic upgrade or background-service installation.

1. Rehearse the offline import/verification and complete snapshot restore described
   in `RELEASE_MIGRATION.md`; preserve the old service until an explicit cutover.
2. Verify every advanced web editor and native action against the same relational
   authorization boundary. Deny unsupported legacy endpoints rather than falling
   back to a shared unscoped JSON gallery.
3. Exercise private originals/thumbnails/renders, exports/restores, duplicate checks,
   jobs and caches with two households and revoked grants. Workers must persist
   actor/resource/household scope and revalidate it before delivering results.
4. Configure and test purchase-channel-specific checkout and signed-event adapters,
   plan limits, retention, account deletion, email verification/recovery delivery,
   private object storage permissions and durable processing worker operation.
5. Verify physical-device profile switching, Keychain isolation, QR approval,
   invitation links, bootstrap ownership, MFA and local HTTP compatibility.
6. Review the concrete source patch/public destination separately, then approve a
   future deployment and traffic switch. These preparations do not authorize either.

## Stripe and App Store activation

`requirements-billing.txt` pins the official `stripe==16.1.0` and
`app-store-server-library==3.1.2` packages. Apple 3.1.2 includes the upstream OCSP
freshness fix; keep security updates reviewed and pinned. No custom signature
cryptography or unsigned transaction fallback is implemented. The legacy runtime
does not install these packages unless explicitly selected.

The environment entry point parses `BILLING_PLANS` as a JSON array. Each plan needs
`id`, `name`, explicit positive `storage_bytes`, `asset_limit`, `frame_limit`, and
one or both configured `stripe_price_id` and `apple_product_id`. Optional
`description` and `price_label` are display text; native Apple pricing comes from
StoreKit. No default product, price, or numerical quota exists. Duplicate provider
product mappings are invalid. `BILLING_CHECKOUT_HOSTS` is a JSON array of exact
allowed hostnames. Malformed JSON or partial provider configuration fails startup.

Stripe requires all of `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
`STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL`; return destinations must use HTTPS.
`STRIPE_LIVE_MODE` explicitly selects live versus test verification. Set the
provider webhook to `/api/v1/billing/webhooks/stripe`. Subscribe to subscription
created/updated/deleted, checkout completed/async-payment-succeeded and invoice
paid/payment-failed/payment-action-required events. Verification uses the exact
raw body and official SDK signature/timestamp checks, then retrieves the current
subscription with the server API. Only a configured single-item subscription with
a paid latest invoice grants access; browser return parameters grant nothing.
Same-second conflicting events conservatively prefer negative states. Test a
subsequent renewal/recovery event when testing payment recovery.

Apple requires `APPLE_BUNDLE_ID`, numeric `APPLE_APP_ID` for production,
`APPLE_ENVIRONMENT` (`sandbox` or `production`), `APPLE_ROOT_CERTIFICATES` (a JSON
array of paths to reviewed official Apple DER trust roots), `APPLE_SIGNING_KEY_PATH`
(a private App Store Server API key file), `APPLE_KEY_ID`, `APPLE_ISSUER_ID`.
Mount key files privately and read-only; never put them in the image or repository.
Configure App Store Server Notifications V2 at `/api/v1/billing/webhooks/apple`.
The official verifier uses mandatory online certificate/revocation checks. Xcode
and LocalTesting verification environments, which bypass signatures, are rejected.
Both the notification and its nested transaction are verified. The server also
fetches current subscription status and verifies that transaction independently,
checking bundle, environment, product, purchased ownership and account token.
Grace/retry states do not silently create a paid grace policy.

Native StoreKit prepares an owner-only household purchase intent, then uses the
returned stable UUID as `appAccountToken`. Signed transactions/restore requests go
to the owner-only transaction endpoint; the server's original-transaction binding
cannot move to another household. A plan/product identifier or client paid flag is
insufficient. Family sharing is not silently mapped to household authorization.
Restoring an existing purchase reads the owner-only
`GET /api/v1/households/<hid>/billing/apple/account` endpoint for its existing
`app_account_token` and configured `product_ids`; that read creates neither token
nor purchase intent and remains available while a subscription is active. An
unlinked household returns 404. Submit the signed restore transaction without a
checkout reference to reconcile its permanent original binding.
Invited members use explicit collection/frame grants instead. Native external
Stripe checkout is disabled by default pending the distribution region/entitlement
policy decision; Stripe checkout remains available through the hosted website.

Hosted deployments also need `BILLING_DATABASE_URL`, using the separately bounded
billing role from `postgres-roles.sql`, never a schema-owner or HTTP identity.
Real provider configuration without this separate connection fails startup.
Keep checkout/subscription IDs and full provider payloads out of general logs;
HTTP failures expose generic errors rather than provider exceptions or keys.

Before activation, run Stripe test-mode signed-webhook/checkout lifecycle tests
and real StoreKit sandbox purchase, restore, renewal, expiry, refund/revocation,
out-of-order notification, duplicate delivery and two-household reassignment tests.
The local suite verifies real Stripe HMAC verification, rejection of unsigned Apple
JWS and deterministic SDK-shaped lifecycle responses. It does not substitute for
live sandbox provisioning, a valid Apple certificate-chain/OCSP integration test,
or App Store review. Explicitly test interrupted checkout retries with the same
idempotency key. Active subscriptions block new initial purchases; simultaneous
cross-channel intents still require operational reconciliation/refund policy before
paid launch. No cancellation-retention deadline or automatic deletion is assumed.

## Verification email and recovery delivery

The optional standard-library SMTP adapter registers verification/recovery delivery
callbacks only with complete operator configuration: `SMTP_HOST`, integer
`SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, bare-address `SMTP_FROM` and
`SMTP_TLS_MODE` set to `starttls` or `ssl`. Partial configuration fails startup;
there is no plaintext transport or fallback after failed STARTTLS. TLS certificates
and hostnames use the platform trust store. Secrets belong in private environment
configuration, not templates or logs. Neither importing/registering the adapter
nor running its mock tests sends email. Configure a real provider/account and test
both message delivery and signed-in verification completion before hosted launch.

Links use `${PUBLIC_ORIGIN}/verify#token=...` and `/recover#token=...`; fragments
keep bearer tokens out of HTTP request URLs/access logs. The first-party browser
forms consume them locally and call completion endpoints; do not add analytics or
third-party scripts to these pages. A reset link does not remove enrolled MFA,
verify mailbox ownership, create a user or grant household access. Existing native
share sheets deliver invitations; no automatic SMS sender is configured.

A self-hosted operator without email can issue a single-use recovery challenge for
an **existing enabled household member**, offline, using:

```sh
python scripts/release_recovery.py --user-id USER_ID --household-id HOUSEHOLD_ID --output PRIVATE_DIRECTORY/recovery.txt
```

Run with explicit `RELEASE_MODE=self_hosted`, the local SQLite `DATABASE_URL` and
configured `PUBLIC_ORIGIN`; never use production data during a rehearsal. The
output directory must already be private (0700), canonical, outside this checkout
and outside any served media/document directory. The command exclusively creates
a new 0600 file, never prints the link or token, and refuses overwriting an existing
file. Only the token hash enters the database, with a 30-minute expiry and an audit
event. A fresh issue consumes earlier unused reset links. The normal recovery
completion endpoint consumes the link once, revokes old sessions and continues to
require enrolled MFA. The command does not reset authenticators, disable MFA,
reactivate a disabled user, create an account or change the password itself. Pass
the file privately to the verified account holder and remove it after use/expiry.
A lost authenticator requires a separate reviewed recovery procedure; this CLI
provides no MFA bypass. No real recovery link has been issued during development.
