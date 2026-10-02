# OpenFotoFrame — Generated Art Display Design

Status: Phase 1 is implemented. Physical sensor integration and additional artwork
options in Phases 2 and 3 remain proposed. Raspberry Pi performance targets still
require measurement on the target device.

## 1. Goal and scope

Add generated art as another OpenFotoFrame display option. Each display can show
its existing photo slideshow or a selected procedural artwork. The first artwork
is an animated field of organic cells inspired by stained glass or illuminated
stone. It establishes the module interface for a growing collection of art options.

Here, “generated art” means graphics generated and animated locally in the browser.
The initial feature requires no image generation API, paid service, Internet
connection, or radar sensor. Optional interaction can enrich any artwork that
supports it.

Use the existing Flask application, vanilla JavaScript, Jinja templates, Docker +
Caddy deployment, Chromium kiosk, authentication, display profiles, and TV power
scheduling. Both all-in-one installations and displays connected to a remote
backend remain supported.

The initial scope is a choice between photos and art. Mixed photo/art playlists,
art rotation, synchronized animation across screens, and additional input adapters
are later extensions.

## 2. Existing integration points

The repository currently provides:

- `/display` with an optional `display` query parameter identifying a profile.
- `data/settings.json` with display profiles, dimensions, and synchronization settings.
- `/api/displays` and `/api/displays/<display_id>` for profile management.
- `/api/display/state` and `/api/display/control` for display state and controls.
- `templates/display.html`, `static/js/display.js`, and `static/css/display.css`
  for the kiosk interface.
- Server-rendered photo snapshots from `render_display.py`; the current browser
  polls display state every three seconds and crossfades between snapshot images.
- Display enrollment and session authentication, alongside existing permitted
  local and authenticated access paths.

The existing art-independent infrastructure should serve both modes. Art renders
on the display device because it needs continuous animation. Photo snapshots
continue through the current rendering path. `/api/images` and `data/gallery.json`
retain their photo and group semantics.

## 3. User experience

In the existing display management UI, provide a **Content** choice:

- **Photos**: the current slideshow.
- **Art**: an artwork selector, preview, and settings for that artwork.

Select art independently for each display. A living-room display can show organic
cells while another display shows photos. Art works with an empty photo gallery.
Switching modes preserves each mode's saved configuration. Independent photo
slideshows resume their stored position; synchronized displays rejoin the current
shared photo position when returning to photos.

Start with one catalog entry, **Organic Cells** (`organic-cells`). Show it through
the same selector and settings UI that later artworks will use. Do not make its
settings permanent fields of the shared display UI.

A preview uses the same module and saved seed as the kiosk. Unsaved changes stay
local until the user saves them. Pointer simulation is available in preview and
explicit development mode; it is disabled in the normal kiosk so moving a mouse
to reveal controls does not disturb the artwork.

Keep fullscreen and pause/play controls. In art mode, pause freezes animation and
interaction effects at the current frame; play resumes without a large time jump.
Hide previous/next when only one artwork is selected. A later art playlist can
assign those controls to artwork navigation.

The TV power schedule continues to operate in either mode. Power scheduling and
content selection are separate concerns.

## 4. Architecture

```text
OpenFotoFrame backend (Flask + existing persisted settings)
    │
    ├── display profiles + content selection + art catalog
    ├── /api/display/state and /api/display/control
    └── optional normalized interaction feed, scoped to display
                       │
                       ▼
/display — shared display controller and kiosk shell
    │
    ├── Photos → existing snapshot renderer and crossfade
    └── Art    → artwork host → selected artwork module → Canvas/WebGL
                                  ▲
                                  │ abstract interaction state
                 idle state / preview simulator / optional sensor
```

Keep artwork selection, rendering, and interaction independent. An artwork
consumes abstract interaction state and never parses sensor packets, opens a
serial device, fetches configuration, or manages authentication itself.

