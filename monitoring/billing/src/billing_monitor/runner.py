"""Local, read-only billing monitor loop with an allowlisted localhost dashboard."""

import argparse
import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from billing_monitor import cli


DEFAULT_OUTPUT = pathlib.Path(".local-test/billing-monitor")


def write_status(output_dir, status):
    cli.atomic_write(output_dir / "status.json", json.dumps(status, ensure_ascii=False, indent=2) + "\n")


def run_cycle(output_dir, mode, token_env="SUB2API_ADMIN_TOKEN", auth_type="bearer"):
    """Update sanitized snapshots; never persist the CLI's raw stdout/stderr."""
    argv = ["--lookback-minutes", "30", "--output", str(output_dir / "latest.json"),
            "--html", str(output_dir / "report.html"), "--quiet"]
    if mode == "traces-only":
        argv.append("--traces-only")
    else:
        argv.extend(["--token-env", token_env, "--auth-type", auth_type])
    cycle_started = dt.datetime.now(dt.timezone.utc)
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(argv)
    status = {
        "last_run_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pid": os.getpid(), "mode": mode, "exit_code": code,
        "report_available": (output_dir / "report.html").is_file(),
    }
    if (output_dir / "latest.json").is_file():
        try:
            report = json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))
            status["report_observed_at"] = report.get("observed_at")
            status["report_fresh"] = cli.timestamp(report["observed_at"]) >= cycle_started
            status["new_alert_kinds"] = [item["kind"] for item in report.get("new_alerts", [])]
        except (OSError, ValueError, TypeError, KeyError):
            status["report_available"] = False
    write_status(output_dir, status)
    return status


def handler_for(output_dir):
    """Serve only the three sanitized current snapshots; never list the directory."""
    allowed = {
        "/report.html": ("report.html", "text/html; charset=utf-8"),
        "/latest.json": ("latest.json", "application/json; charset=utf-8"),
        "/status.json": ("status.json", "application/json; charset=utf-8"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(False)

        def do_HEAD(self):
            self.respond(True)

        def respond(self, head_only):
            route = urlsplit(self.path).path
            if route == "/":
                self.send_response(302)
                self.send_header("Location", "/report.html")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if route not in allowed:
                self.send_error(404)
                return
            name, content_type = allowed[route]
            try:
                data = (output_dir / name).read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if not head_only:
                self.wfile.write(data)

        def log_message(self, *_args):
            # HTTP request paths and addresses are not retained in a log.
            pass

    return Handler


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--mode", choices=("traces-only", "full"), default="traces-only")
    result.add_argument("--token-env", default="SUB2API_ADMIN_TOKEN")
    result.add_argument("--auth-type", choices=("bearer", "api-key"), default="bearer")
    result.add_argument("--interval-seconds", type=int, default=900)
    result.add_argument("--port", type=int, default=8994)
    result.add_argument("--output-dir", type=pathlib.Path, default=DEFAULT_OUTPUT)
    result.add_argument("--once", action="store_true", help="Collect once without starting an HTTP server")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.interval_seconds < 60 or not 1 <= args.port <= 65535:
        raise SystemExit("interval must be at least 60 seconds and port must be valid")
    if args.mode == "full" and not os.environ.get(args.token_env):
        raise SystemExit("full mode requires an administrator credential in the configured environment variable")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.once:
        return run_cycle(output_dir, args.mode, args.token_env, args.auth_type)["exit_code"]

    # Bind first: a second process cannot run the same dashboard/collector loop.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(output_dir))
    server.daemon_threads = True
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            try:
                run_cycle(output_dir, args.mode, args.token_env, args.auth_type)
            except Exception:
                # Never persist exception messages from data sources or raw logs.
                write_status(output_dir, {
                    "last_run_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "pid": os.getpid(), "mode": args.mode, "exit_code": 1,
                    "report_available": False, "error": "Monitor cycle failed",
                })
            stop.wait(args.interval_seconds)

    thread = threading.Thread(target=worker, name="billing-monitor-collector", daemon=True)
    thread.start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        server.server_close()
        thread.join(timeout=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
