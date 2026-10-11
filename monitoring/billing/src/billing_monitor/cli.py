"""Read-only billing monitor for the isolated Kiro test instance and Sub2API."""

import argparse
import bisect
import collections
import datetime as dt
import decimal
import hashlib
import html
import getpass
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


HERE = pathlib.Path(__file__).resolve().parent
BEIJING = dt.timezone(dt.timedelta(hours=8))
ZERO = decimal.Decimal(0)
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")
COST_FIELDS = ("input_cost", "output_cost", "cache_creation_cost", "cache_read_cost")
DEFAULT_STATE = pathlib.Path.home() / ".kiro-rs" / "billing-monitor-94-state.json"
EXPECTED_IMAGE = "sha256:ba74e53a06cf097c587f658ae6222047b404f9045f1c9e08f67c55bdbfdc359a"


def timestamp(value):
    # Rust emits nanoseconds; Python 3.9/3.10 accept only microseconds.
    value = re.sub(r"\.(\d+)(?=[+-]\d\d:\d\d$)",
                   lambda match: "." + match[1][:6].ljust(6, "0"), str(value).replace("Z", "+00:00"))
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def money(value):
    result = decimal.Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("monetary values must be finite and non-negative")
    return result


def integer(value):
    result = money(value)
    if result != result.to_integral_value():
        raise ValueError("counts must be non-negative integers")
    return int(result)


def money_text(value):
    return format(value.quantize(decimal.Decimal("0.00000001")), "f")


def json_data(raw):
    return json.loads(raw, parse_float=decimal.Decimal)


def json_file(path):
    return json_data(pathlib.Path(path).read_text(encoding="utf-8-sig"))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def collect_traces(args, since, until):
    if args.traces_json:
        document = json_file(args.traces_json)
        if isinstance(document, list):
            document = {"traces": document, "traces_truncated": True}
    else:
        params = {
            "since": since.isoformat(), "until": until.isoformat(),
            "db_path": args.db_path, "container": args.container, "max_rows": args.max_rows,
        }
        collector = (HERE / "collector.py").read_text(encoding="utf-8")
        source = "PARAMS = " + repr(params) + "\n" + collector
        result = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             args.ssh_host, "python3", "-"], input=source, capture_output=True,
            text=True, timeout=75, encoding="utf-8",
            env={key: value for key, value in os.environ.items()
                 if key.upper() not in {args.token_env.upper(), "SUB2API_ADMIN_TOKEN", "SUB2API_ADMIN_API_KEY"}},
        )
        if result.returncode:
            raise RuntimeError("SSH trace collection failed; check read access and schema")
        document = json_data(result.stdout)
    if not isinstance(document, dict) or not isinstance(document.get("traces"), list):
        raise ValueError("trace source must contain a traces array")
    rows = []
    seen = set()
    for raw in document["traces"]:
        started = timestamp(raw["ts"])
        duration = integer(raw["duration_ms"])
        finish = started + dt.timedelta(milliseconds=duration)
        if not since <= finish < until:
            continue
        ref = str(raw["ref"])
        if not re.fullmatch(r"[a-f0-9]{16,64}", ref) or ref in seen:
            raise ValueError("trace references must be unique sanitized hashes")
        seen.add(ref)
        status = raw["final_status"]
        if status not in ("success", "error", "interrupted"):
            raise ValueError("unsupported trace status")
        row = {
            "ref": ref, "start": started, "finish": finish,
            "model": str(raw["model"]), "status": status,
            "credits": money(raw["credits"] or 0),
            "usage_source": raw.get("usage_source") or "unknown",
            "error_type": raw.get("error_type"),
            "no_schedulable_credentials": bool(raw.get("no_schedulable_credentials")),
            "attempt_http_statuses": [integer(x) for x in raw.get("attempt_http_statuses", [])],
        }
        row.update({field: integer(raw[field]) for field in TOKEN_FIELDS})
        rows.append(row)
    return rows, document


def unwrap_usage(document):
    if isinstance(document, list):
        return document, False
    if not isinstance(document, dict):
        raise ValueError("Sub2API document must be an object or array")
    if "code" in document:
        if document["code"] != 0:
            raise ValueError("Sub2API API returned a non-success code")
        document = document.get("data")
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise ValueError("Sub2API document must contain items")
    items = document["items"]
    complete = (document["complete"] is True if "complete" in document else
                isinstance(document.get("total"), int) and len(items) == document["total"])
    return items, complete


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Sub2API redirected the API request; verify its origin")