## 5. Display configuration and persistence

Extend each existing display profile in `data/settings.json` with an explicit
`content` object. Example of proposed persisted configuration:

```json
{
  "id": "default",
  "name": "Main display",
  "active": true,
  "width": 1920,
  "height": 1080,
  "synchronized": true,
  "content": {
    "mode": "art",
    "art": {
      "artwork_id": "organic-cells",
      "settings_version": 1,
      "seed": 42173,
      "settings": {
        "palette": "warm-stone",
        "custom_cell_colors": null,
        "grout_color": "#171411",
        "cell_density": 80,
        "idle_speed": 0.05,
        "interaction_strength": 0.7
      }
    }
  }
}
```

Profiles without `content` resolve to `mode: "photos"`. Legacy single-display
installations therefore retain their current behavior without user intervention.
Do not require art settings until art is selected. Keep the last saved art
configuration when switching back to photos.

The backend registry supplies defaults, validates settings, and handles versioned
settings migrations for each artwork. Reject unknown artwork IDs, invalid modes,
and invalid settings on writes without changing the saved profile. Use finite
numbers, bounded ranges, and allowlisted enum values. Migrations must not silently
reset a user's palette or seed.

The existing backup/restore path must include these profile fields. Optional
sensor configuration and calibration are also persisted under `data/`; verify
backup coverage before introducing a separate data file. Secrets and live sensor
samples are not artwork settings.

## 6. Artwork catalog and module contract

Use a small explicit registry of bundled artworks. An entry contains:

- Stable ID, display name, description, and a static preview asset.
- Allowlisted local JavaScript module path.
- Settings version, defaults, and a declarative settings schema.
- Capabilities such as interaction support and Canvas or WebGL rendering.

The schema describes each control's label, type, bounds or options, and default.
The UI builds controls from that schema; the server independently validates the
values. Store only the ID and configuration on the profile, not executable code
or an arbitrary module URL. Adding an artwork requires one registry entry, its
module, and its preview asset, without another branch in the display controller.

Proposed browser contract:

```js
// Host imports only registry-approved modules.
export function createArtwork({ container, seed, settings, quality }) {
    return {
        resize({ width, height, pixelRatio }) {},
        setSettings(settings) {},
        updateInteractionState(state) {},
        render({ elapsedSeconds, deltaSeconds }) {},
        dispose() {}
    };
}
```

The host owns the animation clock, `requestAnimationFrame`, pause state, resize
observation, interaction delivery, and module loading. Modules own their drawing
resources. `dispose()` removes their elements, listeners, and GPU resources;
modules must not create independent animation loops or polling timers.

On pause, the host holds elapsed animation time and the visible interaction state.
On resume, it supplies the newest interaction state and a bounded frame delta.
While hidden, suspend rendering and resume with the same bounded-clock behavior.

A seed allows repeatable initial geometry and a useful preview. It does not promise
pixel-identical animation across different browsers, devices, or start times.

## 7. Display state and controls

Extend `/api/display/state` additively with `content_mode`. Photo responses retain
the current fields and snapshot semantics. Art responses supply a resolved artwork
configuration and use `snapshot_url: null`, `index: 0`, and `total: 0` for the
photo-specific fields while retaining the shared `paused` field.

Proposed art response fields:

```json
{
  "content_mode": "art",
  "paused": false,
  "snapshot_url": null,
  "index": 0,
  "total": 0,
  "art": {
    "artwork_id": "organic-cells",
    "settings_version": 1,
    "seed": 42173,
    "settings": {
        "palette": "warm-stone",
        "custom_cell_colors": null,
        "grout_color": "#171411",
      "cell_density": 80,
      "idle_speed": 0.05,
      "interaction_strength": 0.7
    },
    "revision": "server-assigned-configuration-revision"
  }
}
```

