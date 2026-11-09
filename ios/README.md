# OpenFotoFrame for iPhone and iPad

A SwiftUI companion for the existing OpenFotoFrame server. Everyday photo management
is native; advanced workflows use the server's existing pages inside the app. No
backend migration, new API, or third-party iOS package is required.

## Open and run

1. Open `OpenFotoFrame.xcodeproj` in Xcode (16 or newer).
2. Choose the **OpenFotoFrame** scheme and an iPhone/iPad simulator. For a physical
   device, configure your signing team as described below. The deployment target
   is iOS 17; the published project has no default signing team.
3. Run. The app connects directly to
   `https://frame.tail5e8630.ts.net` and opens the native gallery without a sign-in
   screen when the server permits access. Your iPhone must be
   connected to Tailscale and permitted to reach this server. You can edit the
   address in Settings → Disconnect This Frame to connect to another frame.
   An explicit disconnect keeps the connection screen open on subsequent launches.
   Other servers that require login continue to use the existing web login flow.

Use the same URL you use in a browser, such as `https://frame.example.com` or
`http://frame.local:5000`. The server must be reachable from the device. Simulator
tests can connect to the Mac at `http://127.0.0.1:5059`; a physical iPhone needs the
server's LAN hostname/IP or HTTPS/Tailscale address.

The Xcode project is checked in. To regenerate it after changing `project.yml`:

```sh
cd ios
xcodegen generate
```

### Local signing configuration

Copy `Signing.local.xcconfig.example` to `Signing.local.xcconfig` and replace
`YOURTEAMID` with your Apple Developer Team ID. This local file is ignored by Git.
Both Debug and Release include it through `Signing.xcconfig`, including after
regenerating the project with XcodeGen. Do not hardcode a personal team in the
project or generator spec. CI can instead pass
`OPENFOTOFRAME_DEVELOPMENT_TEAM=YOURTEAMID` to `xcodebuild`.

Simulator builds can continue using `CODE_SIGNING_ALLOWED=NO` without a team.

## Feature coverage

| Web functionality | In the iOS app |
| --- | --- |
| Login, first-login password change, MFA challenge, recovery codes | Shared authenticated web sign-in before native screens become available |
| Photo library and file uploads | Native Upload tab, up to 10 photos per batch; HEIC conversion to JPEG |
| Duplicate comparisons and low-resolution warnings | Native preflight review, with thumbnails of similar photos, before upload |
| Gallery, visibility filters, metadata | Native searchable grid and photo details |
| Show/hide/delete, bulk operations | Native photo controls and Select mode with deletion confirmation |
| Slideshow order | Photos → menu → Reorder Slideshow |
| Create and dissolve groups | Native Select → Group and Photo Groups → Ungroup |
| Group membership, scales, match heights, group mat/effects | Photo Groups → Edit Group in Web Editor; opens the selected group |
| Individual zoom, mat color/finish, border effect/width, title | Native photo detail form with explicit Save |
| Interactive crop, clear crop, exact rendered preview, color presets | Photo → Crop & Full Preview; opens the selected photo in the web editor |
| Global mat appearance, timing, fades, shuffle, fit, screen ratio | Settings → Mat & Slideshow |
| Next/previous, pause/play, current slide count | Native Frame tab, synchronized with the server every five seconds while active |
| Full live slideshow, groups, crops, mat textures, transitions | Frame → Watch Slideshow; full screen with display sleep disabled while open |
| HDMI-CEC on/off tests and schedules, local/Tailscale network info | Settings → TV Power Schedules & Network; opens the web Settings tab |
| Dropbox configuration, disconnect, manual/scheduled backup, restore, history | Settings → Backup, Restore & History |
| Add/delete users and reset passwords | Settings → Manage Users |
| Change own password, TOTP enrollment, passkey enrollment, recovery codes | Settings → account/security screens; passkeys require the domain setup below |
| Sign out or switch servers | Settings → Sign Out / Disconnect This Frame |

