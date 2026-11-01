# Using OpenFotoFrame

Photo management, appearance, generated art and TV controls. For installation, see [INSTALLATION.md](INSTALLATION.md).

## Pages

| Page | URL | Description |
|------|-----|-------------|
| Login | `/login` | Sign in |
| Upload | `/upload` | Upload photos, adjust settings |
| Gallery | `/gallery` | Manage photos (show/hide/delete) |
| Display | `/display` | Fullscreen slideshow for TV |
| Backup | `/backup` | Dropbox backup management (admin only) |
| Users | `/admin/users` | User management (admin only) |

## User Roles

| Role | Permissions |
|------|-------------|
| **Admin** | Upload, manage gallery, manage users, change settings, manage backup |
| **User** | Upload, manage gallery, change own password |

## Gallery Management

The gallery page lets you:

- **Show/Hide Photos** - Toggle visibility without deleting
- **Group Images** - Click "Group" to select images that display together as a multi-image slide
- **Bulk Actions** - Select mode for show/hide/delete across multiple photos
- **Filter View** - Show all, visible only, or hidden only
- **Upload Order** - Gallery shows newest uploads first
- **See Metadata** - Upload date, uploader, file size

Hidden photos remain on disk but won't appear in the slideshow.

## Settings

Settings are organized into two tabs in the sidebar:

### Mat Tab

| Setting | Description |
|---------|-------------|
| **Mat Color** | Background color around photos (10 neutrals + 25 accent colors) |
| **Mat Finish** | Texture overlay: Eggshell (default), Flat, Linen, Suede, or Silk |
| **Border Effect** | Bevel (uniform inset border) or Shadow (drop shadow) |
| **Bevel/Shadow Size** | Effect width around images (0-16px) |

### Settings Tab

| Setting | Description |
|---------|-------------|
| **Slideshow Interval** | Seconds between transitions (3-300, default 60) |
| **Transition Duration** | Fade animation length |
| **Image Fit** | "Contain" (full image) or "Cover" (fill screen) |
| **Shuffle** | Randomize photo order |
| **Target Screen Ratio** | 16:9, 21:9, 4:3, or 1:1 |
| **TV Power Schedule** | HDMI-CEC on/off times by day of week |

Mat color, mat finish, border effect, and image scale can be overridden per image or per group from the upload page preview. Image cropping is also available per image.

## Generated Art

Open **Settings → Displays → Edit content** for a display and choose **Art**.
Choose Organic cells, Circles, Rectangles, Hexagons, Triangles, or Diamonds in the content editor. Circles offer a gap-fill slider that adds smaller circles; rectangles offer a squareness slider that biases their proportions toward squares. Each seed varies sizes and locations; density controls typical size. Polygon cells fit the screen as complete partitions, with adapted boundary cells rather than cropped rows. Circles remain whole, with grout between them. Viewer color sweeps cover the full height. Shape changes preserve the palette, grout, seed, and viewer response.

Organic Cells animates locally in the browser and works with no photos or sensor.
Select a colorway to set both cells and grout, or choose a cell palette and grout
color independently. Custom cell palettes support 2–12 colors, with reorder controls.
The live preview supports pointer interaction; click **Save content** to apply it
on that display. Preview changes do not affect the TV until saved.

The existing `/display?display=<id>` URL serves both photos and art. Pause/play
freezes and resumes art independently of other displays. Switching back to Photos
preserves art settings. Independent photo slideshows resume their stored position;
synchronized photo displays rejoin the current shared slideshow position.

Art uses bundled assets and keeps animating through a backend outage after it has
loaded. Fresh startup needs the backend. Radar integration is planned separately;
normal kiosk art currently runs in ambient mode. New artwork modules can be added
through the catalog and shared lifecycle in `static/js/display-art.js`.

A separately deployed test instance is documented in [docs/TEST_INSTANCE.md](TEST_INSTANCE.md).

## TV Power Schedule (HDMI-CEC)

Control your TV's power automatically using HDMI-CEC. During install, choose "Enable HDMI-CEC TV power control" to set up the CEC device passthrough.

### Configuring Schedules

In the web UI settings panel:
1. Click **+ Add Schedule** to create a new on/off time pair
2. Set the on time, off time, and select which days of the week
3. Add multiple schedules for different viewing patterns
4. Use **Test On** / **Test Off** buttons to verify CEC control works

### Requirements

- TV must support HDMI-CEC (most modern TVs do)
- Pi must be connected via HDMI
- `cec-utils` is installed automatically during setup
- The CEC device (`/dev/cec0`) must be passed through to the Docker container


## Troubleshooting CEC

- If CEC status shows "unavailable", ensure the device mapping is uncommented in `docker-compose.yml`
- Some TVs use different CEC brand names (Anynet+, Bravia Sync, SimpLink, etc.) — the protocol is the same
- Not all TVs respond to all CEC commands

## Duplicate Detection

When uploading photos, the app uses perceptual hashing (pHash) to detect potential duplicates:

- Each image gets a structural fingerprint that's compared against existing gallery images
- If a close match is found (Hamming distance < 10), you'll see a warning with a side-by-side comparison
- You can still choose to upload the image if desired
- Low-resolution images (below 1280x720) also trigger a warning

### Backfill Hashes

If you had photos uploaded before duplicate detection was added, run the backfill to compute hashes for existing images:

```
POST /api/gallery/backfill-hashes  (Admin only)
```

[Back to overview](../README.md)