The host reconciles mode, artwork ID, and revision on the existing state poll.
Mode changes dispose the current renderer before mounting the next one. A
settings-only change uses `setSettings()` when supported; structural changes such
as seed or geometry can recreate the module. Art state reads do not build photo
slides or generate snapshots.

Extend the existing profile write API to accept validated `content` updates.
Expose the registry through a proposed `GET /api/artworks`, readable by the same
users and displays allowed to read display state. Include safe catalog metadata,
settings schemas, and local module paths, with no device credentials.

Pause/play must work in art mode even when there are no photos; the current
control handler's empty-gallery early return needs to be moved into the photo
branch. Previous/next in the initial art mode return an explicit unsupported-action
error without modifying photo position.

Preserve current photo synchronization. For the first version, art pause state is
independent per display, even when a profile has `synchronized: true` for photos.
Switching an art display must not pause or advance another display's photos.
Art pause state is runtime state and resets to playing after a backend restart.

Use existing display access checks for reads and controls, existing authenticated
profile management permissions for content changes, and existing CSRF protection
for browser writes. Sensor administration is restricted to admins. No new route
uses an unauthenticated alternate art page or accepts arbitrary executable assets.

## 8. First artwork: Organic Cells

Implemented shape options: organic cells (the original default), circles, rectangles,
hexagons, triangles, and diamonds. Circle gap fill (0–1) adds smaller circles into
free space; rectangle squareness (0–1) biases the rectangle size distribution from elongated tiles toward squares.
All geometric patterns use seeded size/location variation; density sets typical
size. Polygon layouts partition the exact screen bounds with fitted boundary
cells. Circle packing keeps every circle whole inside the screen, with grout
filling the gaps. Viewer influence sweeps equally over the full screen height;
room distance changes strength only slightly.
These settings share the existing palette, independent grout, seeded geometry,
and viewer interaction. Older saved configurations receive the organic default.

Generate a stable arrangement of Voronoi-like cells from the saved seed. Use
irregular or softened geometry with independently colored grout and a broad, slowly evolving color
field. Cache geometry and redraw changing color values rather than rebuilding
cells on every frame.

Initial controls:

| Setting | Meaning | Initial validation |
|---------|---------|--------------------|
| `palette` | Cell color collection | Bundled palette IDs or `custom` |
| `custom_cell_colors` | User-defined cell palette | Required for `custom`: 2–12 opaque `#RRGGBB` colors; otherwise null |
| `grout_color` | Independent color between cells | One opaque `#RRGGBB` color |
| `cell_density` | Approximate number of cells | Integer, 20–300 |
| `idle_speed` | Rate of ambient color drift | Number, 0–1 |
| `interaction_strength` | Strength of viewer influence | Number, 0–1 |
| `seed` | Stable starting geometry | Integer, 0–2147483647; stored outside settings |

### Cell palettes and colorways

Provide a broad selection spanning warm, cool, muted, vivid, neutral, and
monochrome looks. Each bundled cell palette contains explicit color stops rather
than only a hue range. Initial catalog:

| Palette ID | Display name | Cell colors | Suggested grout |
|------------|--------------|-------------|-----------------|
| `warm-stone` | Warm Stone | `#D8C3A5`, `#B89B72`, `#8C735B`, `#E8DCC8` | `#171411` |
| `terracotta` | Terracotta | `#B85C38`, `#D98E64`, `#E9BE96`, `#8E493A` | `#352820` |
| `desert` | Desert | `#E4C690`, `#C59B63`, `#D98973`, `#8E9B80` | `#514639` |
| `forest` | Forest | `#234D3C`, `#52785A`, `#8BA67B`, `#C2C9A0` | `#101D17` |
| `ocean` | Ocean | `#123B5D`, `#236B8E`, `#54A6B5`, `#B3DAD8` | `#0B1D2B` |
| `glacier` | Glacier | `#DDEBF0`, `#AFCBD5`, `#779BAE`, `#E9E5F2` | `#344853` |
| `dusk` | Dusk | `#5B456B`, `#946B8A`, `#CD9293`, `#E9BFA2` | `#251E30` |
| `jewel` | Jewel Tones | `#176B63`, `#324B9B`, `#713B89`, `#AE365B`, `#D6A440` | `#15121D` |
| `citrus` | Citrus | `#F2C94C`, `#E89B35`, `#A8BA54`, `#F3DEA0` | `#383820` |
| `pastel` | Soft Pastels | `#EBC7D2`, `#C8D7EC`, `#CDE2D0`, `#F0E0B9` | `#F5F0E8` |
| `earth` | Earth Neutrals | `#6D6257`, `#9B8B78`, `#C5B8A6`, `#E2D8C9` | `#302B26` |
| `monochrome` | Monochrome | `#262626`, `#666666`, `#AAAAAA`, `#E5E5E5` | `#101010` |