Administration links are shown for administrators. All operations still use the
server's own authorization. Host installation, Docker upgrades, kiosk autostart,
and Tailscale installation remain host setup tasks, as they are for the web app.

## Sessions and connections

- WebKit owns the persistent, HttpOnly session. Native requests use cookies from
  that same app-local cookie store, with domain, path, expiration, and Secure checks.
- Every native mutation first fetches a current CSRF token from the authenticated
  upload page. Password/MFA gates must finish before this page is available.
- Native requests and embedded top-level navigation stay on the configured origin.
  Redirects never forward credentials or CSRF tokens to a different origin. External
  links open in the system browser when explicitly tapped.
- Sign-out clears local WebKit data and native state, invalidates pending requests,
  and attempts the server's logout endpoint. The app stores only the server URL in
  UserDefaults; it does not store passwords or enrollment secrets.
- The ATS exception permits connections to self-hosted HTTP servers. Application
  validation limits HTTP to loopback, `.local`, private IPv4, and Tailscale IPv4
  addresses. HTTPS is required for other hosts. Use HTTPS on untrusted networks.
- TLS verification is never disabled. A private/self-signed CA must be installed
  and trusted on the iPhone, or the server must use a publicly trusted certificate.
  Accepting a warning in Safari does not establish app trust.
- Photo imports preserve JPEG, PNG, GIF, and WebP bytes. Other decodable formats are
  converted to JPEG. Requests contain one photo, limited to under 49 MB to leave
  room under the server's 50 MB request cap. Staging is capped at 100 MB; successfully
  uploaded files are removed from the retry queue. Native previews are downsampled
  to 2048 pixels; uploaded originals are unaffected.

## Enable passkeys for a signed build

