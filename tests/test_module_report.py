"""Module delivery against a fictional ledger. The one-pixel image only tests embedding."""
import base64
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
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
        self.obs["waveform"] = dict(trace=self.trace, image=self.pic, signals=["clk", "/tb_router/dut/ready", "B"], window="20–30 ns")

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
                         "20–30 ns", "B=0", "clk", "/tb_router/dut/ready", "select=A", "../logs/key%20case.png", self.trace):
            self.assertIn(fragment, body)
        self.assertNotIn(str(self.root), body)
        with (self.root / "docs/verification-details_v0p5.csv").open(encoding="utf-8-sig", newline="") as stream:
            wave = next(json.loads(row["waveform"]) for row in csv.DictReader(stream) if row["waveform"])
        self.assertEqual(wave["signals"], self.obs["waveform"]["signals"])

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
        result = self.export(name, "--document-number", "VERIFY-27", "--task-number", "MOD-18",
                             "--issue-date", "2025-08-15")
        self.assertEqual(result.returncode, 0, result.stderr)
        from docx import Document
        doc = Document(self.root / name)
        self.assertEqual(len(doc.sections), 3)
        self.assertEqual(doc.core_properties.subject, "路由器")
        cover = doc.tables[0]._tbl
        self.assertIs(doc.element.body[0], cover)
        self.assertEqual(len(cover.xpath("./w:tblGrid/w:gridCol")), 6)
        cover_rows = cover.xpath("./w:tr")
        self.assertEqual(len(cover_rows), 8)
        for row, value in ((1, self.plan["project"]), (2, "文件名称：路由器模块验证文档"),
                           (3, "文件编号：VERIFY-27"), (4, "文件版本号：v0p5"),
                           (5, "项目任务号：MOD-18"), (7, "编制日期 2025-08-15")):
            self.assertEqual("".join(cover_rows[row].xpath(".//w:t/text()")), value)
            self.assertEqual(cover_rows[row].xpath("./w:tc/w:tcPr/w:gridSpan/@w:val"),
                             ["6" if row in (1, 7) else "5"])
        self.assertTrue(all(t.style.name == "PLM table" for t in doc.tables[1:]))
        self.assertTrue(all(t.cell(0, 0).paragraphs[0].style.name == "表格正文" for t in doc.tables[1:]))
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
        # A reviewer can update the entire index after adding a heading or caption.
        # Linked page-number fields alone can only refresh existing rows.
        for anchor, code in (("SVContents", r'TOC \o "1-3" \h \z \u'), ("SVTables", r'TOC \h \z \c "表"'),
                             ("SVFigures", r'TOC \h \z \c "图"')):
            title = doc.element.body.xpath(f'./w:p[w:bookmarkStart[@w:name="{anchor}"]]')[0]
            entry = title.getnext()
            self.assertEqual([t.strip() for t in entry.xpath('./w:r/w:instrText/text()')], [code])
            while entry.getnext().xpath('./w:fldSimple'):
                entry = entry.getnext()
            self.assertEqual(entry.xpath('./w:r/w:fldChar/@w:fldCharType')[-1], 'end')
        for para in doc.paragraphs:
            if para.text in ("修订记录", "目录", "表目录", "图目录"):
                self.assertEqual(para.style.name, "TOC 标题1")
            if para.text in ("目录", "表目录"):
                self.assertTrue(para.paragraph_format.page_break_before)
        self.assertTrue(any(p.style.name == "toc 1" for p in doc.paragraphs))
        self.assertTrue(any(p.style.name == "table of figures" for p in doc.paragraphs))
        captions = [p for p in doc.paragraphs if p.style.name == "Caption"]
        cached = ["".join(p._p.xpath(".//w:t/text()")) for p in captions]
        self.assertTrue(any(text.startswith("表 2-1") for text in cached))
        self.assertTrue(any(text.startswith("图 3-1") for text in cached))
        for p, title in zip(captions, cached):
            ordinal = title.split("　")[0].split("-")[1]
            switch = r"\r 1" if ordinal == "1" else r"\s 1"
            self.assertEqual(p._p.xpath("./w:fldSimple/@w:instr"),
                             [r"STYLEREF 1 \n", "SEQ " + title[0] + r" \* ARABIC " + switch])
        for sec in list(doc.sections)[1:]:
            self.assertEqual(sec.header.paragraphs[0].text, "路由器")
            self.assertEqual(sec.header.paragraphs[0].alignment, 2)
            self.assertEqual(sec.footer.paragraphs[0].alignment, 2)
        for sec in doc.sections:
            self.assertEqual([getattr(sec, a).twips for a in ("top_margin", "left_margin", "bottom_margin", "right_margin")], [1644,1418,1418,1134])
        self.assertEqual(doc.styles["Normal"].font.name, "Times New Roman")
        self.assertEqual(doc.styles["Normal"].font.size.pt, 14)
        self.assertEqual(doc.styles["Normal"].element.xpath(".//w:rFonts/@w:eastAsia"), ["宋体"])
        self.assertEqual(doc.styles["Heading 1"].element.xpath(".//w:rFonts/@w:eastAsia"), ["黑体"])
        self.assertTrue(references <= anchors)
        links = {r.target_ref for r in doc.part.rels.values() if r.is_external}
        self.assertTrue({"../logs/case.vcd", "../logs/key%20case.png", "../docs/verification-details_v0p5.csv", "../plan.json", "../results.json"} <= links)
        self.assertIn("CANDIDATE", result.stdout)
        headings = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
        self.assertEqual(headings, ["验证策略", "验证方案", "验证结果"])
        self.assertEqual(len(doc.inline_shapes), 1)
        self.assertTrue(any("CP-B" in p.text and "20–30 ns" in p.text and "/tb_router/dut/ready" in p.text
                            for p in doc.paragraphs))
        with zipfile.ZipFile(self.root / name) as package:
            self.assertTrue(any(package.read(member) == PNG for member in package.namelist() if member.startswith("word/media/")))
            for member in package.namelist():
                if member.endswith(".xml"):
                    self.assertNotIn(b"{{", package.read(member), member)
            self.assertNotIn("说明模块用途".encode(), package.read("word/document.xml"))

    @unittest.skipUnless(importlib.util.find_spec("docx"), "optional Word dependency")
    def test_archived_paragraph_cover_retains_empty_control_numbers(self):
        from docx import Document
        from verify_layout import read_cover
        doc = Document()
        doc.add_paragraph(self.plan["project"])
        for label, value in (("文件名称", "路由器模块验证文档"), ("文件编号", ""), ("文件版本号", "v0p5"),
                             ("项目任务号", ""), ("编制日期", "2025-08-15")):
            doc.add_paragraph(label + "\t" + value)
        doc.add_section()
        # Body fields must never supply missing identity on the cover.
        doc.add_paragraph("文件版本号\tv1p0")
        fields, project = read_cover(doc)
        self.assertIsNone(project)
        self.assertEqual(fields["文件版本号"], "v0p5")
        self.assertEqual(fields["文件编号"], "")
        self.assertEqual(fields["项目任务号"], "")

    @unittest.skipUnless(importlib.util.find_spec("docx"), "optional Word dependency")
    def test_controlled_document_title_is_independent_of_frozen_module_name(self):
        from docx import Document
        from verify_layout import check_table_cover, read_cover
        name = "release/formal_v0p5.docx"
        title = "接口验收验证文档"
        result = self.export(name, "--title", title)
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = Document(self.root / name)
        fields, project = read_cover(doc)
        self.assertEqual(fields["文件名称"], title)
        self.assertEqual(doc.core_properties.title, title)
        self.assertEqual(doc.core_properties.subject, "路由器")
        printed = "\n".join([project, *fields, *fields.values()])
        self.assertEqual(check_table_cover(doc, fields, project, printed), "路由器")
        doc.core_properties.title = "另一书名"
        with self.assertRaisesRegex(ValueError, "title differs"):
            check_table_cover(doc, fields, project, printed)
        doc.core_properties.title = title
        doc.core_properties.subject = "另一模块"
        with self.assertRaisesRegex(ValueError, "header"):
            check_table_cover(doc, fields, project, printed)
        result = self.export(name, "--title", "  ")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(Document(self.root / name).core_properties.title, title)

    @unittest.skipUnless(importlib.util.find_spec("docx"), "optional Word dependency")
    def test_default_title_and_archived_table_metadata(self):
        from docx import Document
        from verify_layout import check_table_cover, read_cover
        name = "release/default_v0p5.docx"
        self.assertEqual(self.export(name).returncode, 0)
        doc = Document(self.root / name)
        fields, project = read_cover(doc)
        self.assertEqual(fields["文件名称"], "路由器模块验证文档")
        self.assertEqual(doc.core_properties.title, fields["文件名称"])
        # Earlier table-cover releases stored the project/version as dc:title.
        doc.core_properties.title = project + " 验证报告 v0p5"
        printed = "\n".join([project, *fields, *fields.values()])
        self.assertEqual(check_table_cover(doc, fields, project, printed), "路由器")
        fields["文件名称"] = "未登记的文件名称"
        with self.assertRaisesRegex(ValueError, "title differs"):
            check_table_cover(doc, fields, project, printed)

    @unittest.skipUnless(importlib.util.find_spec("docx"), "optional Word dependency")
    def test_missing_or_invalid_bundled_template_preserves_existing_artifacts(self):
        from docx import Document
        from docx.oxml.ns import qn
        from formal_word import MODULE_TEMPLATE
        from render_report import main
        self.export("seed_v0p5.md")
        output = self.root / "release_v0p5.docx"
        details = output.with_name("release-details_v0p5.csv")
        output.write_bytes(b"reviewer's Word")
        details.write_bytes(b"reviewer's CSV")
        template = self.root / "template.docx"
        options = ["--root", str(self.root), "--plan", "plan.json", "--results", "results.json", "--output", str(output)]
        with patch("formal_word.MODULE_TEMPLATE", template):
            for invalid in ("missing", "broken package", "empty document", "wrong cell merge"):
                if invalid == "broken package":
                    template.write_bytes(b"invalid Word package")
                if invalid == "empty document":
                    Document().save(template)
                if invalid == "wrong cell merge":
                    doc = Document(MODULE_TEMPLATE)
                    doc.tables[0]._tbl.xpath("./w:tr[5]//w:gridSpan")[0].set(qn("w:val"), "3")
                    doc.save(template)
                with self.subTest(invalid=invalid):
                    self.assertEqual(main(options), 1)
                    self.assertEqual(output.read_bytes(), b"reviewer's Word")
                    self.assertEqual(details.read_bytes(), b"reviewer's CSV")


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
    def test_archived_paragraph_cover_with_original_body_font_still_renders(self):
        from docx import Document
        from docx.shared import Pt
        from verify_layout import main, render_and_check
        name = "release/archived_v0p5.docx"
        result = self.export(name)
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = self.root / name
        doc = Document(dest)
        table = doc.tables[0]._tbl
        for line in (self.plan["project"], "路由器模块验证文档", "文件名称\t路由器模块验证文档", "文件编号\t",
                     "文件版本号\tv0p5", "项目任务号\t", "编制日期\t2025-08-15"):
            table.addprevious(doc.add_paragraph(line)._p)
        table.getparent().remove(table)
        doc.styles["Normal"].font.size = Pt(11)
        # Paragraph-cover archives predate native TOC wrappers. They still
        # contain the individual bookmarked page references.
        for run in doc.element.xpath(".//w:r[w:fldChar or w:instrText]"):
            run.getparent().remove(run)
        candidate = io.BytesIO()
        doc.save(candidate)
        word, pdf = render_and_check(candidate.getvalue(), dest.parent, os.environ["SUPERVERIFY_SOFFICE"],
                                     dest.with_name("archived-details_v0p5.csv"))
        dest.write_bytes(word)
        dest.with_suffix(".pdf").write_bytes(pdf)
        self.assertEqual(main([str(dest)]), 0)

    @unittest.skipUnless(os.environ.get("SUPERVERIFY_SOFFICE"), "provide the project LibreOffice for PDF integration")
    def test_rendered_word_pdf_page_cache_and_evidence_failures(self):
        from docx import Document
        from docx.oxml.ns import qn
        name = "release/review_v0p5.docx"
        result = self.export(name, "--title", "受控接口验证文件", "--soffice", os.environ["SUPERVERIFY_SOFFICE"], "--pdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RENDERED", result.stdout)
        dest = self.root / name
        check = [sys.executable, str(BASE / "scripts/verify_layout.py"), str(dest)]
        def verify(expected, message=""):
            run = subprocess.run(check, capture_output=True, text=True)
            self.assertEqual(run.returncode, expected, run.stderr)
            self.assertIn(message, run.stderr)
            if expected == 0:
                self.assertGreaterEqual(json.loads(run.stdout)["front_pages"], 3)
        verify(0)
        from xml.etree import ElementTree
        converted = subprocess.run([os.environ["SUPERVERIFY_SOFFICE"],
                                    "-env:UserInstallation=" + (self.root/"index-profile").as_uri(),
                                    "--headless", "--convert-to", "fodt", "--outdir", str(dest.parent), str(dest)],
                                   capture_output=True, text=True, timeout=90)
        self.assertEqual(converted.returncode, 0, converted.stderr)
        odf = ElementTree.parse(dest.with_suffix(".fodt"))
        text_ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
        indexes = [e for e in odf.iter() if e.tag in {text_ns + k for k in
                   ("table-of-content", "table-index", "illustration-index")}]
        self.assertEqual(len(indexes), 3)
        self.assertEqual(indexes[0].tag, text_ns + "table-of-content")
        self.assertEqual([e[0].get(text_ns + "caption-sequence-name") for e in indexes[1:]], ["表", "图"])
        self.assertTrue(all(e.findall(text_ns + "index-body/" + text_ns + "p") for e in indexes))
        latest = self.root / "latest"
        latest.mkdir()
        word_link = latest / "review.docx"
        word_link.symlink_to(Path("..") / name)
        pdf_link = word_link.with_suffix(".pdf")

        def verify_latest(expected):
            alias = subprocess.run([*check[:-1], str(word_link)], capture_output=True, text=True)
            self.assertEqual(alias.returncode, expected, alias.stderr)

        verify_latest(0)
        pdf_link.symlink_to("missing.pdf")
        verify_latest(1)
        pdf_link.unlink()
        pdf_link.symlink_to(Path("..") / Path(name).with_suffix(".pdf"))
        verify_latest(0)
        original = dest.read_bytes()
        doc = Document(dest)
        for code in doc.element.xpath(".//w:r[w:fldChar or w:instrText]"):
            code.getparent().remove(code)
        doc.save(dest)
        verify(1, "outside the native Word index")
        dest.write_bytes(original)
        doc = Document(dest)
        doc.element.xpath(".//w:instrText")[1].text = r' TOC \h \z \c "图" '
        doc.save(dest)
        verify(1, "native Word index instruction")
        dest.write_bytes(original)
        doc = Document(dest)
        # Change the real rendered sequence while leaving the cached index
        # intact. Checking only index page numbers cannot catch this defect.
        from verify_layout import render_and_check
        caption = next(p for p in doc.paragraphs if p.style.name == "Caption"
                       and "".join(p._p.xpath(".//w:t/text()")).startswith("表 3-1　验证结果"))
        caption._p.xpath("./w:fldSimple")[1].set(qn("w:instr"), r"SEQ 表 \r 8")
        damaged = io.BytesIO()
        doc.save(damaged)
        with self.assertRaisesRegex(ValueError, "indexed heading or caption"):
            render_and_check(damaged.getvalue(), dest.parent, os.environ["SUPERVERIFY_SOFFICE"],
                             dest.with_name("review-details_v0p5.csv"))
        doc = Document(dest)
        version_value = doc.tables[0]._tbl.xpath("./w:tr[5]//w:t[last()]")[-1]
        version_value.text = "V0.5"
        doc.save(dest)
        verify(1, "visible cover")
        dest.write_bytes(original)
        doc = Document(dest)
        from docx.shared import Pt
        doc.styles["Normal"].font.size = Pt(11)
        doc.save(dest)
        verify(1, "14 pt")
        dest.write_bytes(original)
        plan_file = self.root / "plan.json"
        old_plan = plan_file.read_bytes()
        for mismatched in (dict(self.plan, project="另一项目"),
                           dict(self.plan, module_report=dict(self.plan["module_report"], module="另一模块"))):
            plan_file.write_text(json.dumps(mismatched))
            verify(1, "linked plan project or module")
        plan_file.write_bytes(old_plan)
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
