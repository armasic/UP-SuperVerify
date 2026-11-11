"""Report tests check conclusions and complete details, not wording snapshots."""
import csv
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

BASE = Path(__file__).resolve().parents[1]
FIXTURE = BASE / "tests/fixtures/evidence"
SCRIPT = BASE / "scripts/render_report.py"


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        shutil.copytree(FIXTURE, self.root)

    def generate(self, name="report.md", *extra):
        self.assertTrue(SCRIPT.is_file(), "Report generator has not been implemented")
        return subprocess.run([sys.executable, str(SCRIPT), "--plan", "plan.json", "--results", "results.json", "--root", str(self.root), "--output", str(self.root / name), *extra], text=True, capture_output=True)

    def test_report_uses_evaluated_status_and_preserves_all_check_details(self):
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        body = (self.root / "report.md").read_text()
        self.assertIn("ACCEPTED", body)
        self.assertNotIn("This untrusted summary", body)
        with (self.root / "report-details.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual({r["check_id"] for r in rows}, {"CP-A", "CP-B", "CP-NORMAL-A", "CP-NORMAL-B"})
        self.assertEqual({r["variant_id"] for r in rows}, {"VAR-A", "VAR-NORMAL"})
        isolation = next(r for r in rows if r["check_id"] == "CP-B")
        self.assertEqual(isolation["expected"], "B=0")
        self.assertEqual(isolation["stimulus"], "select=A; event=1，推进一个时钟")
        self.assertTrue(isolation["oracle"])
        self.assertEqual(json.loads(isolation["requirement_sources"])["REQ-ROUTE"], "虚构规范 第1节")

    def test_pass_summary_does_not_override_blocked_result(self):
        path = self.root / "results.json"
        results = json.loads(path.read_text())
        results["summary"] = "ACCEPTED — ALL PRODUCT REQUIREMENTS PASS"
        results["observations"][0]["status"] = "BLOCKED"
        path.write_text(json.dumps(results))
        result = self.generate()
        self.assertEqual(result.returncode, 2, result.stderr)
        body = (self.root / "report.md").read_text()
        self.assertIn("INCOMPLETE", body)
        self.assertNotIn("ALL PRODUCT REQUIREMENTS PASS", body)
        self.assertNotIn("ACCEPTED", body)

    def test_repeated_runs_keep_body_compact_and_reproduction_in_details(self):
        path = self.root / "results.json"
        results = json.loads(path.read_text())
        original_run = results["runs"][0]
        original_observations = list(results["observations"])
        for index in range(2, 22):
            run = dict(original_run, id="RUN-" + str(index),
                       command="fictional-example --case " + str(index), seed=str(index))
            results["runs"].append(run)
            results["current_runs"].append(run["id"])
            results["observations"].extend(dict(obs, run_id=run["id"]) for obs in original_observations)
        path.write_text(json.dumps(results))
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        body = (self.root / "report.md").read_text()
        self.assertEqual(body.count(original_run["tool"]), 1)
        self.assertNotIn("--case", body)
        with (self.root / "report-details.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual({r["run_id"] for r in rows}, set(results["current_runs"]))
        latest = next(r for r in rows if r["run_id"] == "RUN-21")
        metadata = json.loads(latest["run_metadata"])
        self.assertEqual(metadata["command"], "fictional-example --case 21")
        self.assertEqual(metadata["plan_sha256"], original_run["plan_sha256"])

    def test_invalid_data_does_not_overwrite_existing_report(self):
        (self.root / "report.md").write_text("previous report")
        (self.root / "results.json").write_text('{"runs": [], "runs": []}')
        result = self.generate()
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.root / "report.md").read_text(), "previous report")
        self.assertFalse((self.root / "report-details.csv").exists())

    def test_private_absolute_paths_are_not_exported(self):
        path = self.root / "results.json"
        results = json.loads(path.read_text())
        results["runs"][0]["command"] = "vcs " + str(self.root / "inputs/design.sv") + " --log /Users/privateuser/run.log"
        results["observations"][0]["actual"] = "A=1; debug /home/privateuser/secret.log"
        path.write_text(json.dumps(results))
        self.assertEqual(self.generate().returncode, 0)
        for name in ("report.md", "report-details.csv"):
            content = (self.root / name).read_text()
            self.assertNotIn(str(self.root), content)
            self.assertNotIn("/Users/privateuser", content)
            self.assertNotIn("/home/privateuser", content)

    def test_quoted_paths_keep_run_metadata_valid_json(self):
        path = self.root / "results.json"
        results = json.loads(path.read_text())
        for directory in ("private", "private dir"):
            with self.subTest(directory=directory):
                results["runs"][0]["command"] = 'vcs "/Users/' + directory + '/design.sv" --log "C:\\' + directory + '\\run.log"'
                path.write_text(json.dumps(results))
                self.assertEqual(self.generate().returncode, 0)
                with (self.root / "report-details.csv").open(encoding="utf-8-sig", newline="") as stream:
                    row = next(csv.DictReader(stream))
                metadata = json.loads(row["run_metadata"])
                self.assertEqual(metadata["command"], 'vcs "<absolute-path>" --log "<absolute-path>"')
                self.assertEqual(metadata["checks"], results["runs"][0]["checks"])

    def test_missing_check_still_has_a_csv_row(self):
        path = self.root / "results.json"
        results = json.loads(path.read_text())
        results["observations"].pop()
        results["runs"][0]["checks"] = 3
        path.write_text(json.dumps(results))
        self.assertEqual(self.generate().returncode, 2)
        with (self.root / "report-details.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        row = next(r for r in rows if r["check_id"] == "CP-NORMAL-B")
        self.assertEqual(row["status"], "NOT_RUN")
        self.assertEqual(row["expected"], "B=0")

    def test_division_expression_is_preserved_in_complete_details(self):
        path = self.root / "plan.json"
        plan = json.loads(path.read_text())
        plan["cases"][0]["variants"][0]["checks"][0]["expected"] = "output = input / 2; a = (input+1)/2; b = input /2"
        path.write_text(json.dumps(plan))
        import hashlib
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        path = self.root / "results.json"
        results = json.loads(path.read_text())
        results["runs"][0]["plan_sha256"] = digest
        path.write_text(json.dumps(results))
        self.assertEqual(self.generate().returncode, 0)
        with (self.root / "report-details.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["expected"], "output = input / 2; a = (input+1)/2; b = input /2")

    def test_missing_root_does_not_allow_overwriting_the_input_plan(self):
        original = (self.root / "plan.json").read_bytes()
        path = self.root / "plan.md"
        path.write_bytes(original)
        result = subprocess.run([sys.executable, str(SCRIPT), "--plan", str(path), "--results", str(self.root / "results.json"), "--output", str(path)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(path.read_bytes(), original)

    def test_output_cannot_replace_input_or_evidence(self):
        original = (self.root / "plan.json").read_bytes()
        result = self.generate("plan.json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.root / "plan.json").read_bytes(), original)
        result = self.generate("inputs/checks.sv")
        self.assertEqual(result.returncode, 1)

    @unittest.skipUnless(importlib.util.find_spec("docx"), "optional python-docx is not installed")
    def test_word_export_has_chinese_title_black_headings_and_compact_tables(self):
        result = self.generate("report.docx")
        self.assertEqual(result.returncode, 0, result.stderr)
        from docx import Document
        doc = Document(self.root / "report.docx")
        self.assertEqual(doc.paragraphs[0].style.name, "Title")
        self.assertTrue(any("ACCEPTED" in p.text for p in doc.paragraphs))
        self.assertTrue(doc.tables)
        for name in ("Title", "Heading 1", "Heading 2"):
            self.assertEqual(str(doc.styles[name].font.color.rgb), "000000")
            self.assertFalse(doc.styles[name].element.xpath("./w:pPr/w:pBdr"))
        with zipfile.ZipFile(self.root / "report.docx") as archive:
            xml = archive.read("word/document.xml").decode()
        self.assertIn("tblHeader", xml)
        self.assertNotIn(str(self.root), xml)

    def test_missing_docx_dependency_is_clear_and_does_not_install(self):
        # -S omits site-packages, exercising the real optional-dependency failure.
        self.assertTrue(SCRIPT.is_file(), "Report generator has not been implemented")
        result = subprocess.run([sys.executable, "-S", str(SCRIPT), "--plan", "plan.json", "--results", "results.json", "--root", str(self.root), "--output", str(self.root / "report.docx")], text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("python-docx", result.stderr)
        self.assertFalse((self.root / "report.docx").exists())
        self.assertFalse((self.root / "report-details.csv").exists())


if __name__ == "__main__":
    unittest.main()
