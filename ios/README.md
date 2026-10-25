# OpenFotoFrame for iPhone and iPad

A SwiftUI companion for the existing OpenFotoFrame server. Everyday photo management
is native; advanced workflows use the server's existing pages inside the app. No
backend migration, new API, or third-party iOS package is required.

## Open and run

1. Open `OpenFotoFrame.xcodeproj` in Xcode (16 or newer).
2. Choose the **OpenFotoFrame** scheme and an iPhone/iPad simulator, or select your
   signing team and a physical device. The deployment target is iOS 17. Andrew's
   signing team `BC9NG49J8V` is preserved in both the project and generator spec.
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
