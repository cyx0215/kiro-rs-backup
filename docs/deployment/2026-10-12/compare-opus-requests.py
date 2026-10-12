"""Prepare four synthetic Opus comparisons; no inference without --execute.

Run on the 8993 host after explicit approval. Credentials stay in server memory.
This does not change configuration. The gateway may internally retry each call.
"""

import argparse
import copy
import datetime as dt
import json
import pathlib
import time
import urllib.error
import urllib.request


def cases():
    base = {
        "model": "claude-opus-5-5",
        "max_tokens": 1024,
        "stream": True,
        "messages": [{"role": "user", "content": "Reply with exactly: OK. Do not call tools."}],
    }
    for name, thinking, tool in (
        ("plain", False, False),
        ("adaptive_high", True, False),
        ("one_tool", False, True),
        ("adaptive_high_and_tool", True, True),
    ):
        payload = copy.deepcopy(base)
        if thinking:
            payload["thinking"] = {"type": "adaptive"}
            payload["output_config"] = {"effort": "high"}
        if tool:
            payload["tools"] = [{
                "name": "diagnostic_echo",
                "description": "Synthetic diagnostic tool. Do not invoke it.",
                "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            }]
        yield name, payload


def classify(value):
    encoded = json.dumps(value)
    return [item for item in (
        "MODEL_TEMPORARILY_UNAVAILABLE", "USER_REQUEST_RATE_EXCEEDED",
        "CONTENT_LENGTH_EXCEEDS_THRESHOLD", "Invalid additionalModelRequestFields",
    ) if item in encoded]


def call(opener, key, name, payload):
    started = time.monotonic()
    result = {"case": name, "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    request = urllib.request.Request(
        "http://127.0.0.1:8993/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"x-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=90) as response:
            result["http_status"] = response.status
            result["headers_ms"] = round((time.monotonic() - started) * 1000)
            result["event_counts"] = {}
            for line in response:
                if time.monotonic() - started > 120:
                    result["stopped_at_time_limit"] = True
                    break
                if not line.startswith(b"data: "):
                    continue
                data = json.loads(line[6:])
                kind = data.get("type", "unknown")
                allowed = {"message_start", "content_block_start", "content_block_delta",
                           "content_block_stop", "message_delta", "message_stop", "ping", "error"}
                kind = kind if kind in allowed else "unknown"
                counts = result["event_counts"]
                counts[kind] = counts.get(kind, 0) + 1
                if kind == "content_block_delta" and data.get("delta", {}).get("type") == "text_delta":
                    result.setdefault("first_text_ms", round((time.monotonic() - started) * 1000))
                if kind == "message_delta":
                    usage = data.get("usage", {})
                    result["usage"] = {key: usage[key] for key in (
                        "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                    ) if isinstance(usage.get(key), (int, float))}
                if kind == "error":
                    result["error_categories"] = classify(data)
            result["completed"] = bool(result["event_counts"].get("message_stop")) and not result["event_counts"].get("error")
    except urllib.error.HTTPError as error:
        result["http_status"] = error.code
        result["error_categories"] = classify(error.read(16384).decode("utf-8", errors="replace"))
    except (OSError, ValueError) as error:
        result["error_class"] = type(error).__name__
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"dry_run": True, "requests": [
            {"case": name, "payload": payload} for name, payload in cases()
        ]}, indent=2))
        return

    root = pathlib.Path("/home/deploy/kiro-billing-test-20261010/data")
    config = json.loads((root / "config.json").read_text())
    credentials = json.loads((root / "credentials.json").read_text())
    enabled = [item for item in credentials if not item.get("disabled", False)]
    assert config["loadBalancingMode"] == "priority"
    assert min(enabled, key=lambda item: (item.get("priority", 0), item["id"]))["id"] == 5
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for name, payload in cases():
        result = call(opener, config["apiKey"], name, payload)
        print(json.dumps(result), flush=True)
        if (name == "plain" and not result.get("completed")) or result.get("http_status") == 429:
            print(json.dumps({"stopped": "baseline_failed_or_rate_limited"}))
            break
        if result.get("error_class") or result.get("stopped_at_time_limit"):
            break


if __name__ == "__main__":
    main()