Apple requires the relying-party HTTPS domain to be associated with the app before
WebAuthn can use passkeys in an embedded `WKWebView`.
See [Apple: Supporting passkeys](https://developer.apple.com/documentation/authenticationservices/supporting-passkeys).
The base project builds and supports password/TOTP/recovery login without this
capability. **A passkey-only account cannot complete login in the base build.**

For the actual deployment:

1. Configure the server's stable HTTPS hostname and WebAuthn RP ID/origin using its
   existing security settings. The origin must match the address entered in the app.
2. Enable Associated Domains for `tech.swagner.openfotoframe` in your Apple developer
   account and select its signing team in Xcode.
3. Generate the domain-specific project:

   ```sh
   cd ios
   FRAME_PASSKEY_DOMAIN=frame.example.com xcodegen generate --spec project-passkeys.yml
   ```

4. Serve `https://frame.example.com/.well-known/apple-app-site-association` with
   `Content-Type: application/json`, no redirect, and your actual team/app ID:

   ```json
   { "webcredentials": { "apps": ["TEAMID.tech.swagner.openfotoframe"] } }
   ```

   Host this through the existing HTTPS reverse proxy; the Flask app does not
   currently serve that file. The example is in `associated-domain/`.
5. Rebuild/reinstall the signed app and verify enrollment and login on a physical
   device with iCloud Keychain enabled. This depends on your domain, signing team,
   server configuration, and Apple's association validation.

`project-passkeys.yml` generates `OpenFotoFrame/Passkeys.entitlements`. It is a local
deployment file and is ignored by Git. Regenerating `project.yml` returns to the
generic build. Passkeys and self-signed/raw-IP deployments retain the same server
restrictions as the web app. Physical-device passkey verification is required
before distributing a passkey-enabled release.

## Verification

Unit tests cover server URL/origin validation, cookie scoping, gallery/settings
decoding, multipart uploads, format preservation, and duplicate warnings. The
normal scheme runs these plus server-independent onboarding UI tests; live tests
skip unless their fixture environment is supplied.

```sh
cd ios
xcodebuild -project OpenFotoFrame.xcodeproj -scheme OpenFotoFrame \
  -destination 'platform=iOS Simulator,name=iPhone 18 Pro' \
  CODE_SIGNING_ALLOWED=NO test
```

For end-to-end tests, start an **isolated real Flask server** from the repository
root in a separate terminal:

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python ios/scripts/integration_server.py
```

Then run the integration scheme (substitute an installed simulator):

```sh
cd ios
xcodebuild -project OpenFotoFrame.xcodeproj -scheme OpenFotoFrameIntegration \
  -destination 'platform=iOS Simulator,name=iPhone 18 Pro' \
  -parallel-testing-enabled NO CODE_SIGNING_ALLOWED=NO test
```

The fixture server copies the backend into a temporary directory and seeds test
accounts and geometric photos. It keeps CSRF enabled and never uses your real
uploads or users. Tests exercise native login/cookie sharing, duplicate preflight,
upload, metadata/visibility changes, grouping, reorder, settings, display control,
deletion, logout, UI navigation, the crop-editor handoff, first-login password
changes, and an MFA recovery challenge. Restart the fixture server before each
full run: the password and recovery-code tests consume their initial state.

Use a dedicated simulator, since UI tests disconnect/reconnect this app. No
physical TV, Dropbox account, or Apple passkey ceremony is exercised by fixtures;
test those against the deployment before distribution. The existing backend suite
remains `pytest` from the repository root.

The **OpenFotoFrameTailscale** scheme includes a read-only UI check of the actual
Tailscale server. Run only `testTailscaleConnectionWithoutSignIn` to verify that
launch reaches the native gallery without showing a web sign-in screen. This
requires Tailscale connectivity and does not upload, edit, or delete server data.

## Hosted and self-hosted onboarding (local development)

The release client starts with **OpenFotoFrame Cloud**, **Your own server**, and
QR/link import. Generic builds intentionally have no hosted-service endpoint.
Configure `FRAME_HOSTED_SERVER_URL` and `FRAME_INVITE_URL` for the intended
service; do not use a personal server as a public default. `project-links.yml`
adds an `applinks` entitlement only after a project-controlled invitation domain
is selected. Its AASA document must identify the signing team and
`tech.swagner.openfotoframe`, with `/connect` supported. Regenerating `project.yml`
returns to the generic project without associated domains. Arbitrary HTTPS servers can advertise system-browser PKCE sign-in, including
first-party passkeys; this does not require an app-associated domain for each server.

Setup, invitation, and quick-login links use:

```text
https://invite.example.com/connect#v=1&purpose=invite&mode=self_hosted&server=https%3A%2F%2Fframe.example.com&token=OPAQUE_SECRET
openfotoframe://connect#v=1&purpose=setup&mode=self_hosted&server=https%3A%2F%2Fframe.example.com
```

All payload fields are in the fragment. Setup links have no token; invitations
require a token; pairing requires `id` and `token` (a short-lived pairing code).
HTTPS landing links must match the configured invitation host. Pasting/scanning a
link never fetches its destination until the user confirms it. Public servers
require HTTPS. Local HTTP requires explicit confirmation before any request.

Saved connection metadata is stored in UserDefaults. Access and rotated refresh
credentials are scoped to canonical server origin plus account ID and stored in
Keychain with `WhenUnlockedThisDeviceOnly` protection. They are also bound to the
server's stable identity. Passwords are never retained. Each connection owns an
immutable-origin URLSession; the app uses ephemeral WebKit data per selected
scope. Native API requests use bearer credentials and explicit household,
collection, and frame headers. Advanced editors redeem a short-lived, one-use
web bridge by same-origin native POST with automatic redirects disabled. Only
the three validated exact-host bridge cookies enter the selected scope’s ephemeral
WebKit store before the advanced page loads. The page then uses the scoped
cookie/CSRF web session.
Credentials never appear in browser URLs or JavaScript storage.

The existing `frameServer` preference is imported once as an **Existing frame**
profile. It does not imply a first-party account or grant permissions. Older
servers remain available through explicitly selected **Existing Web Companion**
compatibility; the new native sign-in requires API v1. No changes are applied to
an existing server or its library by opening the app.

`ConnectionTests` cover link parsing, credential scoping, metadata migration,
delayed responses during connection switching, and local HTTP confirmation.
Use the isolated integration fixture and a dedicated simulator for native/web
editor regression checks. No release, deployment, or TestFlight upload is part of
these development commands.

For the new native sign-in and selected collection/frame smoke test, start a
separate disposable API v1 fixture:

```sh
.venv/bin/python ios/scripts/release_integration_server.py
xcodebuild -project ios/OpenFotoFrame.xcodeproj -scheme OpenFotoFrameReleaseIntegration \
  -destination 'platform=iOS Simulator,name=YOUR_DEDICATED_SIMULATOR' \
  -parallel-testing-enabled NO -only-testing:OpenFotoFrameTests \
  -only-testing:OpenFotoFrameUITests/OpenFotoFrameUITests/testHostingChoiceAndExplicitLocalHTTPConfirmation \
  -only-testing:OpenFotoFrameUITests/OpenFotoFrameUITests/testReleaseNativeLoginAndScopedGallery test
```

The fixture imports only repository code, creates temporary relational/media
storage, binds localhost port 5081, and seeds an owner, collection, frame, and
geometric photo. It never uses deployment databases, accounts, or photos. Its
credentials are test fixtures, not application defaults. Hosted billing displays
only server-configured plans; checkout is unavailable without a configured
provider. Checkout opens an explicitly confirmed HTTPS provider URL in the system
browser and uses a stable request idempotency key across retries. No subscription
is activated by a client redirect or a successful checkout-screen dismissal.

Keep simulator ad-hoc signing enabled for the API v1 smoke test: the native client uses the real Keychain, and an unsigned simulator application cannot access its credential records. This does not require distribution signing or upload.

### Hosted subscriptions and browser sign-in

The native app uses StoreKit 2 for Apple subscriptions, loading only product identifiers configured by the central service. Apple transaction proof is accepted only for the exact HTTPS origin configured in `FrameHostedServerURL`; self-hosted and invitation-supplied servers cannot receive App Store proof. Each purchase intent returns a server-generated, permanently household-bound `appAccountToken`. Purchase, restore and unfinished transactions require backend verification before the transaction is finished or the UI reports an active subscription. Switching connection/account/scope during purchase prevents submission to the new connection. Unfinished transactions remain available for an explicit retry. Stripe checkout remains supported by the website; native external checkout is disabled by default (`FrameAllowsExternalCheckout=false`) pending storefront and distribution policy review.

Before release, configure real App Store Connect subscription products, bundle/environment identifiers, server Apple verification credentials and notification endpoints. Exercise sandbox purchase, Ask to Buy pending, cancellation, renewal/refund, restore and a switched household/account on physical devices. Local builds do not activate providers or perform purchases.

HTTPS servers advertising `auth_code_pkce` also offer system-browser sign-in, including server passkeys. The app generates a random verifier and state, opens only the selected server’s `/authorize/<request>` route in an ephemeral `ASWebAuthenticationSession`, validates the exact callback state and issuer, then redeems the one-use code with its verifier. Callback codes are not bearer credentials. Password/MFA native sign-in and QR approval remain available; HTTP compatibility servers do not receive passkey/browser authorization.

Frame owners can use **Connection & Access → Set Up Display** to create a two-minute enrollment code. The physical display opens `/display/enroll` and enters the code; the code is never appended to a URL. Credentials can be revoked from the same screen. No physical TV is contacted by the app’s enrollment preparation.

The native API v1 integration fixture uses port **5081**. Do not change it to 5060/5061: browser engines block those SIP ports, producing an empty web document even though native API requests succeed. The integration test exercises real Keychain sign-in, scoped native gallery/media, upload preflight/upload, explicit-frame pause/play, denial without a selected frame, and the cookie-bridged advanced crop editor.

Apple restore and unfinished-transaction retry read the selected existing household’s permanent account token using owner-only `GET /billing/apple/account`; this read never creates a household, account token or purchase intent. The app filters verified StoreKit transactions by that token and the configured product ID, then sends their signed proof to the same central origin. Active households retain Restore/Manage controls while new purchase buttons are disabled. An active subscription does not prevent a legitimate restore through the original provider.
