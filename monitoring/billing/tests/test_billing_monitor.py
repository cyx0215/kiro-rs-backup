"""Billing-monitor regression tests; synthetic data, no network or credentials."""

import contextlib
import datetime as dt
import io
import json
import pathlib
import runpy
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from billing_monitor import cli as m


SINCE = m.timestamp("2026-10-10T23:00:00+08:00")
UNTIL = SINCE + dt.timedelta(minutes=30)


def raw_usage(identifier=1, **overrides):
    row = dict(id=identifier, account_id=94, group_id=7, model="claude-opus-5-5",
               created_at="2026-10-10T23:10:00+08:00", input_tokens=0, output_tokens=100,
               cache_creation_tokens=2000, cache_read_tokens=10000,
               input_cost="0", output_cost="0.002", cache_creation_cost="0.01",
               cache_read_cost="0.002", total_cost="0.014", actual_cost="0.02352",
               rate_multiplier="1.68", account_rate_multiplier=None, billing_mode="token")
    row.update(overrides)
    return row


def usage(identifier=1, **overrides):
    return m.normalize_usage([raw_usage(identifier, **overrides)], 94, SINCE, UNTIL)[0]


def trace(ref="a" * 16, **overrides):
    row = dict(ref=ref, model="claude-opus-5-5", start=SINCE + dt.timedelta(minutes=9),
               finish=SINCE + dt.timedelta(minutes=10), status="success", credits=m.money("0.2"),
               usage_source="simulated", error_type=None, attempt_http_statuses=[200],
               input_tokens=0, output_tokens=100, cache_creation_tokens=2000, cache_read_tokens=10000)
    row.update(overrides)
    return row


def args(**overrides):
    value = m.parser().parse_args([])
    for key, val in overrides.items():
        setattr(value, key, val)
    return value


def report(traces=None, rows=None, **overrides):
    source = dict(traces_truncated=False, diagnostics=[])
    source.update(overrides.pop("source", {}))
    options = overrides.pop("options", args(traces_json="offline"))
    return m.build_report(options, SINCE, UNTIL, traces or [], source, rows,
                          overrides.pop("complete", True), overrides.pop("errors", {}), [])