def usage_page(args, since, until, page, page_size, opener):
    base = args.sub2api_url.rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query
            or parsed.fragment or parsed.username or parsed.password):
        raise ValueError("Sub2API URL must be an HTTPS origin without credentials")
    token = os.environ.get(args.token_env)
    if not token:
        raise RuntimeError("Sub2API admin credential environment variable is unset")
    query = urllib.parse.urlencode({
        "account_id": args.account_id, "page": page, "page_size": page_size,
        "start_date": since.astimezone(BEIJING).date().isoformat(),
        "end_date": until.astimezone(BEIJING).date().isoformat(),
        "timezone": "Asia/Shanghai", "sort_by": "id", "sort_order": "desc",
        "exact_total": "true",
    })
    headers = {"Accept": "application/json", "User-Agent": "curl/8.0.1"}
    headers["x-api-key" if args.auth_type == "api-key" else "Authorization"] = (
        token if args.auth_type == "api-key" else "Bearer " + token)
    request = urllib.request.Request(base + "/api/v1/admin/usage?" + query, headers=headers)
    try:
        with opener.open(request, timeout=20) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise RuntimeError("Sub2API page exceeds the response size limit")
            return json_data(raw)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Sub2API usage API returned HTTP {error.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("Sub2API connection failed") from None


def normalize_usage(raw_rows, account_id, since, until):
    rows = []
    seen = set()
    for raw in raw_rows:
        if integer(raw["account_id"]) != account_id:
            raise ValueError("Sub2API source contains a different account")
        identifier = integer(raw["id"])
        if identifier in seen:
            raise ValueError("Sub2API source contains duplicate usage IDs")
        seen.add(identifier)
        created = timestamp(raw["created_at"])
        if not since <= created < until:
            continue
        row = {
            "id": identifier, "time": created, "model": str(raw["model"]),
            "upstream_model": str(raw.get("upstream_model") or raw["model"]),
            "group_id": raw.get("group_id"),
            "billing_mode": raw.get("billing_mode"),
            "billing_type": raw.get("billing_type"),
            "non_token_media": bool(raw.get("image_count") or raw.get("media_type")
                                    or raw.get("image_input_cost") or raw.get("image_output_cost")
                                    or raw.get("image_input_tokens") or raw.get("search_count")),
            "account_rate_multiplier": money(raw["account_rate_multiplier"]
                if raw.get("account_rate_multiplier") is not None else 1),
            "rate_multiplier": money(raw["rate_multiplier"]),
            "account_stats_cost": money(raw["account_stats_cost"])
                if raw.get("account_stats_cost") is not None else None,
        }
        row.update({field: integer(raw[field]) for field in TOKEN_FIELDS})
        row.update({field: money(raw[field]) for field in COST_FIELDS + ("total_cost", "actual_cost")})
        rows.append(row)
    return rows


def fetch_usage(args, since, until):
    if args.traces_only:
        return None, False
    if args.sub2api_json:
        raw, complete = unwrap_usage(json_file(args.sub2api_json))
        return normalize_usage(raw, args.account_id, since, until), complete
    opener = urllib.request.build_opener(NoRedirect())
    page_size = min(200, args.max_rows)
    records, seen, initial_total = [], set(), None
    complete = True
    # Prefer recent IDs at the row cap. ID order is not a guarantee of timestamp
    # order, so do not stop early based on created_at. Inserts can shift pages;
    # changed totals or duplicate IDs invalidate completeness.
    for page in range(1, math.ceil(args.max_rows / page_size) + 1):
        document = usage_page(args, since, until, page, page_size, opener)
        items, _ = unwrap_usage(document)
        payload = document["data"]
        total = integer(payload["total"])
        pages = integer(payload["pages"])
        if integer(payload["page"]) != page or pages < page:
            raise ValueError("Sub2API pagination metadata is inconsistent")
        if initial_total is None:
            initial_total = total
        elif total != initial_total:
            complete = False
        ids = [integer(row["id"]) for row in items]
        if len(set(ids)) != len(ids) or seen.intersection(ids):
            complete = False
        if items and not set(ids).difference(seen):
            return records, False
        unique = [row for row in items if integer(row["id"]) not in seen]
        if len(seen) + len(unique) > args.max_rows:
            unique = unique[:args.max_rows - len(seen)]
            complete = False
        seen.update(integer(row["id"]) for row in unique)
        # Only allowlisted fields survive beyond a single HTTP response.
        records.extend(normalize_usage(unique, args.account_id, since, until))
        if page == pages:
            return records, complete and len(seen) == total
        if not items or len(seen) >= args.max_rows:
            break
    return records, False


def account_cost(row):
    base = row["account_stats_cost"]
    return (row["total_cost"] if base is None else base) * row["account_rate_multiplier"]


def formula_checks(row, tolerance, price_rules):
    if row["billing_mode"] not in (None, "", "token") or row["non_token_media"]:
        return [], None, "unsupported_billing_mode"
    issues = []
    parts = sum((row[field] for field in COST_FIELDS), ZERO)
    if abs(parts - row["total_cost"]) > tolerance:
        issues.append("component_sum")
    if abs(row["total_cost"] * row["rate_multiplier"] - row["actual_cost"]) > tolerance:
        issues.append("user_multiplier")
    rates = {}
    for token_field, cost_field in zip(TOKEN_FIELDS, COST_FIELDS):
        count = row[token_field]
        rates[token_field] = money_text(row[cost_field] * 1000000 / count) if count else None
        if not count and row[cost_field] > tolerance:
            issues.append("cost_without_" + token_field)
    rules = [rule for rule in price_rules if rule["model"] == row["model"]
             and rule["group_id"] == row["group_id"]
             and timestamp(rule["from"]) <= row["time"] < timestamp(rule["until"])]
    pricing = "derived_rates_only"
    if len(rules) > 1:
        issues.append("ambiguous_price_rules")
        pricing = "ambiguous_price_rules"
    elif rules:
        pricing = "checked_against_supplied_prices"
        for token_field, cost_field in zip(TOKEN_FIELDS, COST_FIELDS):
            expected = row[token_field] * money(rules[0]["usd_per_million"][token_field]) / 1000000
            if abs(expected - row[cost_field]) > tolerance:
                issues.append("unit_price_" + token_field)
    return issues, rates, pricing


def match_rows(traces, usage, max_delta_seconds):
    pairs, used_traces, used_usage = [], set(), set()
    # Exact four-token matches first; then unique three-token matches can expose
    # the known final-zero input problem without consuming an exact match.
    for fields, confidence in ((TOKEN_FIELDS, "inferred_four_tokens"),
                               (TOKEN_FIELDS[1:], "inferred_three_tokens_input_mismatch")):
        by_usage, by_trace = collections.defaultdict(list), collections.defaultdict(list)
        index = collections.defaultdict(list)
        for ti, trace in enumerate(traces):
            if trace["status"] == "success" and (fields != TOKEN_FIELDS or ti not in used_traces):
                index[(trace["model"], *(trace[field] for field in fields))].append((trace["finish"], ti))
        for candidates in index.values():
            candidates.sort()
        for ui, row in enumerate(usage):
            if ui in used_usage:
                continue
            candidates = index[(row["upstream_model"], *(row[field] for field in fields))]
            earliest = row["time"] - dt.timedelta(seconds=max_delta_seconds)
            latest = row["time"] + dt.timedelta(seconds=max_delta_seconds)
            left = bisect.bisect_left(candidates, (earliest, -1))
            right = bisect.bisect_right(candidates, (latest, len(traces)))
            for finished, ti in candidates[left:right]:
                delta = abs((finished - row["time"]).total_seconds())
                by_usage[ui].append((ti, delta))
                by_trace[ti].append(ui)
        for ui, candidates in by_usage.items():
            if len(candidates) != 1 or len(by_trace[candidates[0][0]]) != 1:
                continue
            ti, delta = candidates[0]
            trace, row = traces[ti], usage[ui]
            if ti in used_traces:
                continue
            used_traces.add(ti)
            used_usage.add(ui)
            pairs.append({
                "sub2api_id": row["id"], "trace_ref": trace["ref"],
                "model": row["model"], "finished_at": trace["finish"].isoformat(),
                "time_delta_seconds": round(delta, 3), "confidence": confidence,
                "trace_tokens": {field: trace[field] for field in TOKEN_FIELDS},
                "sub2api_tokens": {field: row[field] for field in TOKEN_FIELDS},
                "token_mismatch": trace["input_tokens"] != row["input_tokens"],
            })
    ambiguous = len([ui for ui in by_usage if ui not in used_usage])
    return pairs, ambiguous


def cache_regression(item, fix_time):
    return (item.get("reason") == "previous_prefix_changed"
            and item.get("normalized_equal") is True and item.get("text_equal") is True
            and {item.get("previous_kind"), item.get("current_kind")} == {"text", "string"}
            and item.get("previous_role") in ("user", "assistant")
            and item.get("previous_role") == item.get("current_role")
            and item.get("common_blocks") is not None
            and item.get("common_blocks") == item.get("previous_deepest")
            and bool(item.get("time")) and timestamp(item["time"]) >= fix_time)


def cache_write_anomaly(item, fix_time):
    """Review a local estimate discrepancy, not an upstream billing error."""
    if item.get("kind") != "usage" or item.get("provider_usage") is not False:
        return False
    try:
        context = integer(item.get("context_tokens"))
        prompt = integer(item.get("prompt_estimate"))
        observed = timestamp(item["time"])
    except (ValueError, TypeError, KeyError, decimal.DecimalException):
        return False
    return context > 0 and prompt >= 100000 and prompt >= context * 2 and observed >= fix_time


def build_report(args, since, until, traces, source, usage, complete, errors, price_rules):
    alerts = []

    def alert(key, kind, detail, event=False):
        alerts.append({"key": key, "kind": kind, "detail": detail, "event": event})

    for origin, error in errors.items():
        alert("source:" + origin + ":" + digest(error), "source", origin + ": " + error)
    if source.get("traces_truncated"):
        alert("partial:traces", "coverage", "Trace row limit reached; the window is incomplete")
    if usage is not None and not complete:
        alert("partial:sub2api", "coverage", "Sub2API pagination or offline data is incomplete")
    container = source.get("container")
    if container:
        if container["status"] != "running":
            alert("container:stopped", "availability", "Test container is not running")
        if args.expected_image and container["image"] != args.expected_image:
            alert("container:image:" + container["image"], "deployment", "Test image differs from the expected fix")
        if container["restarts"]:
            alert("container:restarts:" + container["image"] + ":" + str(container["restarts"]),
                  "availability", "Test container has restarted; verify the deployment", True)
    elif not args.traces_json and "traces" not in errors:
        alert("container:unavailable", "source", "Container status could not be read")
    if source.get("diagnostics_available") is False:
        alert("diagnostics:unavailable", "source", "Container diagnostics could not be read")
    if source.get("diagnostics_truncated"):
        alert("partial:diagnostics", "coverage", "Diagnostic limits reached; cache checks are incomplete")

    checked = []
    for row in usage or []:
        issues, rates, pricing = formula_checks(row, args.money_tolerance, price_rules)
        for issue in issues:
            alert(f"formula:{row['id']}:{issue}", "formula",
                  f"Sub2API usage {row['id']}: {issue}", True)
        checked.append({
            "id": row["id"], "time": row["time"].isoformat(), "model": row["model"], "group_id": row["group_id"],
            "tokens": {field: row[field] for field in TOKEN_FIELDS},
            "cost_components_usd": {field: money_text(row[field]) for field in COST_FIELDS},
            "derived_usd_per_million": rates, "pricing_check": pricing,
            "total_usd": money_text(row["total_cost"]),
            "user_multiplier": str(row["rate_multiplier"]),
            "account_multiplier": str(row["account_rate_multiplier"]),
            "account_base_source": "account_stats_cost" if row["account_stats_cost"] is not None else "total_cost",
            "user_usd": money_text(row["actual_cost"]), "account_usd": money_text(account_cost(row)),
            "formula_issues": issues,
        })
    pairs, ambiguous = match_rows(traces, usage or [], args.match_seconds)
    for pair in pairs:
        if pair["token_mismatch"]:
            alert(f"tokens:{pair['sub2api_id']}", "tokens",
                  f"Sub2API usage {pair['sub2api_id']}: probable input token mismatch", True)

    recent = sorted((row for row in traces if row["finish"] >= until - dt.timedelta(minutes=15)),
                    key=lambda item: item["finish"], reverse=True)
    if len(recent) >= 3 and all(row["status"] != "success" for row in recent[:3]):
        alert("failures:streak", "failures", "Three most recent requests failed or were interrupted")
    if any(429 in row["attempt_http_statuses"] for row in recent):
        alert("rate_limit:active", "rate_limit", "An upstream attempt returned HTTP 429 in the last 15 minutes")
    if any(row["status"] == "error" and (row.get("no_schedulable_credentials") or any(
           word in str(row["error_type"]).lower() for word in
           ("no_available", "no_eligible", "all_disabled", "no_credentials"))) for row in recent):
        alert("no_account:active", "no_account", "Test server reported no schedulable credential")
    # Other traffic can reach the test server directly. Absence of account usage
    # alone is not proof of misrouting unless exclusivity is explicitly asserted.
    mature = [row for row in traces if row["status"] == "success"
              and row["finish"] < until - dt.timedelta(minutes=2)]
    if (args.expect_exclusive_account and complete and usage is not None and not usage
            and not source.get("traces_truncated") and len(mature) >= 3):
        alert("route:unpaired", "route", "No account usage matched completed test calls; verify routing and data delay")
    if complete and usage is not None and not source.get("traces_truncated") and "traces" not in errors:
        paired_refs = {pair["trace_ref"] for pair in pairs}
        if ambiguous or (len(usage) > len(pairs) and any(row["ref"] not in paired_refs for row in mature)):
            pending_ids = sorted(row["id"] for row in usage if row["id"] not in {pair["sub2api_id"] for pair in pairs})
            alert("reconcile:" + digest(json.dumps(pending_ids)), "reconciliation",
                  "Unpaired or ambiguous records require manual review; no token difference is confirmed", True)
    fix_time = timestamp(args.fix_deployed_at)
    for item in source.get("diagnostics", []):
        if cache_regression(item, fix_time) and since <= timestamp(item["time"]) < until:
            alert("cache:" + item["time"], "cache",
                  "Same-role equivalent text changed cache prefix after the fix; inspect this diagnostic", True)
        if cache_write_anomaly(item, fix_time) and since <= timestamp(item["time"]) < until:
            alert("cache-write:" + item["time"], "cache",
                  "Local prompt estimate is at least twice the context estimate; inspect simulated token attribution", True)

    return {
        "schema_version": 1, "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window_start": since.isoformat(), "window_end": until.isoformat(),
        "window_basis": "[since, until): trace finish time / Sub2API created_at; totals are snapshots, not increments",
        "test_server": {
            "available": "traces" not in errors, "trace_count": len(traces),
            "statuses": dict(collections.Counter(row["status"] for row in traces)),
            "kiro_credits_all_test_traffic": money_text(sum((row["credits"] for row in traces), ZERO)),
            "tokens": {field: sum(row[field] for row in traces) for field in TOKEN_FIELDS},
            "usage_sources": dict(collections.Counter(row["usage_source"] for row in traces)),
            "truncated": bool(source.get("traces_truncated")), "container": container,
            "diagnostic_reason_counts_in_log_tail": source.get("diagnostic_reason_counts_in_log_tail", {}),
            "diagnostics_available": source.get("diagnostics_available"),
            "diagnostics_truncated": bool(source.get("diagnostics_truncated")),
        },
        "sub2api": {
            "account_id": args.account_id, "available": usage is not None, "complete": complete,
            "usage_count": len(usage or []),
            "formula_checked_rows": sum(row["pricing_check"] != "unsupported_billing_mode" for row in checked),
            "independent_price_checked_rows": sum(row["pricing_check"] == "checked_against_supplied_prices" for row in checked),
            "user_usd": money_text(sum((row["actual_cost"] for row in usage), ZERO)) if usage is not None else None,
            "account_usd": money_text(sum((account_cost(row) for row in usage), ZERO)) if usage is not None else None,
            "rows": checked,
        },
        "reconciliation": {
            "unique_pairs": len(pairs), "ambiguous_usage_rows": ambiguous,
            "unpaired_usage_rows": len(usage or []) - len(pairs),
            "unpaired_success_traces": sum(row["status"] == "success" for row in traces) - len(pairs),
            "input_mismatches": sum(pair["token_mismatch"] for pair in pairs), "pairs": pairs,
            "no_shared_request_id": True,
        },
        "alerts": alerts, "source_errors": errors,
        "boundaries": [
            "Credits cover all test-server traces; USD covers only the selected Sub2API account.",
            "Simulated cache usage is not an upstream cache measurement; USD is not Kiro credits.",
            "No shared request ID: pairs are inferred using model, finish time and token counts; ambiguous rows remain unpaired.",
            "Derived unit prices describe recorded costs; only supplied dated price rules independently check unit prices.",
            "Account USD applies its own base cost and multiplier; it is not verified against upstream cash costs.",
            "Diagnostics cover at most 5000 log lines and 50 recent cache events, not every request.",
            "Success covers finalized Kiro traces; Sub2API errors before reaching Kiro and active requests are not observed.",
        ],
    }


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def update_state(report, path, scope):
    state = {"version": 2, "scope": scope, "event_hashes": [], "active_hashes": []}
    if path and path.exists():
        state = json_file(path)
        if not isinstance(state, dict) or state.get("version") != 2 or state.get("scope") != scope:
            raise ValueError("state version/scope differs; choose a new state file")
        for field in ("event_hashes", "active_hashes"):
            if not isinstance(state.get(field), list) or not all(isinstance(item, str) for item in state[field]):
                raise ValueError("invalid state hashes; choose a new state file")
    prior = set(state["event_hashes"]) | set(state["active_hashes"])
    active, new = [], []
    events = dict.fromkeys(state["event_hashes"])
    for item in report["alerts"]:
        hashed = digest(item.pop("key"))
        is_event = item.pop("event")
        if hashed not in prior:
            new.append(dict(item))
        if is_event:
            events[hashed] = None
        else:
            active.append(hashed)
    report["new_alerts"] = new
    state["event_hashes"] = list(events)[-20000:]
    state["active_hashes"] = active
    if path:
        atomic_write(path, json.dumps(state, indent=2) + "\n")


def render_html(report):
    esc = lambda value: html.escape(str(value))
    test, sub, rec = report["test_server"], report["sub2api"], report["reconciliation"]
    partial = (not test["available"] or test["truncated"] or test.get("diagnostics_truncated")
               or not sub["available"] or not sub["complete"]
               or any(item["kind"] == "source" for item in report["alerts"]))
    business_alerts = [item for item in report["alerts"] if item["kind"] not in ("coverage", "source")]
    label = "有待处理告警" if business_alerts else ("数据不完整" if partial else "本次未发现已覆盖规则的异常")
    translations = {
        "Sub2API pagination or offline data is incomplete": "账单仅覆盖部分记录，本次结果不能代表整个时间段。",
        "Trace row limit reached; the window is incomplete": "测试调用达到采集上限，需要缩短时间范围或提高上限。",
        "Test container is not running": "测试服务当前未运行。",
        "Test image differs from the expected fix": "测试服务使用的镜像与预期修复版不同。",
        "Test container has restarted; verify the deployment": "测试服务发生过重启，请检查部署状态。",
        "Container status could not be read": "未能读取测试服务状态。",
        "Container diagnostics could not be read": "未能读取缓存诊断。",
        "Diagnostic limits reached; cache checks are incomplete": "诊断达到截断上限，缓存检查覆盖不完整。",
        "Deduplication state unavailable or incompatible; choose a new state file": "告警去重状态不可用或不兼容，请指定新的状态文件；本次报告已保留。",
        "Unpaired or ambiguous records require manual review; no token difference is confirmed": "存在歧义或未配对记录，需人工核对；尚未确认 token 差异。",
        "Three most recent requests failed or were interrupted": "最近三次调用连续失败或中断。",
        "An upstream attempt returned HTTP 429 in the last 15 minutes": "最近15分钟内出现上游429限流。",
        "Test server reported no schedulable credential": "测试服务报告无可调度的凭据。",
        "No account usage matched completed test calls; verify routing and data delay": "已完成调用未配到该账号账单，请检查路由与落账延迟。",
        "Same-role equivalent text changed cache prefix after the fix; inspect this diagnostic": "检测到同角色同文仍导致缓存前缀失配的线索，需要复查。",
        "Local prompt estimate is at least twice the context estimate; inspect simulated token attribution": "本地输入估算达到上下文估算的两倍以上，需要复核模拟 token 分摊；不等于账单算费错误。",
    }
    alerts = "".join("<li>" + esc(translations.get(item["detail"], item["detail"])) + "</li>"
                     for item in report["alerts"]) or "<li>无异常线索。</li>"
    paired = {pair["sub2api_id"]: pair for pair in rec["pairs"]}
    rows = []
    for row in sorted(sub["rows"], key=lambda row: row["time"], reverse=True):
        pair = paired.get(row["id"])
        status = "输入不符（推断）" if pair and pair["token_mismatch"] else ("四类一致（推断）" if pair else "待配对")
        cells = [row["id"], timestamp(row["time"]).astimezone(BEIJING).strftime("%m-%d %H:%M:%S"),
                 row["model"], *(f"{row['tokens'][field]:,}" for field in TOKEN_FIELDS),
                 row["user_usd"], row["account_usd"], status]
        rows.append("<tr>" + "".join("<td>" + esc(value) + "</td>" for value in cells) + "</tr>")
    start = timestamp(report["window_start"]).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    end = timestamp(report["window_end"]).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    statuses = " · ".join({"success": "成功", "error": "失败", "interrupted": "中断"}.get(key, key)
                           + " " + str(value) for key, value in test["statuses"].items()) or "无已完成调用"
    sub_scope = (f"{sub['usage_count']} 条账单" if sub["complete"] else f"{sub['usage_count']} 条账单样本")
    sub_status = "未接入" if not sub["available"] else ("已读取完整分页" if sub["complete"] else "部分数据")
    return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kiro 测试计费监控</title><style>
body{font:15px/1.6 system-ui,sans-serif;background:#f4f6f9;color:#17243a;margin:0;padding:30px}
main{max-width:1380px;margin:auto}h1{font-size:26px}h2{font-size:19px}.cards{display:flex;gap:14px;flex-wrap:wrap}
.card,section{background:white;border:1px solid #dce3ed;border-radius:10px;padding:18px;margin:14px 0}
.card{flex:1;min-width:165px}.number{font-size:25px;font-weight:700}.muted{color:#59677a}
table{border-collapse:collapse;width:100%;white-space:nowrap}th,td{text-align:right;padding:9px;border-bottom:1px solid #e7ebf1}
th{background:#eef2f8}td:nth-child(3){text-align:left}.scroll{overflow:auto}pre{white-space:pre-wrap;word-break:break-word}
</style><main><h1>Kiro 8993 · 计费监控</h1>""" + f"""
<p>Sub2API 账号 #{esc(sub['account_id'])} · {esc(label)}</p>
<p class="muted">北京时间 {esc(start)} 至 {esc(end)}。按完成时间统计，本页是保存时的快照。</p>
<div class="cards"><div class="card">测试调用<div class="number">{test['trace_count'] if test['available'] else '未接入'}</div>{esc(statuses)}</div>
<div class="card">上游 Kiro credits<div class="number">{esc(test['kiro_credits_all_test_traffic'] if test['available'] else '未接入')}</div>测试服务器全部流量</div>
<div class="card">用户 USD<div class="number">{esc(sub['user_usd'] if sub['available'] else '未接入')}</div>{esc(sub_scope)}</div>
<div class="card">账号 USD<div class="number">{esc(sub['account_usd'] if sub['available'] else '未接入')}</div>{esc(sub_scope)} · 独立账号倍率</div></div>
<section><h2>核对结果</h2><p>推断配对 {rec['unique_pairs']} 条；输入不符 {rec['input_mismatches']} 条；待配对账单 {rec['unpaired_usage_rows']} 条。</p>
<p>测试端：{'已读取' if test['available'] else '不可用'}；Sub2API：{esc(sub_status)}；尚未配对的成功调用 {rec['unpaired_success_traces']} 条。</p><ul>{alerts}</ul></section>
<section><h2>逐条账单</h2><div class="scroll"><table><thead><tr><th>ID</th><th>时间</th><th>模型</th><th>输入</th><th>输出</th><th>缓存写</th><th>缓存读</th><th>用户 USD</th><th>账号 USD</th><th>配对</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="10">暂无可用的 Sub2API 账单数据</td></tr>'}</tbody></table></div></section>
<section><h2>验证边界</h2><p>credits 与 USD 分开统计；模拟缓存不代表上游真实命中。无共享请求 ID，配对是推断。未提供历史价格快照时，仅核对费用分项及倍率，单价由账单反推，不能证明定价配置正确。</p>
<p>不含到达 Kiro 前的错误、正在进行的请求；日志只读取有上限的诊断片段。</p>
<details><summary>查看脱敏明细与验证范围</summary><pre>{esc(json.dumps(report, ensure_ascii=False, indent=2))}</pre></details></section></main></html>"""


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    window = result.add_mutually_exclusive_group()
    window.add_argument("--since", help="Timezone-aware ISO timestamp")
    window.add_argument("--lookback-minutes", type=int, default=30)
    result.add_argument("--until", help="Timezone-aware ISO timestamp; default: now")
    result.add_argument("--account-id", type=int, default=94)
    result.add_argument("--ssh-host", default="racknerd-deploy")
    result.add_argument("--db-path", default="/home/deploy/kiro-billing-test-20261010/data/traces.db")
    result.add_argument("--container", default="kiro-rs-billing-test-20261010")
    result.add_argument("--expected-image", default=EXPECTED_IMAGE)
    result.add_argument("--traces-json", help="Offline sanitized collector JSON")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--sub2api-json", help="Offline usage JSON; keep raw exports outside Git")
    mode.add_argument("--traces-only", action="store_true", help="Explicitly skip Sub2API; partial report")
    result.add_argument("--sub2api-url", default="https://02acc.itssx.com")
    result.add_argument("--token-env", default="SUB2API_ADMIN_TOKEN")
    result.add_argument("--prompt-token", action="store_true", help="Read the credential from a hidden terminal prompt")
    result.add_argument("--auth-type", choices=("bearer", "api-key"), default="bearer")
    result.add_argument("--max-rows", type=int, default=10000)
    result.add_argument("--match-seconds", type=float, default=10)
    result.add_argument("--money-tolerance", type=money, default=decimal.Decimal("0.000001"))
    result.add_argument("--prices-json", help="Optional dated prices keyed by model and group")
    result.add_argument("--expect-exclusive-account", action="store_true")
    result.add_argument("--fix-deployed-at", default="2026-10-10T22:58:45+08:00")
    result.add_argument("--state", type=pathlib.Path, help="Override the source-specific dedup state path")
    result.add_argument("--no-state", action="store_true")
    result.add_argument("--output", type=pathlib.Path, help="Write sanitized JSON report")
    result.add_argument("--html", type=pathlib.Path, help="Write a standalone Chinese HTML report")
    result.add_argument("--quiet", action="store_true", help="Print only new alerts; reports still update")
    return result


def safe_error(error):
    # Never include a raw API body, SSH stderr, file contents, or credential in errors.
    if isinstance(error, RuntimeError):
        return str(error)
    return "Source unavailable or invalid data (" + type(error).__name__ + ")"


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        until = timestamp(args.until) if args.until else dt.datetime.now(dt.timezone.utc)
        since = timestamp(args.since) if args.since else until - dt.timedelta(minutes=args.lookback_minutes)
        timestamp(args.fix_deployed_at)
        if (until <= since or args.lookback_minutes < 1 or not 1 <= args.max_rows <= 100000
                or not math.isfinite(args.match_seconds) or not 0 < args.match_seconds <= 120
                or args.account_id < 1):
            raise ValueError("invalid time window or limit")
        price_rules = json_file(args.prices_json)["rules"] if args.prices_json else []
        # Validate all rules before collecting anything, including currently unused rules.
        for rule in price_rules:
            if timestamp(rule["from"]) >= timestamp(rule["until"]):
                raise ValueError("price rule time range is empty")
            integer(rule["group_id"])
            str(rule["model"])
            for field in TOKEN_FIELDS:
                money(rule["usd_per_million"][field])
        if args.prompt_token and not (args.sub2api_json or args.traces_only):
            os.environ[args.token_env] = getpass.getpass("Sub2API admin credential (hidden): ")
        errors = {}
        try:
            traces, source = collect_traces(args, since, until)
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired, KeyError, TypeError, decimal.DecimalException) as error:
            traces, source = [], {}
            errors["traces"] = safe_error(error)
        try:
            usage, complete = fetch_usage(args, since, until)
        except (ValueError, OSError, RuntimeError, KeyError, TypeError, decimal.DecimalException) as error:
            usage, complete = None, False
            errors["sub2api"] = safe_error(error)
        report = build_report(args, since, until, traces, source, usage, complete, errors, price_rules)
        scope = digest("|".join((str(args.account_id), args.ssh_host, args.container, args.db_path,
                                 args.sub2api_url, str(args.traces_json or "live"),
                                 str(bool(args.traces_only)), str(args.sub2api_json or "live"))))
        state_path = args.state or DEFAULT_STATE.with_name(f"billing-monitor-{args.account_id}-{scope[:12]}-state.json")
        state_failed = False
        try:
            update_state(report, None if args.no_state else state_path, scope)
        except (ValueError, OSError, KeyError, TypeError):
            state_failed = True
            # Rebuild because update_state may already have removed internal keys.
            report = build_report(args, since, until, traces, source, usage, complete, errors, price_rules)
            report["alerts"] = [{key: value for key, value in item.items() if key not in ("key", "event")}
                                for item in report["alerts"]]
            state_alert = {"kind": "source", "detail": "Deduplication state unavailable or incompatible; choose a new state file"}
            report["alerts"].append(state_alert)
            # Deduplication is unavailable: notify this access obstacle explicitly,
            # rather than silently suppressing it in quiet mode.
            report["new_alerts"] = [dict(state_alert)]
            print("billing monitor: Deduplication state unavailable or incompatible; report preserved; choose a new state file", file=sys.stderr)
        encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            atomic_write(args.output, encoded)
        if args.html:
            atomic_write(args.html, render_html(report))
        if not args.quiet:
            print(encoded)
        elif report["new_alerts"]:
            print(json.dumps({"new_alerts": report["new_alerts"]}, ensure_ascii=False))
        incomplete = bool(state_failed or errors or source.get("traces_truncated") or source.get("diagnostics_truncated") or (usage is not None and not complete)
                          or any(item["kind"] == "source" for item in report["alerts"]))
        return 1 if incomplete else (2 if report["new_alerts"] else 0)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, decimal.DecimalException) as error:
        print("billing monitor: " + safe_error(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