A **colorway** is a convenient preset pairing a cell palette with a suggested
grout color. Offer swatch previews that show both the cell colors and grout.
Choosing a full colorway explicitly updates both controls. Choosing only a cell
palette preserves the selected grout color; changing grout preserves the cell
palette. Mark a modified pairing as customized so the UI does not claim it still
matches the original colorway.

The persisted `palette`, `custom_cell_colors`, and `grout_color` values are
authoritative. Colorway presets resolve to those values on selection; do not store
a competing colorway ID that could override a user's separate selections on reload.

For cells, provide a **Custom** option with 2–12 editable color stops, plus add,
remove, and reorder controls. Selecting it initially copies the current bundled
palette into editable stops. Reject a custom palette with too few colors or invalid
color strings. Moving back to a bundled palette clears `custom_cell_colors` to null.
The preview reflects edits immediately; saving persists the configuration for that
display through the existing profile API.

For grout, provide independent swatches for near-black, charcoal, warm brown,
deep navy, forest green, medium gray, ivory, and white, plus a color picker and hex
entry for any opaque color. Grout is not derived from the cells, a photo's mat
color, or automatic mat-color settings. It remains fixed during animation unless
the user changes it. Light grout and low-contrast combinations are valid choices.

Cell animation interpolates within the selected color stops; idle movement and
viewer interaction can modulate brightness and influence without replacing the
chosen palette with unrelated hues. Settings changes update colors without
regenerating the seeded cell geometry.

Expose the palette choices, custom color-list editor, and grout color control
through the artwork's declarative settings schema so future artworks can reuse
the same UI control types. The catalog supplies the colorway metadata and swatches.
Validate and normalize hex colors on the backend as well as in the preview.

For older art configurations containing only `palette`, retain that palette and
fill missing `custom_cell_colors` with null and `grout_color` with `#171411`, the
initial dark-grout default. Include both channels in settings revisions, preview,
backup/restore, and configuration round trips.

When nobody is present, animation stays slow and visually interesting. Avoid
obvious short loops and abrupt changes. Each viewer influences a broad field,
rather than one tile:

- Horizontal position moves the center of influence.
- Nearer viewers increase influence strength.
- Faster movement increases ripple or turbulence.
- Stationary viewers allow the field to settle or bloom.
- Multiple viewers contribute blended influences.
- Departing viewers' influence fades over several seconds.

Begin with Canvas 2D. WebGL remains available for later modules if profiling shows
it is useful. Use normalized geometry to fit landscape, portrait, and other
configured display dimensions. Start fullscreen; optional framing can be added
through artwork or host settings later.

## 9. Optional interaction

All artworks receive a valid interaction state, including an empty idle state.
Sensor availability is never a prerequisite for selecting or running art.

Proposed shared state:

```json
{
  "version": 1,
  "sequence": 1042,
  "display_id": "default",
  "source_status": "connected",
  "people": [
    { "id": "radar-1", "x": -0.35, "y": 0.52, "vx": 0.08, "vy": -0.02, "speed": 0.082 }
  ],
  "occupancy": 1,
  "activity": 0.31
}
```

