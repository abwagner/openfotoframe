# Hosted and self-hosted release readiness

Prepared locally on 2026-11-09 on `feature/hosted-self-hosted`. No production
service, existing library, physical display, installed app or TestFlight build was
changed. No payment, real recovery message or live subscription was created.
This is an implementation/rehearsal branch, not a general-release announcement.

## Implemented

One SwiftUI app offers a configured hosted service or an explicitly entered
self-hosted server. Multiple profiles isolate issuer/account Keychain credentials,
media requests and advanced browser storage. Invitations and quick-login QR codes
show the destination before connecting. There is no discovery or VPN dependency.
First-party login includes server-enforced MFA, passkey/browser PKCE support,
password/recovery flows, device revocation and a short-lived native-to-web bridge.

The opt-in `release_app:create_app()` entry point uses relational accounts,
households, memberships, collection grants and frame grants. It never imports the
legacy application or silently activates against its JSON files. PostgreSQL RLS
uses a restricted application role; self-hosted SQLite uses the same application
permission checks. Frame access does not grant collection/media access. Private
filesystem or S3 media, thumbnails, rendered snapshots, restore/export jobs and
browser adapters recheck authorization. Physical frame browser credentials cannot
become management sessions. Per-frame mat overrides and crop/rotation-aware
portrait pairing are rendered through the existing Pillow renderer.

The advanced editor, account/password/MFA pages, household invitations and resource
grants, frame-only browser controls, scoped backups/restores, registration,
verification/recovery links and website subscription checkout use the same v1
policy layer. Public information pages contain no third-party scripts. Invitation
and email secrets use fragments and explicit POST completion; previews do not
accept them. The web invitation fallback navigates to the selected server before
performing a same-origin form submission.

Paid-service support includes configurable plans, Stripe checkout and verified
webhooks, Apple StoreKit purchase/restore and server-side transaction verification,
a bounded billing database role, idempotent event handling and household-bound
entitlements. Resource grants remain independent of payment. Incomplete provider
configuration fails closed. External Stripe checkout inside the iOS app remains
disabled by default pending storefront-policy decisions; website Stripe checkout
and Apple in-app subscriptions are separate supported paths.

Offline migration tooling plans, imports and verifies a consistent legacy snapshot
into a new destination. It preserves required credentials/MFA with explicit owner
and email mapping, limits old shared access to imported resources, rejects known
default administrator credentials before writing, and never imports sessions or
shared display secrets. Frame enrollment must be repeated. TLS-only SMTP delivery
and an offline existing-user self-hosted recovery command are available; neither
bypasses enrolled MFA. See the migration and operations runbooks for exact limits.

## Local verification

- Full Python suite: **356 passed, 12 skipped** (the PostgreSQL cases are opt-in).
- Disposable real PostgreSQL suite with restricted roles: **12 passed**, no skips.
- Final export/restore/storage checks after the size-limit correction: **5 passed**.
- Final browser/rendering checks after the reorder correction: **15 passed**.
- Node browser/fullscreen/artwork tests: **24 passed**.
- iOS Release simulator build passed; native tests: **26 passed, 1 optional legacy
  live-server test skipped**. Both UI tests passed, including the authenticated
  advanced crop editor. No physical device installation or archive/upload occurred.
- Release Python lint/compilation and whitespace checks passed. Existing legacy
  Pillow deprecation warnings remain; they are not new release failures.
Tests include tenant isolation and revocation, real restricted-role PostgreSQL RLS,
one-use invite/pairing/PKCE/bridge credentials, browser CSRF, private media,
transaction rollback, migration idempotency and dangerous paths, billing event
replay, TLS-only mocked delivery, native profile isolation and advanced editing.

A source-only release Docker image is built locally. Its allowlist excludes live
data, private configuration, signing files and source-control metadata. An isolated
network-disabled container creates only temporary SQLite/media data and checks
runtime imports, schema creation and public onboarding pages. This is a packaging
smoke test, not a production deployment or S3/payment integration test.

## Remaining launch gates

1. **Hosted configuration and real provider verification.** Choose public hosted
   and invitation domains, publish associated-domain files only after approval,
   configure real plan prices/limits, Stripe sandbox products/webhooks, Apple
   subscription products/API credentials/trust roots, and SMTP. Exercise purchase,
   renew, restore, refund, expiry, interruption and duplicate events end to end.
   Mocked SDK tests do not establish a real certificate chain, OCSP response,
   mailbox delivery or App Store sandbox behavior. Define cross-channel purchase
   reconciliation/refunds, cancellation, retention and support policy.
2. **Durable background processing.** Collection exports are persisted and have an
   exact-scope worker command, but an automatic dispatch/retry supervisor is not
   installed. Queued exports stay queued until a worker runs. Configure and test
   restart recovery, retry limits, reconciliation of private orphan objects after
   storage-provider failures, result expiry and monitoring before offering hosted
   backup service. Legacy global Dropbox backup is not migrated as a shared tenant
   credential; collection export/restore is the new scoped path.
3. **TV power execution.** Schedule configuration is scoped, validated and timezone
   aware, but the new server does not execute power commands. The UI/API state
   this explicitly. Keep existing automation until a per-frame agent, durable
   command/acknowledgement protocol and DST/restart behavior are implemented and
   tested on the actual TV. See [frame automation](RELEASE_FRAME_AUTOMATION.md).
4. **Physical-device and HTTPS testing.** Verify universal links/QR sharing from
   email and text, custom-server certificate behavior, passkey/browser callbacks,
   Apple sandbox purchases/restore, profile switching and the advanced editor on
   an actual iPhone. Simulator integration is useful but does not replace this.
5. **Deployment and migration rehearsal.** Provision isolated PostgreSQL roles,
   private object storage, TLS, private secrets, backups, restoration and monitoring.
   Rehearse a fresh consistent copy of the real legacy library, explicit owner/email
   mapping and complete rollback without touching the original. Confirm counts,
   hashes, mats/crops/groups, selected grants, MFA and frame re-enrollment. Then
   prepare a specific reviewed cutover and a newly approved TestFlight build.
6. **Account and subscription management polish.** Browser grant replacement is
   an explicitly confirmed complete replacement; existing grants are not prefilled.
   Invitation revocation currently needs its recorded ID. Factor inventory/removal,
   adding another passkey from an account page, and provider management portals
   need further UI work and testing. See [browser account limits](../release_web/ACCOUNT_UI.md).
   The implemented login/enrollment and owner checkout paths do not imply that
   every lifecycle management screen is finished.
7. **Publication and rollout approval.** Review the exact patch and intended
   destinations first. Forgejo may mirror publicly, so even a Forgejo push is not
   a private staging action. Nothing here authorizes publishing, deploying or
   moving production data.

The architecture and generic protocols belong in the public source repository.
Keep actual infrastructure addresses, accounts, keys, session/invitation samples,
customer backups and provider configuration in a separate private runbook/secret
store. Documentation visibility does not replace enforced access controls.
