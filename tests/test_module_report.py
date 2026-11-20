"""Module delivery against a fictional ledger. The one-pixel image only tests embedding."""
import base64
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
sys.path.insert(0, str(BASE / "scripts"))
from check_evidence import canonical_sha256, evaluate, file_sha256

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC")


class ModuleReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "fixture"
        shutil.copytree(BASE / "tests/fixtures/evidence", self.root)
        self.plan = json.loads((self.root / "plan.json").read_text())
        self.data = json.loads((self.root / "results.json").read_text())
        self.plan["module_report"] = dict(version="V0.5", strategy="按接收对象比较选中与保持行为。", waveform_checks=["CP-B"])
        self.run = self.data["runs"][0]
        self.run["plan_sha256"] = canonical_sha256(self.plan)
        self.pic = "logs/key case.png"
        self.trace = "logs/case.vcd"
        for name, content in ((self.trace, b"$comment synthetic test fixture $end"), (self.pic, PNG)):
            path = self.root / name
            path.write_bytes(content)
            self.run["logs"].append(dict(path=name, sha256=file_sha256(path)))
        self.obs = self.data["observations"][1]
        self.obs["waveform"] = dict(trace=self.trace, image=self.pic, signals=["clk", "A", "B"], window="20–30 ns")

    def export(self, name):
        for filename, data in (("plan.json", self.plan), ("results.json", self.data)):
            (self.root / filename).write_text(json.dumps(data))
        return subprocess.run([sys.executable, str(BASE / "scripts/render_report.py"), "--root", str(self.root),
                               "--plan", "plan.json", "--results", "results.json", "--output", str(self.root / name)],
                              capture_output=True, text=True)

    def test_report_layout_relative_figure_and_current_observation(self):
        name = "docs/verification_V0.5.md"
        result = self.export(name)
        self.assertEqual(result.returncode, 0, result.stderr)
        body = (self.root / name).read_text()
        self.assertLess(body.index("## 验证策略"), body.index("## 验证方案"))
        self.assertLess(body.index("## 验证方案"), body.index("## 验证结果"))
        for fragment in ("V0.5", self.plan["module_report"]["strategy"], "REQ-ROUTE", "VAR-A", "CP-B", "RUN-1",
                         "20–30 ns", "B=0", "clk", "select=A", "../logs/key%20case.png", self.trace):
            self.assertIn(fragment, body)
        self.assertNotIn(str(self.root), body)
        self.assertTrue((self.root / "docs/verification-details_V0.5.csv").is_file())

    def test_missing_capture_is_an_incomplete_report(self):
        del self.obs["waveform"]
        result = self.export("review_V0.5.md")
        self.assertEqual(result.returncode, 2, result.stderr)
        body = (self.root / "review_V0.5.md").read_text()
        self.assertIn("CP-B", body)
        self.assertIn("INCOMPLETE", body)
        self.assertNotIn("![", body)

    def test_old_or_undeclared_evidence_and_changed_rtl_are_invalid(self):
        for bad in ("logs/old.png", "../logs/old.png", self.trace):
            with self.subTest(bad=bad):
                self.obs["waveform"]["image"] = bad
                self.assertEqual(evaluate(self.plan, self.data, self.root)["status"], "INVALID")
        self.obs["waveform"]["image"] = self.pic
        self.assertEqual(evaluate(self.plan, self.data, self.root)["status"], "ACCEPTED")
        (self.root / "inputs/design.sv").write_text("changed after run")
        self.assertEqual(evaluate(self.plan, self.data, self.root)["status"], "INVALID")

    def test_wrong_version_preserves_output_and_stale_plan_is_rejected(self):
        path = self.root / "verification_v0p5.md"
        path.write_text("manual version")
        self.assertEqual(self.export(path.name).returncode, 1)
        self.assertEqual(path.read_text(), "manual version")
        self.plan["module_report"]["version"] = "v0p5"
        self.assertEqual(self.export(path.name).returncode, 1)
        self.run["plan_sha256"] = canonical_sha256(self.plan)
        self.assertEqual(self.export(path.name).returncode, 0)

    def test_capture_from_another_run_and_corrupt_image_cannot_be_reported(self):
        other = dict(self.run, id="other", logs=list(self.run["logs"]))
        self.data["runs"].append(other)
        image_record = self.run["logs"].pop()
        self.assertEqual(evaluate(self.plan, self.data, self.root)["status"], "INVALID")
        self.run["logs"].append(image_record)
        picture = self.root / self.pic
        picture.write_bytes(PNG[:-10])
        image_record["sha256"] = file_sha256(picture)
        self.assertEqual(self.export("invalid_V0.5.md").returncode, 1)
        self.assertFalse((self.root / "invalid_V0.5.md").exists())

    def test_no_root_never_reads_or_links_waveforms(self):
        from render_report import main
        self.export("verification_V0.5.md")
        dest = self.root / "unchecked_V0.5.md"
        self.assertEqual(main(["--plan", str(self.root / "plan.json"), "--results", str(self.root / "results.json"),
                               "--output", str(dest)]), 2)
        self.assertNotIn("![", dest.read_text())

    @unittest.skipUnless(importlib.util.find_spec("docx"), "optional Word dependency")
    def test_word_contains_version_order_caption_and_embedded_picture(self):
        name = "docs/verification_V0.5.docx"
        result = self.export(name)
        self.assertEqual(result.returncode, 0, result.stderr)
        from docx import Document
        doc = Document(self.root / name)
        self.assertIn("V0.5", doc.paragraphs[0].text)
        headings = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
        for heading in ("验证策略", "验证方案", "验证结果"):
            self.assertIn(heading, headings)
        self.assertEqual(len(doc.inline_shapes), 1)
        self.assertTrue(any("CP-B" in p.text and "20–30 ns" in p.text for p in doc.paragraphs))
        with zipfile.ZipFile(self.root / name) as package:
            self.assertTrue(any(package.read(member) == PNG for member in package.namelist() if member.startswith("word/media/")))