Coordinates are normalized: `x` is left-to-right in `[-1, 1]`; `y` is distance
from the screen in `[0, 1]`, increasing into the room. Velocities are normalized
coordinate units per second; speed is their magnitude. Activity is bounded to
`[0, 1]`. IDs are strings scoped to the source. An adapter must not claim stable
target identity when the hardware cannot provide it reliably.

The interaction layer validates readings, maps calibrated physical coordinates,
filters noise, smooths movement, handles disappearing targets, and publishes the
state. Derive velocity when the source cannot provide it; mark unreliable readings
rather than inventing sensor precision. The renderer interpolates using a local
monotonic clock and receipt times, so split installations do not require clocks
to be synchronized.

The first version delivers idle state or local preview simulation without a
realtime backend dependency. When radar is added, use a same-origin authenticated
WebSocket feed, proposed as `/api/art/interaction?display=<id>`, separate from the
three-second display configuration poll. Send interaction snapshots at the source's
supported rate, targeting 10–30 updates per second; browser frame rate is independent.
Send the latest state on connection, discard older sequences, and keep queues
bounded so slow clients receive current state rather than accumulated history.

Before adding that endpoint, choose and verify a WebSocket-capable Flask serving
configuration, dependency, worker model, and Caddy upgrade routing. The current
requirements alone do not establish WebSocket support. Define a shared broker or
a single interaction owner so multiple server workers see the same tracking data.
Validate Origin and the existing display session during the handshake; close and
reauthenticate on session expiry or revocation. Do not put long-lived credentials
in a WebSocket URL.

A stale feed clears occupancy after a configurable timeout (initial default:
1200 ms) and smoothly releases visual influence. Connection loss enters idle mode
and reconnects with backoff. Client simulation remains local and cannot alter
another display's interaction state.

## 10. Sensor deployment and calibration

Keep sensor adapters outside artwork modules. A small optional Python service
reads USB/serial/UART input and publishes normalized observations. Pick and
implement a concrete radar protocol during the hardware phase; multi-target
position data is preferred over a presence-only signal. Other adapters can later
supply phone, camera, audio, or Home Assistant input through the same abstraction.

Deployment follows the physical location of the screen and viewers:

- **All-in-one:** run the optional sensor service on the kiosk host. Grant only
  the required device access if it runs in a container.
- **Split:** run the sensor service beside the remote display, and forward its
  observations to the backend through an authenticated TLS connection. Bind its
  source identity to an allowed display ID; a source must not choose arbitrary
  displays. Keep its service credential separate from browser enrollment and CEC
  credentials, with an admin-managed provisioning and rotation path.

No radar is assumed to be attached to the remote backend. The existing kiosk still
opens `/display?display=<id>` through the current enrollment flow; there is no
separate art server or kiosk launch URL.

Persist sensor device settings, source-to-display bindings, smoothing, timeout,
and calibration separately from individual artwork parameters. Room dimensions
use meters before normalization. Calibration records center, left/right boundaries,
and near/far distances; optionally invert horizontal orientation. Provide an admin
calibration view that shows raw and mapped positions, validates nonzero spans, and
saves the mapping. Calibration applies to all artworks on that display.

Reconnect failed devices automatically. Sensor failure leaves art running in idle
mode. Use the existing Docker restart and kiosk supervision arrangements; only
the optional host sensor service requires additional service setup.

## 11. Performance and recovery

Target the project's Raspberry Pi and Linux display devices. Design for 1080p and
4K output, with a capped internal canvas resolution and pixel ratio so a 4K screen
does not force full-resolution animation. Start with a 30 fps budget on constrained
devices; allow 60 fps where measured performance supports it. Adapt drawing quality
without changing the saved artistic configuration.

