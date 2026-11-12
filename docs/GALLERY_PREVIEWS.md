# Displayed-slide gallery

The web gallery and SwiftUI gallery default to composed photo slides, including
manual groups, synthetic automatic portrait pairs, crops, display-specific
appearance, and mats. Each card is one slide; original uploads remain available
in a separate originals view for selection, visibility, grouping, and deletion.
Automatic pairs offer individual photo editors and are never persisted or treated
as editable saved groups. Pair membership can change with the display's daily
shuffle. Hidden photos are absent from the displayed-slide view.

The default gallery remains a grid of rectangular thumbnails matching the display
aspect ratio. Captions and edit buttons are omitted from the grid. Clicking or
tapping a thumbnail opens a slide preview, where the user can edit the saved group
or an individual photo. An automatic pair offers an editor for each member photo.

`GET /api/gallery/display-slides` returns the current display profile and slide
membership. An optional profile ID path segment selects a specific display.
`GET /api/gallery/display-thumbnails/<display_id>/<slide_id>` returns a JPEG with
at most 400 pixels on its longest edge. The complete display composition is
downsampled after rendering so mat and bevel geometry match the display. Legacy
servers reuse their existing display snapshots; release servers render on demand
using the same renderer as their display. These routes do not change grouping,
photo metadata, playback position, or TV power.

Both inventory and thumbnail requests require management-user authorization.
Release servers recheck the selected collection's read permission, plus the
selected frame's permission when applicable, on every request. A frame containing
other collections does not expand this gallery's collection scope. Without a
selected frame, a collection preview uses its defaults. A selected frame that
does not contain this collection returns no slides. Frame-only controllers cannot
read a collection through this preview API. Responses are private and no-store;
release rendering retains its existing rate limit. No additional persistent
client photo cache or original-file copy is introduced.

Older servers without the new API show an explicit preview-unavailable message;
the originals view continues to work. New backend routes must be deployed before
the updated app can show composed thumbnails. Rendering/caching optimization for
large paid-service libraries remains a separate performance concern; this change
does not add a persistent derived-image cache to the release server.
