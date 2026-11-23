"""Module delivery against a fictional ledger. The one-pixel image only tests embedding."""
import base64
import importlib.util
import json
import os
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
        self.plan["module_report"] = dict(version="v0p5", module="路由器", strategy="按接收对象比较选中与保持行为。", waveform_checks=["CP-B"])
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

    def export(self, name, *options):
        for filename, data in (("plan.json", self.plan), ("results.json", self.data)):
            (self.root / filename).write_text(json.dumps(data))
        return subprocess.run([sys.executable, str(BASE / "scripts/render_report.py"), "--root", str(self.root),
                               "--plan", "plan.json", "--results", "results.json", "--output", str(self.root / name), *options],
                              capture_output=True, text=True)

    def test_report_layout_relative_figure_and_current_observation(self):
        name = "docs/verification_v0p5.md"
        result = self.export(name)
        self.assertEqual(result.returncode, 0, result.stderr)
        body = (self.root / name).read_text()
        self.assertLess(body.index("## 验证策略"), body.index("## 验证方案"))
        self.assertLess(body.index("## 验证方案"), body.index("## 验证结果"))
        for fragment in ("v0p5", self.plan["module_report"]["strategy"], "REQ-ROUTE", "VAR-A", "CP-B", "RUN-1",
                         "20–30 ns", "B=0", "clk", "select=A", "../logs/key%20case.png", self.trace):
            self.assertIn(fragment, body)
        self.assertNotIn(str(self.root), body)
        self.assertTrue((self.root / "docs/verification-details_v0p5.csv").is_file())

    def test_missing_capture_is_an_incomplete_report(self):
        del self.obs["waveform"]
        result = self.export("review_v0p5.md")
        self.assertEqual(result.returncode, 2, result.stderr)
        body = (self.root / "review_v0p5.md").read_text()
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
        path = self.root / "verification_v0p6.md"
        path.write_text("manual version")
        self.assertEqual(self.export(path.name).returncode, 1)
        self.assertEqual(path.read_text(), "manual version")
        self.plan["module_report"]["version"] = "v0p6"
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
        self.assertEqual(self.export("invalid_v0p5.md").returncode, 1)
        self.assertFalse((self.root / "invalid_v0p5.md").exists())

    def test_no_root_never_reads_or_links_waveforms(self):
        from render_report import main
        self.export("verification_v0p5.md")
        dest = self.root / "unchecked_v0p5.md"
        self.assertEqual(main(["--plan", str(self.root / "plan.json"), "--results", str(self.root / "results.json"),
                               "--output", str(dest)]), 2)
        self.assertNotIn("![", dest.read_text())

    @unittest.skipUnless(importlib.util.find_spec("docx"), "optional Word dependency")
    def test_word_contains_version_order_caption_and_embedded_picture(self):
        name = "docs/verification_v0p5.docx"
        result = self.export(name)
        self.assertEqual(result.returncode, 0, result.stderr)
        from docx import Document
        doc = Document(self.root / name)
        self.assertEqual(len(doc.sections), 3)
        self.assertIn("v0p5", " ".join(p.text for p in doc.paragraphs[:8]))
        self.assertTrue(any("路由器" in p.text for p in doc.paragraphs[:8]))
        self.assertEqual([section._sectPr.xpath("w:pgNumType/@w:fmt") for section in doc.sections], [[], ["upperRoman"], ["decimal"]])
        self.assertTrue(all(abs(s.page_width.mm-210)<0.1 and abs(s.page_height.mm-297)<0.1 for s in doc.sections))
        self.assertTrue(all(s._sectPr.xpath("w:pgNumType/@w:start") == ["1"] for s in list(doc.sections)[1:]))
        names = [p.text for p in doc.paragraphs]
        for name_on_page in ("目录", "图目录", "表目录", "修订记录"):
            self.assertIn(name_on_page, names)
        self.assertLess(names.index("目录"), names.index("表目录"))
        self.assertLess(names.index("表目录"), names.index("图目录"))
        anchors = set(doc.element.xpath('.//w:bookmarkStart/@w:name'))
        fields = doc.element.xpath('.//w:fldSimple/@w:instr')
        references = {field.split()[1] for field in fields if field.strip().startswith('PAGEREF ')}
        self.assertGreater(len(references), 10)
        for label in ("文件名称", "文件编号", "文件版本号", "项目任务号", "编制日期"):
            self.assertTrue(any(p.text.startswith(label + "\t") for p in doc.paragraphs))
        self.assertTrue(any(p.text.startswith("表 2-1") for p in doc.paragraphs))
        self.assertTrue(any(p.text.startswith("图 3-1") for p in doc.paragraphs))
        for sec in list(doc.sections)[1:]:
            self.assertEqual(sec.header.paragraphs[0].text, "路由器")
            self.assertEqual(sec.header.paragraphs[0].alignment, 2)
            self.assertEqual(sec.footer.paragraphs[0].alignment, 2)
        for sec in doc.sections:
            self.assertEqual([getattr(sec, a).twips for a in ("top_margin", "left_margin", "bottom_margin", "right_margin")], [1644,1418,1418,1134])
        self.assertEqual(doc.styles["Normal"].font.name, "Times New Roman")
        self.assertEqual(doc.styles["Normal"].element.xpath(".//w:rFonts/@w:eastAsia"), ["宋体"])
        self.assertEqual(doc.styles["Heading 1"].element.xpath(".//w:rFonts/@w:eastAsia"), ["黑体"])
        self.assertTrue(references <= anchors)
        links = {r.target_ref for r in doc.part.rels.values() if r.is_external}
        self.assertTrue({"../logs/case.vcd", "../logs/key%20case.png", "../docs/verification-details_v0p5.csv", "../plan.json", "../results.json"} <= links)
        self.assertIn("CANDIDATE", result.stdout)
        headings = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
        for heading in ("验证策略", "验证方案", "验证结果"):
            self.assertTrue(any(text.endswith(heading) for text in headings))
        self.assertEqual(len(doc.inline_shapes), 1)
        self.assertTrue(any("CP-B" in p.text and "20–30 ns" in p.text for p in doc.paragraphs))
        with zipfile.ZipFile(self.root / name) as package:
            self.assertTrue(any(package.read(member) == PNG for member in package.namelist() if member.startswith("word/media/")))


    def test_module_version_forms_and_details_are_strict(self):
        for version in ("V0.5", "v0.5", "V0p5", "v2", "v0p1p2", "0p5", "v0p5-beta"):
            with self.subTest(version=version):
                self.plan["module_report"]["version"] = version
                self.run["plan_sha256"] = canonical_sha256(self.plan)
                self.assertEqual(self.export("bad_" + version + ".md").returncode, 1)
        self.plan["module_report"]["version"] = "v0p5"
        self.run["plan_sha256"] = canonical_sha256(self.plan)
        for wrong in ("details.csv", "details_V0.5.csv", "details_v0p4.csv"):
            path = self.root / wrong
            path.write_text("manual")
            self.assertEqual(self.export("report_v0p5.md", "--details", str(path)).returncode, 1)
            self.assertEqual(path.read_text(), "manual")

    def test_conflicting_module_and_unrendered_pdf_preserve_existing_files(self):
        name = "review_v0p5.docx"
        existing = self.root / name
        existing.write_bytes(b"existing")
        for options in (("--module-name", "other"), ("--pdf",)):
            with self.subTest(options=options):
                self.assertEqual(self.export(name, *options).returncode, 1)
                self.assertEqual(existing.read_bytes(), b"existing")
                self.assertFalse(existing.with_suffix(".pdf").exists())

    @unittest.skipUnless(os.environ.get("SUPERVERIFY_SOFFICE"), "provide the project LibreOffice for PDF integration")
    def test_rendered_word_pdf_page_cache_and_evidence_failures(self):
        from docx import Document
        from docx.oxml.ns import qn
        name = "release/review_v0p5.docx"
        result = self.export(name, "--soffice", os.environ["SUPERVERIFY_SOFFICE"], "--pdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RENDERED", result.stdout)
        dest = self.root / name
        check = [sys.executable, str(BASE / "scripts/verify_layout.py"), str(dest)]
        def verify(expected, message=""):
            run = subprocess.run(check, capture_output=True, text=True)
            self.assertEqual(run.returncode, expected, run.stderr)
            self.assertIn(message, run.stderr)
            if expected == 0:
                self.assertGreaterEqual(json.loads(run.stdout)["front_pages"], 2)
        verify(0)
        latest = self.root / "latest"
        latest.mkdir()
        for source in (dest, dest.with_suffix(".pdf")):
            (latest/source.name).symlink_to(Path("..")/source.parent.name/source.name)
        alias = subprocess.run([*check[:-1], str(latest/dest.name)], capture_output=True, text=True)
        self.assertEqual(alias.returncode, 0, alias.stderr)
        original = dest.read_bytes()
        doc = Document(dest)
        cover = next(p for p in doc.paragraphs if p.text == "文件版本号\tv0p5")
        cover.text = "文件版本号\tV0.5"
        doc.save(dest)
        verify(1, "visible cover")
        dest.write_bytes(original)
        doc = Document(dest)
        first_ref = doc.element.xpath(".//w:fldSimple")[0]
        next(first_ref.iter(qn("w:t"))).text = "999"
        doc.save(dest)
        verify(1, "cached Word page")
        dest.write_bytes(original)
        details = dest.with_name("review-details_v0p5.csv")
        old_csv = details.read_bytes()
        details.write_bytes(old_csv.replace(b"v0p5", b"v0p4"))
        verify(1, "CSV contains")
        details.write_bytes(old_csv)
        doc = Document(dest)
        actual = next(p for p in doc.paragraphs if "有效通过检查点" in p.text and not p.text.startswith("v0p5"))
        actual.add_run("changed observation")
        doc.save(dest)
        verify(1, "content and PDF")
        dest.write_bytes(original)
        (self.root / self.trace).unlink()
        verify(1, "missing evidence link")