Use locally bundled assets and dependencies. After a successful load, animation
continues through backend or network outages using the last configuration and idle
interaction. A fresh load still requires the backend; complete offline startup
would require a separate caching design.

If an artwork fails to import, initialize, or render, dispose it and show a calm
built-in static fallback without a repeated crash loop. Report the artwork ID and
failure for diagnosis. Retain the user's art selection; configuration changes or
an explicit retry can recover it. Switching back to photos must remain possible.

Pause or reduce rendering work when the page is hidden. Do not expose debug details
on the normal TV view. Preview/development diagnostics can show frame rate,
source status, people, and normalized positions. Runtime frame rate comes from the
browser rather than an invented backend metric.

## 12. Proposed repository additions

```text
app.py                         # Extend profiles, state/control, catalog route
artwork_catalog.py             # Registry, validation, version migration
static/js/display.js           # Shared host and mode reconciliation
static/js/display-art.js        # Module lifecycle, clock, resize, quality
static/js/artworks/
    organic-cells.js            # First implementation of the artwork contract
static/artworks/
    organic-cells-preview.svg   # Bundled catalog preview
static/js/art-preview.js        # Preview host and local interaction simulator
templates/display.html         # Shared photo/art mount points
static/css/display.css         # Shared shell and art surface styles
# Existing display management template/script: content selector and schema controls
# Extend existing display tests; add catalog and module lifecycle coverage
```

Later sensor work adds `interaction/` for normalization/tracking and
`scripts/art-sensor.py` for the optional device adapter service. These are proposed
additions within OpenFotoFrame, rather than a second backend/frontend repository.

## 13. Delivery plan and acceptance criteria

### Phase 1 — Integrated art option

Implement profile defaults and validation, registry/catalog, shared display host,
Organic Cells, per-display mode selection and settings, preview, and pause/play.
Idle animation is the default; pointer simulation permits development without
hardware. Complete the common module contract in this phase.

Acceptance criteria:

- Existing installations open `/display` in photo mode with current behavior.
- A display can select art and render it with no uploaded photos or sensor.
- Two profiles can select different content modes and configurations.
- Selection, settings, and seed survive restart and backup/restore.
- Switching modes repeatedly leaves one active renderer with no leaked loops or listeners.
- Pause/play works with an empty gallery and does not affect another display's photos.
- Invalid configuration writes are rejected; failed modules recover to the static fallback.
- Preview and kiosk use the same artwork implementation.
- The bundled colorways, custom cell palettes, and independent grout colors are selectable and previewed together.
- Changing only cells or grout preserves the other selection and the seeded geometry.
- Both color channels survive save/reload, restart, and backup/restore; older palette-only settings receive defaults.
- Invalid custom palettes and grout colors are rejected without changing the saved configuration.
- Animation continues after a network disconnect following initial load.

Verify relevant backend behavior with pytest, including photo compatibility,
profile isolation, configuration validation, permissions, and empty-gallery art
controls. Verify lifecycle, pause timing, resize, and error recovery in the browser.
Measure sustained frame rate and memory on a target display device; inspect both
1080p and 4K output before treating the performance target as met.

### Phase 2 — Optional physical interaction

Implement one selected radar adapter, calibration, filtering, source authentication,
display-scoped WebSocket delivery, and all-in-one/split service installation.
Validate reconnects, stale-input decay, and operation without hardware. Verify that
the deployed worker model shares interaction state and that revoked sessions and
unauthorized sensor/display bindings cannot access the feed.

### Phase 3 — Additional art options

Add a second artwork using only the registry and module contract. This is the
practical check that Organic Cells has not become hardcoded into the host. Consider
art rotation, scene transitions, mixed photo/art playlists, and synchronized art
only after defining their selection, timing, and control semantics. Additional
input adapters reuse the normalized interaction model.

The architectural rule across every phase is: OpenFotoFrame owns display management,
artwork modules own visuals, and optional inputs supply abstract interaction state.
