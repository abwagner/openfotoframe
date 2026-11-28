import os
from pathlib import Path
import runpy
import tempfile
import time
import unittest


worker = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/release_worker.py"))
ready = worker["heartbeat_ready"]
write = worker["write_heartbeat"]


class ReleaseWorkerHeartbeatTests(unittest.TestCase):
    def test_idle_heartbeat_expires_and_claim_uses_the_queue_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heartbeat"
            self.assertFalse(ready(5, path))
            write("poll", path)
            self.assertTrue(ready(5, path))
            stale_poll = time.time() - 30
            os.utime(path, (stale_poll, stale_poll))
            self.assertFalse(ready(5, path))
            write("claimed", path)
            os.utime(path, (stale_poll, stale_poll))
            self.assertTrue(ready(5, path))
            expired_claim = time.time() - 601
            os.utime(path, (expired_claim, expired_claim))
            self.assertFalse(ready(5, path))
