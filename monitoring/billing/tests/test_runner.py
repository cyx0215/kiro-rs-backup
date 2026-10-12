"""Offline/local-only tests for the resident monitor."""

import json
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from billing_monitor import runner


class RunnerTests(unittest.TestCase):
    def test_dynamic_dashboard_is_independent_of_generated_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "report.html").write_text("old-static-report")
            (root / "latest.json").write_text('{"generation":1}')
            server = ThreadingHTTPServer(("127.0.0.1", 0), runner.handler_for(root, dynamic=True))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = "http://127.0.0.1:" + str(server.server_port)
            try:
                with opener.open(base + "/report.html", timeout=5) as response:
                    self.assertIn(b"fetchJSON('/latest.json')", response.read())
                # The unchanged collector may overwrite its static report every cycle.
                (root / "report.html").write_text("new-static-report")
                (root / "latest.json").write_text('{"generation":2}')
                with opener.open(base + "/latest.json", timeout=5) as response:
                    self.assertEqual(json.load(response), {"generation": 2})
                with opener.open(base + "/report.html", timeout=5) as response:
                    self.assertNotIn(b"new-static-report", response.read())
                with self.assertRaises(urllib.error.HTTPError):
                    opener.open(base + "/runner.py", timeout=5)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_dashboard_only_neither_collects_nor_requires_credentials(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(runner.os.environ, {}, clear=True), \
                mock.patch.object(runner, "run_cycle") as collect, \
                mock.patch.object(runner, "ThreadingHTTPServer") as server:
            self.assertEqual(runner.main(["--mode", "full", "--dashboard-only",
                                          "--output-dir", directory]), 0)
            collect.assert_not_called()
            server.return_value.serve_forever.assert_called_once()
            self.assertFalse((pathlib.Path(directory) / "status.json").exists())

    def test_cycle_keeps_sanitized_reports_and_does_not_log_cli_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            def collect(argv):
                print("synthetic-private-output")
                runner.cli.atomic_write(root/"latest.json", json.dumps({
                    "observed_at": runner.dt.datetime.now(runner.dt.timezone.utc).isoformat(),
                    "new_alerts": [{"kind": "coverage"}],
                }))
                runner.cli.atomic_write(root/"report.html", "sanitized-report")
                self.assertIn("--traces-only", argv)
                return 1
            with mock.patch.object(runner.cli, "main", side_effect=collect):
                status = runner.run_cycle(root, "traces-only")
            self.assertTrue(status["report_fresh"])
            self.assertEqual(status["exit_code"], 1)
            self.assertEqual(status["new_alert_kinds"], ["coverage"])
            self.assertNotIn("synthetic-private", (root/"status.json").read_text())

    def test_dashboard_serves_only_allowlisted_files_on_loopback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root/"report.html").write_text("safe-report")
            (root/"private.json").write_text("private")
            server = ThreadingHTTPServer(("127.0.0.1", 0), runner.handler_for(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:"+str(server.server_port)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(base+"/report.html", timeout=5) as response:
                    self.assertEqual(response.read(), b"safe-report")
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                for route in ("/private.json", "/../private.json", "/missing"):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        opener.open(base+route, timeout=5)
                    self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_full_mode_requires_credential_before_any_collection(self):
        with mock.patch.dict(runner.os.environ, {}, clear=True), mock.patch.object(runner, "run_cycle") as collect:
            with self.assertRaisesRegex(SystemExit, "requires an administrator credential"):
                runner.main(["--mode", "full", "--once"])
            collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
