"""Dashboard HTTP/SSE smoke tests — stdlib only, no mcp import."""

import json
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import events
from dashboard import start_dashboard


class TestDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.snapshot = []
        os.environ["DELEGATE_DASHBOARD_PORT"] = "0"  # ephemeral: never collide
        cls.url = start_dashboard(lambda: cls.snapshot)
        assert cls.url is not None

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("DELEGATE_DASHBOARD_PORT", None)
        cls.tmp.cleanup()

    def test_index_serves_html(self):
        with urllib.request.urlopen(self.url + "/", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("cc-delegate", resp.read().decode("utf-8"))

    def test_tasks_snapshot_json(self):
        type(self).snapshot = [{"taskId": "t_a", "status": "running", "workDir": ".cc-delegate"}]
        with urllib.request.urlopen(self.url + "/tasks", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data[0]["taskId"], "t_a")

    def test_events_streams_published_event(self):
        type(self).snapshot = []
        req = urllib.request.urlopen(self.url + "/events", timeout=10)
        try:
            events.publish(self.tmp.name, "t_sse", {"kind": "shell", "note": "$ ls"}, ".cc-delegate")
            line = req.readline().decode("utf-8")
            while not line.startswith("data: "):
                line = req.readline().decode("utf-8")
            payload = json.loads(line[len("data: "):])
            self.assertEqual(payload["task_id"], "t_sse")
        finally:
            req.close()

    def test_unknown_path_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.url + "/nope", timeout=5)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
