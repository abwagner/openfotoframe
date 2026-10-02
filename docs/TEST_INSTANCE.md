# OpenFotoFrame test instance

The isolated server instance is available at **https://frame-dev.swagner.tech/upload**.
Sign in through the existing Authelia account. Andrew maps to the test administrator;
Allison maps to a separate test user. No production passwords, MFA secrets, or
session/enrollment credentials are copied into the test instance.

The test TV view is **https://frame-dev.swagner.tech/display**. Enroll it through
that instance's display enrollment flow, or open it while logged in. It starts in
Art mode with Organic Cells and an empty photo gallery. Use **Settings → Displays →
Edit content** to try colorways, custom cell colors, grout colors, or photo mode.

For a fullscreen movement preview, open
**https://frame-dev.swagner.tech/display?simulate=1** and move the pointer across
the pattern. Pointer input stays local to that browser. The regular display URL
runs the ambient artwork; the settings editor also supports pointer interaction.

## Isolation

| Resource | Production | Test |
|----------|------------|------|
| Container | `openfotoframe` | `openfotoframe-test` |
| Compose project | `swagner-home` | `openfotoframe-test` |
| Data volume | `photoframe_data` | `photoframe_test_data` |
| Upload volume | `photoframe_uploads` | `photoframe_test_uploads` |
| Hostname | `frame.swagner.tech` | `frame-dev.swagner.tech` |

The test instance has its own settings, users, gallery, session key, and display
and CEC credentials. It starts without TV schedules, backup credentials, CEC device
access, or physical display agents. Uploads and edits affect only its test volumes.
It shares the server's existing reverse proxy and SSO service.

## Deploying test code

The server's `proxy` Docker network, Traefik, CrowdSec, and Authelia must already
exist. The hostname must resolve to that server and be included in the SSO policy.
The server is configured with test display read/enrollment routes delegated to the
app's own authentication and management routes requiring the existing family/admin
SSO policy. A fresh server must configure those rules before using this Compose file.

To build and deploy the current checkout only to the test instance:

```bash
docker compose -p openfotoframe-test -f docker-compose.test.yml up -d --build
```

The initial image tag is `openfotoframe-test:0a08763`. Its image is separate from the
production `swagner-home-photo-frame` image, so rebuilding it does not replace the
production image. Existing test data survives rebuilds and restarts.

On first setup, provision local users for the SSO mappings. The current server has
already done this using fresh random local passwords, without copying production
users. The instance is configured to use SSO for management access.

To stop the test instance while retaining its data:

```bash
docker compose -p openfotoframe-test -f docker-compose.test.yml down
```

## Verification performed

- Test and production containers mount different data and upload volumes.
- Production container ID and persisted configuration stayed unchanged during setup.
- Both containers and the SSO service are healthy.
- Anonymous test display API requests return 401; management redirects to SSO.
- The admin SSO identity maps to a fresh test account and opens management.
- An authenticated test display renders and animates Organic Cells at the public URL.
- The test art catalog exposes all twelve colorways.
