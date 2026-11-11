"""Behavioral tests: every mutation below must prevent a false acceptance."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
FIXTURE = BASE / "tests/fixtures/evidence"
SCRIPT = BASE / "scripts/check_evidence.py"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def checker():
    if not SCRIPT.is_file():
        raise AssertionError("Evidence checker has not been implemented")
    spec = importlib.util.spec_from_file_location("check_evidence", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        shutil.copytree(FIXTURE, self.root)
        self.plan = json.loads((self.root / "plan.json").read_text())
        self.results = json.loads((self.root / "results.json").read_text())

    def bind(self):
        for run in self.results["runs"]:
            run["plan_sha256"] = digest(self.plan)
            run["inputs_sha256"] = digest(self.plan["inputs"])
            run["configuration_sha256"] = digest(self.plan["configuration"])

    def evaluate(self):
        return checker().evaluate(self.plan, self.results, self.root)

    def test_complete_declared_scope_accepts_with_current_files(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "ACCEPTED", result)
        self.assertEqual(result["summary"]["passed_checks"], 4)

    def test_case_tag_without_actual_check_does_not_cover_a_requirement(self):
        self.plan["requirements"].append({"id": "REQ-NEW", "description": "different behavior", "source": "spec section 2", "required": True})
        self.plan["cases"][0]["requirements"].append("REQ-NEW")
        self.bind()
        self.assertEqual(self.evaluate()["status"], "INCOMPLETE")

    def test_nonzero_exit_code_cannot_be_overridden_by_pass(self):
        self.results["runs"][0]["exit_code"] = 1
        self.assertEqual(self.evaluate()["status"], "INCOMPLETE")

    def test_defect_closure_and_waiver_require_evidence(self):
        defect = {"id": "BUG-1", "status": "OPEN", "blocking": True, "description": "receiver isolation bug"}
        self.results["defects"] = [defect]
        self.assertEqual(self.evaluate()["status"], "INCOMPLETE")
        defect["status"] = "FIXED"
        self.assertEqual(self.evaluate()["status"], "INVALID")
        defect["regression_runs"] = ["RUN-1"]
        self.assertEqual(self.evaluate()["status"], "ACCEPTED")
        self.results["runs"][0]["status"] = "FAIL"
        self.assertEqual(self.evaluate()["status"], "INCOMPLETE")
        self.results["runs"][0]["status"] = "PASS"
        defect["status"] = "WAIVED"
        del defect["regression_runs"]
        self.assertEqual(self.evaluate()["status"], "INVALID")
        defect["waiver"] = {"reason": "accepted residual risk", "authorization": "user decision W-1"}
        self.assertEqual(self.evaluate()["status"], "ACCEPTED")

    def test_plan_without_rtl_is_valid_but_not_ready(self):
        self.plan["inputs"] = {"roots": [], "files": []}
        for case in self.plan["cases"]:
            for variant in case["variants"]:
                for cp in variant["checks"]:
                    cp["implementation"] = None
        self.assertEqual(checker().validate_plan(self.plan), [])
        self.bind()
        self.assertEqual(checker().evaluate(self.plan, self.results)["status"], "INCOMPLETE")

    def test_normal_control_also_checks_selected_and_unselected_receivers(self):
        self.plan["cases"][0]["variants"][1]["checks"].pop()
        self.assertTrue(checker().validate_plan(self.plan))

    def test_activation_check_reference_is_validated(self):
        self.plan["cases"][0]["high_risk"]["activation_checks"] = ["MISSING"]
        self.assertTrue(checker().validate_plan(self.plan))

    def test_uncovered_requirement_blocks_acceptance(self):
        self.plan["requirements"].append({"id": "REQ-NEW", "description": "new required behavior", "source": "spec section 2", "required": True})
        self.bind()
        self.assertEqual(self.evaluate()["status"], "INCOMPLETE")

    def test_each_variant_and_independent_check_needs_a_result(self):
        for check_id in ("CP-A", "CP-B", "CP-NORMAL-A", "CP-NORMAL-B"):
            with self.subTest(check=check_id):
                original = copy.deepcopy(self.results)
                self.results["observations"] = [o for o in self.results["observations"] if o["check_id"] != check_id]
                self.results["runs"][0]["checks"] = 3
                self.assertEqual(self.evaluate()["status"], "INCOMPLETE")
                self.results = original

    def test_inactive_or_invalid_run_never_passes(self):
        for patch in ({"checks": 0}, {"status": "SKIP"}, {"status": "BLOCKED"}, {"errors": 1}, {"timeouts": 1}, {"status": "TIMEOUT"}):
            with self.subTest(patch=patch):
                original = copy.deepcopy(self.results)
                self.results["runs"][0].update(patch)
                self.results["summary"] = "ALL PASS"
                self.assertNotEqual(self.evaluate()["status"], "ACCEPTED")
                self.results = original

    def test_blocked_and_unknown_expectation_cannot_pass(self):
        self.results["observations"][0]["status"] = "BLOCKED"
        self.assertEqual(self.evaluate()["status"], "INCOMPLETE")
        self.results["observations"][0]["status"] = "PASS"
        self.plan["cases"][0]["variants"][0]["checks"][0]["expected"] = "UNKNOWN"
        self.bind()
        self.assertEqual(self.evaluate()["status"], "INCOMPLETE")

    def test_unresolved_or_unimplemented_check_is_not_counted_as_effective_pass(self):
        cp = self.plan["cases"][0]["variants"][0]["checks"][0]
        for field in ("expected", "implementation"):
            original = cp[field]
            cp[field] = None
            self.bind()
            result = self.evaluate()
            self.assertEqual(result["status"], "INCOMPLETE")
            self.assertEqual(result["summary"]["passed_checks"], 3)
            cp[field] = original

    def test_changed_source_and_plan_invalidate_results(self):
        (self.root / "inputs/design.sv").write_text("changed source\n")
        self.assertEqual(self.evaluate()["status"], "INVALID")
        shutil.copyfile(FIXTURE / "inputs/design.sv", self.root / "inputs/design.sv")
        self.plan["configuration"]["clock_mhz"] = 200
        self.assertEqual(self.evaluate()["status"], "INVALID")

    def test_manifest_detects_new_source_missing_source_and_log_tampering(self):
        (self.root / "inputs/new.sv").write_text("new source\n")
        self.assertEqual(self.evaluate()["status"], "INVALID")
        (self.root / "inputs/new.sv").unlink()
        (self.root / "logs/example.log").write_text("PASS\n")
        self.assertEqual(self.evaluate()["status"], "INVALID")
        (self.root / "logs/example.log").unlink()
        self.assertEqual(self.evaluate()["status"], "INVALID")
        shutil.copyfile(FIXTURE / "logs/example.log", self.root / "logs/example.log")
        (self.root / "inputs/design.sv").unlink()
        self.assertEqual(self.evaluate()["status"], "INVALID")

    def test_root_required_for_acceptance(self):
        self.assertEqual(checker().evaluate(self.plan, self.results)["status"], "INCOMPLETE")
        self.assertEqual(checker().evaluate(self.plan, self.results, self.root / "missing")["status"], "INVALID")

    def test_cross_run_identity_cannot_be_mixed(self):
        for field in ("plan_sha256", "inputs_sha256", "configuration_sha256"):
            with self.subTest(field=field):
                original = self.results["runs"][0][field]
                self.results["runs"][0][field] = "0" * 64
                self.assertEqual(self.evaluate()["status"], "INVALID")
                self.results["runs"][0][field] = original

    def test_coverage_and_metric_evidence_required(self):
        for name in ("coverage", "measurements"):
            with self.subTest(kind=name):
                original = copy.deepcopy(self.results[name])
                self.results[name] = []
                self.assertEqual(self.evaluate()["status"], "INCOMPLETE")
                self.results[name] = copy.deepcopy(original)
                self.results[name][0]["unit"] = "wrong unit"
                self.assertEqual(self.evaluate()["status"], "INVALID")
                self.results[name] = copy.deepcopy(original)
                self.results[name][0]["value"] = 0
                self.assertEqual(self.evaluate()["status"], "INCOMPLETE")
                self.results[name] = copy.deepcopy(original)

    def test_historical_failure_does_not_mask_current_regression(self):
        historical = copy.deepcopy(self.results["runs"][0])
        historical.update(id="RUN-OLD", status="FAIL", errors=1, inputs_sha256="f" * 64)
        self.results["runs"].append(historical)
        self.assertEqual(self.evaluate()["status"], "ACCEPTED")
        self.results["measurements"][0]["run_id"] = "RUN-OLD"
        self.assertEqual(self.evaluate()["status"], "INVALID")

    def test_same_baseline_failure_needs_explicit_disposition_before_rerun_pass(self):
        failed = copy.deepcopy(self.results["runs"][0])
        failed.update(id="RUN-FAIL", status="FAIL", errors=1)
        self.results["runs"].append(failed)
        self.assertEqual(self.evaluate()["status"], "INCOMPLETE")
        failed["disposition"] = {"reason": "environment interruption diagnosed; complete rerun", "regression_runs": ["RUN-1"]}
        self.assertEqual(self.evaluate()["status"], "ACCEPTED")
        failed["disposition"]["regression_runs"] = ["RUN-FAIL"]
        self.assertEqual(self.evaluate()["status"], "INVALID")

    def test_current_run_selection_must_be_explicit_and_nonempty(self):
        self.results["current_runs"] = []
        self.results["observations"] = []
        self.results["coverage"] = []
        self.results["measurements"] = []
        self.assertEqual(self.evaluate()["status"], "INCOMPLETE")
        self.results["current_runs"] = ["unknown"]
        self.assertEqual(self.evaluate()["status"], "INVALID")

    def test_empty_coverage_targets_need_no_invented_coverage(self):
        self.plan["coverage_targets"] = []
        self.results["coverage"] = []
        self.bind()
        self.assertEqual(self.evaluate()["status"], "ACCEPTED")

    def test_unapproved_exclusion_is_not_a_bypass(self):
        self.plan["requirements"][0]["required"] = False
        self.bind()
        self.assertEqual(self.evaluate()["status"], "INVALID")
        self.plan["requirements"][0]["exclusion"] = {"reason": "out of scope", "authorization": "User decision EX-1, 2026-11-10"}
        self.bind()
        self.assertEqual(self.evaluate()["status"], "ACCEPTED")

    def test_missing_isolation_and_risk_controls_are_detected(self):
        self.plan["cases"][0]["variants"][0]["checks"].pop()
        self.assertTrue(checker().validate_plan(self.plan))
        self.plan = json.loads((self.root / "plan.json").read_text())
        self.plan["cases"][0]["high_risk"]["normal_controls"] = []
        self.assertTrue(checker().validate_plan(self.plan))

    def test_duplicate_ids_unknown_references_and_wrong_variant_rejected(self):
        self.results["observations"][0]["variant_id"] = "VAR-NORMAL"
        self.assertEqual(self.evaluate()["status"], "INVALID")
        self.results["observations"][0]["variant_id"] = "VAR-A"
        self.results["observations"][0]["run_id"] = "missing"
        self.assertEqual(self.evaluate()["status"], "INVALID")
        self.plan["requirements"].append(copy.deepcopy(self.plan["requirements"][0]))
        self.assertTrue(checker().validate_plan(self.plan))

    def test_malformed_types_are_errors_not_tracebacks(self):
        for value in (None, [], "plan", 1):
            self.assertTrue(checker().validate_plan(value))
            self.assertEqual(checker().evaluate(value, self.results, self.root)["status"], "INVALID")
        for field in ("runs", "observations", "coverage", "measurements"):
            original = self.results[field]
            self.results[field] = {"bad": "shape"}
            self.assertEqual(self.evaluate()["status"], "INVALID")
            self.results[field] = original

    def test_nested_bad_types_return_invalid_instead_of_crashing(self):
        for collection, field in (("runs", "status"), ("observations", "status")):
            original = self.results[collection][0][field]
            self.results[collection][0][field] = []
            self.assertEqual(self.evaluate()["status"], "INVALID")
            self.results[collection][0][field] = original
        for field in ("op", "kind"):
            original = self.plan["metrics"][0][field]
            self.plan["metrics"][0][field] = []
            self.assertTrue(checker().validate_plan(self.plan))
            self.plan["metrics"][0][field] = original

    def test_bad_variant_id_returns_invalid_without_unhashable_crash(self):
        for value in ([], {}):
            self.plan["cases"][0]["variants"][0]["id"] = value
            self.assertEqual(self.evaluate()["status"], "INVALID")

    def test_large_finite_integer_target_is_not_a_float_overflow(self):
        self.plan["metrics"][0]["target"] = 10 ** 1000
        self.assertEqual(checker().validate_plan(self.plan), [])
        self.bind()
        self.assertEqual(self.evaluate()["status"], "INCOMPLETE")

    def test_unsafe_paths_and_symlink_escape_rejected(self):
        for path in ("../outside", "/tmp/outside", "inputs/../../outside"):
            original = self.plan["inputs"]["files"][0]["path"]
            self.plan["inputs"]["files"][0]["path"] = path
            self.assertTrue(checker().validate_plan(self.plan))
            self.plan["inputs"]["files"][0]["path"] = original
        outside = Path(self.tmp.name) / "outside.sv"
        outside.write_text("secret\n")
        (self.root / "inputs/design.sv").unlink()
        (self.root / "inputs/design.sv").symlink_to(outside)
        self.assertEqual(self.evaluate()["status"], "INVALID")

    def test_duplicate_json_keys_and_nonfinite_values_are_rejected(self):
        for data in ('{"runs": [], "runs": []}', '{"value": NaN}'):
            path = self.root / "bad.json"
            path.write_text(data)
            with self.assertRaises(ValueError):
                checker().load_json(path)

    def test_cli_exit_codes_and_plan_only(self):
        def run(*args):
            return subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True, cwd=self.root)
        args = ["--plan", "plan.json", "--results", "results.json", "--root", str(self.root)]
        self.assertEqual(run(*args).returncode, 0)
        self.assertEqual(run("--plan", str(self.root / "plan.json"), "--plan-only").returncode, 0)
        self.results["observations"][0]["status"] = "SKIP"
        (self.root / "results.json").write_text(json.dumps(self.results))
        self.assertEqual(run(*args).returncode, 2)
        (self.root / "results.json").write_text('{"runs": [], "runs": []}')
        self.assertEqual(run(*args).returncode, 1)


if __name__ == "__main__":
    unittest.main()