class FormulaTests(unittest.TestCase):
    def test_costs_and_multipliers_are_separate(self):
        row = usage(account_rate_multiplier="2", account_stats_cost="0.005")
        self.assertEqual(m.account_cost(row), m.money("0.01"))
        issues, rates, pricing = m.formula_checks(row, m.money("0.000001"), [])
        self.assertEqual(issues, [])
        self.assertEqual(rates["cache_creation_tokens"], "5.00000000")
        self.assertEqual(pricing, "derived_rates_only")
        self.assertIsNone(rates["input_tokens"])

    def test_final_zero_residual_input(self):
        pairs, _ = m.match_rows([trace()], [usage(input_tokens=36538)], 10)
        self.assertEqual(len(pairs), 1)
        self.assertTrue(pairs[0]["token_mismatch"])
        self.assertIn("inferred_three_tokens", pairs[0]["confidence"])

    def test_rounding_and_real_formula_errors(self):
        self.assertEqual(m.formula_checks(usage(actual_cost="0.0235204"), m.money("0.000001"), [])[0], [])
        issues, _, _ = m.formula_checks(usage(total_cost="0.114", actual_cost="0.02352"), m.money("0.000001"), [])
        self.assertEqual(set(issues), {"component_sum", "user_multiplier"})

    def test_external_prices_detect_internally_consistent_wrong_cost(self):
        rules = [dict(model="claude-opus-5-5", group_id=7, **{
            "from": SINCE.isoformat(), "until": UNTIL.isoformat(),
            "usd_per_million": dict(zip(m.TOKEN_FIELDS, [4, 20, 6, "0.2"]))})]
        issues, _, pricing = m.formula_checks(usage(), m.money("0.000001"), rules)
        self.assertEqual(issues, ["unit_price_cache_creation_tokens"])
        self.assertEqual(pricing, "checked_against_supplied_prices")

    def test_non_token_billing_is_not_false_alarm(self):
        issues, rates, pricing = m.formula_checks(usage(billing_mode="per_request", total_cost="8"), m.money("0.000001"), [])
        self.assertEqual(issues, [])
        self.assertIsNone(rates)
        self.assertEqual(pricing, "unsupported_billing_mode")

    def test_explicit_image_and_search_extras_are_skipped_but_unknown_differences_alert(self):
        for field in ("image_input_tokens", "search_count"):
            with self.subTest(field=field):
                issues, _, pricing = m.formula_checks(usage(**{field: 1, "total_cost": "8"}), m.money("0.000001"), [])
                self.assertEqual(issues, [])
                self.assertEqual(pricing, "unsupported_billing_mode")
        issues, _, _ = m.formula_checks(usage(unknown_extra="1", total_cost="8"), m.money("0.000001"), [])
        self.assertIn("component_sum", issues)

    def test_bad_data_cannot_silently_become_zero(self):
        for value in ("NaN", "Infinity", "-1", "0.1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.normalize_usage([raw_usage(input_tokens=value)], 94, SINCE, UNTIL)
        with self.assertRaises(ValueError):
            m.normalize_usage([raw_usage(account_id=93)], 94, SINCE, UNTIL)
        raw = raw_usage()
        del raw["input_tokens"]
        with self.assertRaises(KeyError):
            m.normalize_usage([raw], 94, SINCE, UNTIL)


class PairingTests(unittest.TestCase):
    def test_exact_pair_does_not_make_concurrent_fallback_falsely_unique(self):
        pairs, ambiguous = m.match_rows(
            [trace(input_tokens=1), trace("b" * 16, input_tokens=2)],
            [usage(input_tokens=1), usage(2, input_tokens=9)], 10)
        self.assertEqual([pair["sub2api_id"] for pair in pairs], [1])
        self.assertFalse(pairs[0]["token_mismatch"])
        self.assertEqual(ambiguous, 1)

    def test_concurrent_identical_calls_stay_ambiguous(self):
        pairs, ambiguous = m.match_rows([trace(), trace("b" * 16)], [usage(), usage(2)], 10)
        self.assertEqual(pairs, [])
        self.assertEqual(ambiguous, 2)

    def test_exact_input_disambiguates_other_three_tokens(self):
        pairs, ambiguous = m.match_rows([trace(), trace("b" * 16, input_tokens=2)], [usage(), usage(2, input_tokens=2)], 10)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(ambiguous, 0)
        self.assertFalse(any(p["token_mismatch"] for p in pairs))

    def test_failed_or_different_model_or_late_call_is_not_paired(self):
        for changes in (dict(status="error"), dict(model="claude-sonnet-5"), dict(finish=UNTIL)):
            with self.subTest(changes=changes):
                self.assertEqual(m.match_rows([trace(**changes)], [usage()], 10)[0], [])

    def test_upstream_model_mapping(self):
        pairs, _ = m.match_rows([trace()], [usage(model="friendly-alias", upstream_model="claude-opus-5-5")], 10)
        self.assertEqual(len(pairs), 1)

    def test_nanoseconds_and_end_boundary(self):
        for fraction in ("1", "12", "1234", "12345", "123456", "123456789"):
            self.assertEqual(m.timestamp("2026-10-10T15:00:00." + fraction + "Z").microsecond,
                             int(fraction[:6].ljust(6, "0")))
        self.assertEqual(m.normalize_usage([raw_usage(created_at=UNTIL.isoformat())], 94, SINCE, UNTIL), [])


class PaginationTests(unittest.TestCase):
    def page(self, items, page, total, pages):
        return dict(code=0, data=dict(items=items, page=page, total=total, pages=pages))

    def test_all_pages_are_collected(self):
        pages = [self.page([raw_usage(1)], 1, 2, 2), self.page([raw_usage(2)], 2, 2, 2)]
        with mock.patch.object(m, "usage_page", side_effect=pages):
            rows, complete = m.fetch_usage(args(), SINCE, UNTIL)
        self.assertEqual([row["id"] for row in rows], [1, 2])
        self.assertTrue(complete)

    def test_duplicate_pages_and_changed_totals_are_incomplete(self):
        for second in (self.page([raw_usage(1)], 2, 2, 2), self.page([raw_usage(2)], 2, 3, 2)):
            with mock.patch.object(m, "usage_page", side_effect=[self.page([raw_usage(1)], 1, 2, 2), second]):
                _, complete = m.fetch_usage(args(), SINCE, UNTIL)
                self.assertFalse(complete)

    def test_hard_limit_marks_partial(self):
        with mock.patch.object(m, "usage_page", return_value=self.page([raw_usage()], 1, 2, 2)):
            rows, complete = m.fetch_usage(args(max_rows=1), SINCE, UNTIL)
        self.assertEqual(len(rows), 1)
        self.assertFalse(complete)

    def test_offline_subset_is_not_complete_by_default(self):
        self.assertFalse(m.unwrap_usage([raw_usage()])[1])
        self.assertFalse(m.unwrap_usage({"items": [raw_usage()], "total": 10})[1])
        self.assertFalse(m.unwrap_usage({"items": [raw_usage()], "total": 1, "complete": False})[1])

    def test_read_only_api_request_and_auth_stays_in_header(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(self.page([], 1, 0, 1)).encode()
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.dict(m.os.environ, {"SUB2API_ADMIN_TOKEN": "synthetic-test-token"}):
            m.usage_page(args(), SINCE, UNTIL, 1, 200, opener)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("account_id=94", request.full_url)
        self.assertIn("exact_total=true", request.full_url)
        self.assertIn("sort_order=desc", request.full_url)
        self.assertNotIn("synthetic-test-token", request.full_url)
        with self.assertRaises(RuntimeError):
            m.NoRedirect().redirect_request(request, None, 302, "", {}, "https://elsewhere.invalid")

    def test_recent_page_is_kept_at_cap_and_timestamp_does_not_stop_pagination(self):
        pages = [self.page([raw_usage(99)], 1, 2, 2), self.page([raw_usage(98)], 2, 2, 2)]
        with mock.patch.object(m, "usage_page", side_effect=pages):
            rows, complete = m.fetch_usage(args(max_rows=1), SINCE, UNTIL)
        self.assertEqual([row["id"] for row in rows], [99])
        self.assertFalse(complete)
        # Higher IDs can have older timestamps: reading the next page is required.
        pages[0] = self.page([raw_usage(99, created_at=(SINCE-dt.timedelta(hours=1)).isoformat())], 1, 2, 2)
        with mock.patch.object(m, "usage_page", side_effect=pages) as fetch:
            rows, complete = m.fetch_usage(args(), SINCE, UNTIL)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual([row["id"] for row in rows], [98])
        self.assertTrue(complete)


class AlertTests(unittest.TestCase):
    def test_ambiguous_bills_are_reviewed_without_claiming_routing_failure(self):
        value = report([trace(str(i)*16) for i in range(3)], [usage(i) for i in range(3)],
                       options=args(expect_exclusive_account=True, traces_json="offline"))
        self.assertEqual(value["reconciliation"]["ambiguous_usage_rows"], 3)
        self.assertEqual({item["kind"] for item in value["alerts"]}, {"reconciliation"})

    def test_output_and_cache_differences_require_review_only_with_complete_mature_data(self):
        for field in m.TOKEN_FIELDS[1:]:
            with self.subTest(field=field):
                changes = dict(output_tokens=101)
                changes[field] = 999
                traces = [trace(), trace("b"*16, **changes)]
                rows = [usage(), usage(2, output_tokens=101)]
                value = report(traces, rows)
                self.assertEqual(value["reconciliation"]["unique_pairs"], 1)
                self.assertEqual({item["kind"] for item in value["alerts"]}, {"reconciliation"})
                self.assertNotIn("reconciliation", {item["kind"] for item in report(traces, rows, complete=False)["alerts"]})
                self.assertNotIn("reconciliation", {item["kind"] for item in report(traces, rows, source={"traces_truncated": True})["alerts"]})
        self.assertEqual(report([trace(finish=UNTIL-dt.timedelta(seconds=30))], [usage(output_tokens=999)])["alerts"], [])
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory)/"state.json"
            for count in (1, 0):
                value = report([trace(output_tokens=999)], [usage()])
                m.update_state(value, path, "scope")
                self.assertEqual(len(value["new_alerts"]), count)

    def test_changed_source_failure_notifies_and_unchanged_reason_is_quiet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory)/"state.json"
            for reason, count in (("HTTP 401", 1), ("HTTP 401", 0), ("Connection failed", 1), ("Connection failed", 0)):
                value = report([], None, errors={"sub2api": reason})
                m.update_state(value, path, "scope")
                self.assertEqual(len(value["new_alerts"]), count)
            m.update_state(report([], []), path, "scope")
            value = report([], None, errors={"sub2api": "Connection failed"})
            m.update_state(value, path, "scope")
            self.assertEqual(len(value["new_alerts"]), 1)

    def test_first_observation_and_high_write_are_not_errors(self):
        value = report([trace(cache_creation_tokens=600000)], [], source={"diagnostics": [{"reason": "first_observation"}]})
        self.assertEqual(value["alerts"], [])

    def test_large_simulated_cache_write_is_review_signal(self):
        item = dict(kind="usage", time=SINCE.isoformat(), context_tokens=208465,
                    prompt_estimate=1900000, provider_usage=False)
        self.assertTrue(m.cache_write_anomaly(item, SINCE))
        value = report([], [], source={"diagnostics": [item]})
        self.assertEqual({alert["kind"] for alert in value["alerts"]}, {"cache"})

    def test_normal_cache_write_and_first_observation_are_not_alerts(self):
        item = dict(kind="usage", time=SINCE.isoformat(), context_tokens=240000,
                    prompt_estimate=241000, provider_usage=False)
        self.assertFalse(m.cache_write_anomaly(item, SINCE))

    def test_estimate_review_invalid_missing_provider_and_window(self):
        item = dict(kind="usage", time=SINCE.isoformat(), context_tokens="239127.0",
                    prompt_estimate=2130072, provider_usage=False)
        self.assertTrue(m.cache_write_anomaly(item, SINCE))
        for overrides in ({"context_tokens": 0}, {"context_tokens": "NaN"},
                          {"context_tokens": None}, {"provider_usage": True},
                          {"provider_usage": None}, {"kind": "cache"},
                          {"time": "bad"}, {"prompt_estimate": 99999}):
            self.assertFalse(m.cache_write_anomaly(dict(item, **overrides), SINCE))
        value = report([], [], source={"diagnostics": [dict(item, time=UNTIL.isoformat())]})
        self.assertEqual(value["alerts"], [])
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "state.json"
            for expected in (1, 0):
                value = report([], [], source={"diagnostics": [item]})
                m.update_state(value, path, "scope")
                self.assertEqual(len(value["new_alerts"]), expected)

    def test_role_change_does_not_look_like_text_normalization_regression(self):
        item = dict(reason="previous_prefix_changed", normalized_equal=True, text_equal=True,
                    previous_kind="text", current_kind="string", previous_role="user", current_role="assistant",
                    common_blocks=100, previous_deepest=100, time=SINCE.isoformat())
        self.assertFalse(m.cache_regression(item, SINCE))
        item["current_role"] = "user"
        self.assertTrue(m.cache_regression(item, SINCE))
        item["common_blocks"] = 99
        self.assertFalse(m.cache_regression(item, SINCE))

    def test_sustained_failures_429_and_no_account(self):
        rows = [trace(str(i) * 16, status="error", finish=UNTIL-dt.timedelta(seconds=i),
                      error_type="no_available_credentials", attempt_http_statuses=[429]) for i in range(1, 4)]
        kinds = {item["kind"] for item in report(rows, [])["alerts"]}
        self.assertEqual(kinds, {"failures", "rate_limit", "no_account"})

    def test_missing_attempt_error_type_uses_sanitized_no_account_classification(self):
        value = report([trace(status="error", error_type=None, no_schedulable_credentials=True,
                              finish=UNTIL-dt.timedelta(seconds=1))], [])
        self.assertIn("no_account", [item["kind"] for item in value["alerts"]])

    def test_route_alert_requires_explicit_exclusive_scope_and_complete_data(self):
        rows = [trace(str(i) * 16) for i in range(3)]
        self.assertEqual(report(rows, [])["alerts"], [])
        options = args(expect_exclusive_account=True, traces_json="offline")
        self.assertIn("route", [item["kind"] for item in report(rows, [], options=options)["alerts"]])
        self.assertNotIn("route", [item["kind"] for item in report(rows, [], options=options, complete=False)["alerts"]])

    def test_dedup_persists_events_and_rearms_recovered_conditions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "state.json"
            value = report([], [], errors={"sub2api": "offline"})
            m.update_state(value, path, "scope")
            self.assertEqual(len(value["new_alerts"]), 1)
            value = report([], [], errors={"sub2api": "offline"})
            m.update_state(value, path, "scope")
            self.assertEqual(value["new_alerts"], [])
            m.update_state(report([], []), path, "scope")
            value = report([], [], errors={"sub2api": "offline"})
            m.update_state(value, path, "scope")
            self.assertEqual(len(value["new_alerts"]), 1)
            self.assertNotIn("offline", path.read_text())
            with self.assertRaises(ValueError):
                m.update_state(report([], []), path, "different")

    def test_formula_event_is_not_repeated_after_leaving_window(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "state.json"
            first = report([], [usage(actual_cost="1")])
            m.update_state(first, path, "scope")
            self.assertEqual(len(first["new_alerts"]), 1)
            m.update_state(report([], []), path, "scope")
            again = report([], [usage(actual_cost="1")])
            m.update_state(again, path, "scope")
            self.assertEqual(again["new_alerts"], [])


class CollectorTests(unittest.TestCase):
    def empty_db(self, root):
        path = root/"traces.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE traces (trace_id TEXT, ts TEXT, ts_epoch INTEGER, model TEXT, final_status TEXT, duration_ms INTEGER, error_type TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_creation_tokens INTEGER, cache_read_tokens INTEGER, credits REAL, usage_source TEXT, session_id TEXT, error_message TEXT)")
            db.execute("CREATE TABLE trace_attempts (trace_id TEXT, http_status INTEGER)")
        return dict(since=SINCE.isoformat(), until=UNTIL.isoformat(), db_path=str(path), max_rows=10, container="test")

    def test_diagnostic_truncation_limits_are_explicit_and_return_partial_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            params = self.empty_db(pathlib.Path(directory))
            diagnostic = "2026-10-10T15:00:02Z INFO billing_cache_diagnostic reason=first_observation secret=private-body"
            for lines, expected, kept in (([diagnostic]*50, False, 50), ([diagnostic]*51, True, 50),
                                          (["private-log"]*5000, True, 0)):
                with self.subTest(lines=len(lines)):
                    output = io.StringIO()
                    with mock.patch("subprocess.run", side_effect=[
                            subprocess.CompletedProcess([], 0, m.EXPECTED_IMAGE+"|running|0", ""),
                            subprocess.CompletedProcess([], 0, "\n".join(lines), "")]), contextlib.redirect_stdout(output):
                        runpy.run_path(str(m.HERE/"collector.py"), init_globals={"PARAMS": params})
                    value = json.loads(output.getvalue())
                    self.assertEqual(value["diagnostics_truncated"], expected)
                    self.assertEqual(len(value["diagnostics"]), kept)
                    self.assertNotIn("private-body", output.getvalue())
                    self.assertNotIn("private-log", output.getvalue())
                    with mock.patch.object(m, "collect_traces", return_value=([], value)), \
                            mock.patch.object(m, "fetch_usage", return_value=([], True)), contextlib.redirect_stdout(io.StringIO()):
                        code = m.main(["--since", SINCE.isoformat(), "--until", UNTIL.isoformat(), "--no-state", "--quiet"])
                    self.assertEqual(code, 1 if expected else 0)

    def test_collector_compatibility_emits_standalone_source_and_local_run_agrees(self):
        wrapper = m.HERE.parents[3]/"docs"/"deployment"/"2026-10-10"/"billing_monitor_collect.py"
        with tempfile.TemporaryDirectory() as directory:
            params = self.empty_db(pathlib.Path(directory))
            emitted = subprocess.run([sys.executable, "-B", str(wrapper), "--emit-source"],
                                     input=json.dumps(params), capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(emitted.returncode, 0, emitted.stderr)
            results = []
            for standalone in (True, False):
                output = io.StringIO()
                with mock.patch("subprocess.run", side_effect=[
                        subprocess.CompletedProcess([], 0, m.EXPECTED_IMAGE+"|running|0", ""),
                        subprocess.CompletedProcess([], 0, "", "")]), \
                        mock.patch.object(sys, "argv", [str(wrapper)]), contextlib.redirect_stdout(output):
                    if standalone:
                        exec(compile(emitted.stdout, "<stdin>", "exec"), {})
                    else:
                        runpy.run_path(str(wrapper), init_globals={"PARAMS": params})
                value = json.loads(output.getvalue())
                value.pop("observed_at")
                results.append(value)
            self.assertEqual(results[0], results[1])
            with self.assertRaisesRegex(SystemExit, "--emit-source"):
                exec(compile(wrapper.read_text(encoding="utf-8"), "<stdin>", "exec"), {})

    def test_ssh_child_does_not_inherit_admin_credentials(self):
        environment = {"CUSTOM_ADMIN": "synthetic-secret", "sub2api_admin_token": "synthetic-jwt",
                       "SUB2API_ADMIN_API_KEY": "synthetic-key", "PATH": "safe-path", "SSH_AUTH_SOCK": "safe-agent"}
        with mock.patch.dict(m.os.environ, environment, clear=True), mock.patch.object(m.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, '{"traces":[]}', "")) as run:
            m.collect_traces(args(token_env="CUSTOM_ADMIN"), SINCE, UNTIL)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["env"], {"PATH": "safe-path", "SSH_AUTH_SOCK": "safe-agent"})
        self.assertNotIn("synthetic-secret", kwargs["input"])

    def test_invalid_state_preserves_reports_and_original_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for content in ('bad-json', '[]', '{"version":1}', '{"version":2,"scope":"wrong"}'):
                with self.subTest(content=content):
                    state = root/"state.json"
                    state.write_text(content)
                    output, errors = io.StringIO(), io.StringIO()
                    with mock.patch.object(m, "collect_traces", return_value=([], {})), \
                            mock.patch.object(m, "fetch_usage", return_value=([], True)), \
                            contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        code = m.main(["--since", SINCE.isoformat(), "--until", UNTIL.isoformat(),
                                       "--traces-json", "offline", "--state", str(state), "--quiet",
                                       "--output", str(root/"report.json"), "--html", str(root/"report.html")])
                    self.assertEqual(code, 1)
                    self.assertEqual(state.read_text(), content)
                    value = json.loads((root/"report.json").read_text(encoding="utf-8"))
                    self.assertEqual(value["sub2api"]["usage_count"], 0)
                    self.assertIn("state", value["new_alerts"][0]["detail"])
                    self.assertIn("report preserved", errors.getvalue())
                    self.assertIn("new_alerts", output.getvalue())
                    self.assertIn("数据不完整", (root/"report.html").read_text(encoding="utf-8"))
            for state in ({"version":2,"scope":"scope"}, {"version":2,"scope":"scope","event_hashes":[{}],"active_hashes":[]}):
                (root/"state.json").write_text(json.dumps(state))
                with self.assertRaises(ValueError):
                    m.update_state(report([], []), root/"state.json", "scope")

    def test_compatibility_named_test_entry_point(self):
        wrapper = m.HERE.parents[3]/"docs"/"deployment"/"2026-10-10"/"test_billing_monitor.py"
        result = subprocess.run([sys.executable, "-B", str(wrapper), "FormulaTests.test_final_zero_residual_input"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 1 test", result.stderr)

    def test_cli_source_failure_preserves_report_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            command = ["--since", SINCE.isoformat(), "--until", UNTIL.isoformat(),
                       "--state", str(root / "state.json"), "--output", str(root / "report.json"), "--quiet"]
            with mock.patch.object(m, "collect_traces", return_value=([], {"traces_truncated": False,
                    "container": {"image": m.EXPECTED_IMAGE, "status": "running", "restarts": 0}})), \
                    mock.patch.object(m, "fetch_usage", side_effect=RuntimeError("Sub2API usage API returned HTTP 401")):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(m.main(command), 1)
                first = json.loads((root / "report.json").read_text(encoding="utf-8"))
                self.assertFalse(first["sub2api"]["available"])
                self.assertIsNone(first["sub2api"]["user_usd"])
                self.assertEqual(len(first["new_alerts"]), 1)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(m.main(command), 1)
                self.assertEqual(output.getvalue(), "")

    def test_cross_window_completion_and_sensitive_fields_are_not_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = pathlib.Path(directory) / "traces.db"
            with sqlite3.connect(db_path) as db:
                db.execute("CREATE TABLE traces (trace_id TEXT, ts TEXT, ts_epoch INTEGER, model TEXT, final_status TEXT, duration_ms INTEGER, error_type TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_creation_tokens INTEGER, cache_read_tokens INTEGER, credits REAL, usage_source TEXT, session_id TEXT, secret TEXT)")
                db.execute("CREATE TABLE trace_attempts (trace_id TEXT, http_status INTEGER, secret TEXT)")
                before = SINCE - dt.timedelta(seconds=2)
                db.execute("INSERT INTO traces VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    "private-trace-id", before.isoformat(), int(before.timestamp()), "claude-opus-5-5", "success", 5000,
                    None, 0, 100, 2000, 10000, 0.2, "simulated", "private-session", "secret-body"))
                db.execute("INSERT INTO trace_attempts VALUES (?,?,?)", ("private-trace-id", 429, "secret-attempt"))
                db.execute("ALTER TABLE traces ADD COLUMN error_message TEXT")
                db.execute("UPDATE traces SET error_message = ?", ("所有凭据均已禁用（0/2） secret-body",))
            db.close()
            log = '\x1b[32m2026-10-10T15:00:02.123456789Z\x1b[0m INFO billing_cache_diagnostic reason="previous_prefix_changed" previous_age_secs=Some(10) normalized_equal=Some(true) text_equal=Some(true) previous_role="user" current_role="user" previous_kind="text" current_kind="string" common_blocks=100 previous_deepest=Some(100)\nsecret-log-line'
            log += '\n2026-10-10T15:00:03Z INFO billing_usage_diagnostic provider_usage=false context_tokens=Some(239127) prompt_estimate=2130072 cache_write=68 secret="private-body"'
            inspect = subprocess.CompletedProcess([], 0, m.EXPECTED_IMAGE + "|running|0", "")
            logs = subprocess.CompletedProcess([], 0, log, "")
            output = io.StringIO()
            with mock.patch("subprocess.run", side_effect=[inspect, logs]), contextlib.redirect_stdout(output):
                runpy.run_path(str(m.HERE / "collector.py"), init_globals={"PARAMS": {
                    "since": SINCE.isoformat(), "until": UNTIL.isoformat(), "db_path": str(db_path), "max_rows": 10, "container": "test"}})
            encoded = output.getvalue()
            for private in ("private-trace-id", "private-session", "secret-body", "secret-attempt", "secret-log-line"):
                self.assertNotIn(private, encoded)
            collected = json.loads(encoded)
            self.assertEqual(collected["traces"][0]["attempt_http_statuses"], [429])
            self.assertEqual(collected["traces"][0]["no_schedulable_credentials"], 1)
            self.assertEqual(collected["diagnostics"][0]["previous_age_secs"], 10)
            self.assertNotIn("private-body", encoded)
            self.assertTrue(m.cache_write_anomaly(collected["diagnostics"][1], SINCE))
            source_path = pathlib.Path(directory) / "traces.json"
            source_path.write_text(encoded, encoding="utf-8")
            rows, _ = m.collect_traces(args(traces_json=str(source_path)), SINCE, UNTIL)
            self.assertEqual(len(rows), 1)

    def test_cli_json_html_and_quiet_mode_without_online_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            raw = raw_usage(model="<script>alert(1)</script>")
            (root / "usage.json").write_text(json.dumps({"items": [raw], "complete": True}))
            (root / "traces.json").write_text(json.dumps({"traces": [], "traces_truncated": False}))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = m.main(["--since", SINCE.isoformat(), "--until", UNTIL.isoformat(),
                               "--traces-json", str(root / "traces.json"), "--sub2api-json", str(root / "usage.json"),
                               "--output", str(root / "report.json"), "--html", str(root / "report.html"), "--no-state", "--quiet"])
            self.assertEqual(code, 0)
            self.assertEqual(output.getvalue(), "")
            markup = (root / "report.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>", markup)
            self.assertIn("&lt;script&gt;", markup)
            self.assertTrue(json.loads((root / "report.json").read_text())["sub2api"]["complete"])


if __name__ == "__main__":
    unittest.main()
