# Local screenshot demo

From the repository root:

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python scripts/demo_server.py
```

Open <http://127.0.0.1:5080/gallery> for the seeded gallery or
<http://127.0.0.1:5080/display> for the slideshow. Upload, settings, groups and
photo controls work normally. The local demo signs in automatically as `demo`;
CSRF protection remains enabled. It binds only to loopback and rejects non-local
requests. Do not put this demo behind a public proxy.

Each launch copies the application into a new temporary directory and creates
six stock photos, thumbnails, metadata and a two-photo group. Existing data,
uploads, accounts and server configuration are never copied. Backup/hardware
scheduling is stopped in the disposable copy. Stopping the process removes demo
data; restarting resets all edits. No DNS, remote deployment or TV changes are
needed. Use `--port` for a different local port.

If started as the `openfotoframe-demo` systemd user service, stop it with
`systemctl --user stop openfotoframe-demo.service`. It is a temporary service,
not a boot-time or scheduled deployment.

Photos download once into `$XDG_CACHE_HOME/openfotoframe-demo` (normally
`~/.cache/openfotoframe-demo`). Use `--offline` after the first run or set
`--cache-dir` to use another cache. Photos are not bundled in the repository.

## Photo credits

Images are provided by [Lorem Picsum](https://picsum.photos/) from Unsplash,
under the [Unsplash License](https://unsplash.com/license). Source links and
photographers are recorded in [demo-photos.json](demo-photos.json):

- Coastal Forest and Forest Waterfall — Paul Jarvis
- Summer Creek — Jerry Adney
- Snowy Peaks — Go Wild
- Morning Coffee — Luke Chesser
- Island Light — Margaret Barley

Keep these credits with screenshots/documentation that use the demo photos.
