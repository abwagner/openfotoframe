# Browser account and household access

The opt-in release server provides `/account`, `/change-password`, `/mfa`, `/admin`, `/register` and `/subscribe`. All writes require the existing browser CSRF token and invoke the authoritative versioned API through the scoped adapter. Browser pages never render access or refresh tokens. MFA recovery codes and newly created invitation links are shown once with the release server's no-store/referrer protections.

`/admin` requires an explicitly selected household in which the account is an owner or administrator. The API enforces ownership changes, the last-owner rule, recent authentication and resource ownership. Invitations grant only selected collection/frame roles; email-bound and explicitly transferable invitations remain distinct. Invitation revocation uses the ID shown at creation. Replacing resource grants requires explicit confirmation that the complete previous set will be replaced.

Current limitations are visible: the API has no membership-grant read or invitation-inventory endpoint, so replacement forms do not prefill existing grants, and revocation requires a recorded invitation ID. The account MFA screen enrolls TOTP; passkey enrollment/authentication remains available through the first-party login challenge flow. Factor inventory/removal and adding another passkey from an existing account screen remain follow-up UI work.

Hosted registration creates an unverified account without granting a household or subscription. A verified owner can create a household and prepare Stripe checkout only for configured website plans. A server-selected idempotency key is reused for retries of the current household/plan; only provider HTTPS destinations accepted by the billing API are rendered. Checkout creation/return never marks a subscription paid. Unconfigured website billing shows an explicit unavailable message. Apple purchases/restores remain in the native iOS app; provider portals and full subscription lifecycle browser management require configured providers and further UI testing.

These changes do not configure domains, deliver mail, activate payment providers or contact physical displays. Release activation still requires operator configuration and external-provider/device verification.

## Storage and retained deleted photos

`/storage` first restricts the chosen household to owner/administrator memberships, then loads its administrative storage endpoint. Partial collection members never trigger a household-total read. The screen separates current originals, retained deleted originals, included plan allowance and remaining allowance. Hosted inactivity and full storage have explicit messages; self-hosted servers do not claim unlimited storage or report available disk capacity. Owners can explicitly review subscriptions; no purchase or upgrade is automatic.

`/trash` requires a selected household and collection and offers cursor-paged deleted photos, restore and explicitly confirmed permanent removal. Restore returns a photo ungrouped and remains subject to entitlement/quota checks. Any purge request makes restoration unavailable. A failed purge can be explicitly retried. Queued/failed deletion still consumes storage until the background worker succeeds; these screens do not start a worker or claim immediate storage reclamation.
