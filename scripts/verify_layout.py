#!/usr/bin/env python3
"""Render an editable formal report and verify page references and packaged evidence."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile


def layout_identity(word, pdf):
    digest = hashlib.sha256()
    with ZipFile(io.BytesIO(word)) as archive:
        # The identity itself is stored outside word/ in core document metadata.
        for name in sorted(n for n in archive.namelist() if n.startswith("word/")):
            digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(archive.read(name)).digest())
    return "up-superverify:layout:1:" + digest.hexdigest() + ":" + hashlib.sha256(pdf).hexdigest()


def roman(value):
    digits = []
    for n, letter in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                      (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        repeat, value = divmod(value, n)
        digits.append(letter * repeat)
    return "".join(digits)


def relative_target(uri, directory):
    parsed = urlsplit(uri)
    path = Path(unquote(parsed.path))
    if parsed.scheme or parsed.netloc or path.is_absolute() or re.match(r"^[A-Za-z]:", str(path)):
        raise ValueError("expected a portable relative evidence link")
    return (directory / path).resolve()


def restore_pdf_links(doc, payload, directory):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, TextStringObject

    targets = {relative_target(rel.target_ref, directory): rel.target_ref for rel in doc.part.rels.values() if rel.is_external}
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(payload)))
    changed = False
    for page in writer.pages:
        for ref in page.get("/Annots", []):
            action = ref.get_object().get("/A")
            if action and "/URI" in action:
                uri = str(action["/URI"])
                original = targets.get(relative_target(uri, directory))
                if original and original != uri:
                    action[NameObject("/URI")] = TextStringObject(original)
                    changed = True
    if changed:
        stream = io.BytesIO()
        writer.write(stream)
        return stream.getvalue()
    return payload


COVER_LABELS = ("文件名称", "文件编号", "文件版本号", "项目任务号", "编制日期")


def read_cover(doc):
    """Read physical cover cells once, or the labeled paragraphs in an archived report."""
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    fields = {}
    tables = []
    for block in doc.element.body:
        if block.tag == qn("w:p"):
            if block.xpath("./w:pPr/w:sectPr"):
                break
            label, tab, value = Paragraph(block, doc).text.partition("\t")
            if tab and label.strip() in COVER_LABELS:
                fields[label.strip()] = value.strip()
        elif block.tag == qn("w:tbl"):
            tables.append(block)
    if not tables:
        return fields, None
    if len(tables) != 1 or len(tables[0].xpath("./w:tblGrid/w:gridCol")) != 6:
        raise ValueError("invalid merged cover table: expected a single six-column grid")
    rows = tables[0].xpath("./w:tr")
    if len(rows) != 8:
        raise ValueError("invalid merged cover table: expected eight rows")
    texts = {}
    for index in (1, 2, 3, 4, 5, 7):
        cells = rows[index].xpath("./w:tc")
        whole_row = index in (1, 7)
        if (len(cells) != (1 if whole_row else 2) or
                cells[-1].xpath("./w:tcPr/w:gridSpan/@w:val") != ["6" if whole_row else "5"]):
            raise ValueError("invalid merged cover field in row " + str(index + 1))
        texts[index] = "".join(cells[-1].xpath(".//w:t/text()")).strip()
    fields = {}
    for label, row in zip(COVER_LABELS, (2, 3, 4, 5, 7)):
        match = re.fullmatch(re.escape(label) + r"(?:\s*[:：]\s*|\s+)(.*)", texts[row], re.S)
        if not match:
            raise ValueError("missing cover field: " + label)
        fields[label] = match[1].strip()
    return fields, texts[1]


def check_table_cover(doc, fields, project, pdf_cover):
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    normal = doc.styles["Normal"]
    if (normal.font.size is None or normal.font.size.pt != 14 or normal.font.name != "Times New Roman"
            or normal.element.xpath("./w:rPr/w:rFonts/@w:eastAsia") != ["宋体"]):
        raise ValueError("formal template body requires 14 pt Song and Times New Roman")
    for n in (1, 2, 3):
        name = "Heading " + str(n)
        if name not in doc.styles or doc.styles[name].element.xpath("./w:rPr/w:rFonts/@w:eastAsia") != ["黑体"]:
            raise ValueError("formal template requires Hei chapter heading styles")
    for section in doc.sections:
        margins = (section.top_margin, section.left_margin, section.bottom_margin, section.right_margin)
        if [m.twips if m is not None else None for m in margins] != [1644, 1418, 1418, 1134]:
            raise ValueError("formal template section margins differ from the reference")
    title = fields["文件名称"]
    module = doc.core_properties.subject
    if not project or not title or not module or not fields["编制日期"]:
        raise ValueError("visible cover is missing the project, module title or issue date")
    # Previously published table-cover reports used an automatically generated
    # file title but recorded the project and version in the metadata title.
    archived_default = (title == module + "模块验证文档" and
                        doc.core_properties.title == f"{project} 验证报告 {doc.core_properties.version}")
    if doc.core_properties.title != title and not archived_default:
        raise ValueError("formal Word title differs from the visible cover")
    for section in list(doc.sections)[1:]:
        if not any(p.text.strip() == module and p.alignment == WD_ALIGN_PARAGRAPH.RIGHT for p in section.header.paragraphs):
            raise ValueError("module name in the right header differs from the Word subject")
    printed = "".join(pdf_cover.split())
    for value in (project, *fields, *fields.values()):
        if "".join(value.split()) not in printed:
            raise ValueError("PDF cover differs from the editable cover: " + value)
    return module


def check_native_indexes(doc, front):
    """Confirm each native Word TOC encloses its own editable, cached index rows."""
    from docx.oxml.ns import qn

    outlines = {"SVContents": r'\\o\s+"1-[1-9]"',
                "SVTables": r'\\c\s+"表"', "SVFigures": r'\\c\s+"图"'}
    sections = {}
    current = None
    for p in front:
        for name in p.xpath("./w:bookmarkStart/@w:name"):
            if name in outlines:
                if name in sections:
                    raise ValueError("duplicate native Word index heading: " + name)
                current = sections[name] = []
        if current is not None:
            current.append(p)
    expected = {"SVContents"}
    for instruction in doc.element.xpath(".//w:fldSimple/@w:instr"):
        tokens = instruction.strip().split()
        if tokens[:2] == ["SEQ", "表"]:
            expected.add("SVTables")
        elif tokens[:2] == ["SEQ", "图"]:
            expected.add("SVFigures")
    if sections.keys() != expected:
        raise ValueError("native Word index headings do not match the report chapters and captions")
    for name, paragraphs in sections.items():
        state, code, references = "before", [], 0
        for p in paragraphs:
            for node in p.iter():
                if node.tag == qn("w:fldChar"):
                    marker = node.get(qn("w:fldCharType"))
                    state = {("before", "begin"): "code", ("code", "separate"): "result",
                             ("result", "end"): "after"}.get((state, marker))
                    if state is None:
                        raise ValueError("invalid native Word index field boundaries: " + name)
                elif node.tag == qn("w:instrText") and state == "code":
                    code.append(node.text or "")
                elif node.tag == qn("w:fldSimple") and node.get(qn("w:instr"), "").split()[:1] == ["PAGEREF"]:
                    if state != "result":
                        raise ValueError("cached page reference outside the native Word index: " + name)
                    references += 1
                elif node.tag == qn("w:hyperlink") and node.get(qn("w:anchor")) and state != "result":
                    raise ValueError("cached hyperlink outside the native Word index: " + name)
        instruction = "".join(code).strip()
        if (state != "after" or not references or not re.match(r"^TOC\s", instruction, re.I)
                or not re.search(outlines[name], instruction) or not re.search(r'\\h\b', instruction)):
            raise ValueError("missing or incorrect native Word index instruction: " + name)


def inspect_pair(doc, pdf, directory, pending_details=None, fill=False, *, pdf_directory=None):
    from docx.oxml.ns import qn
    from check_evidence import MODULE_VERSION, load_json

    version = doc.core_properties.version
    sections = [[]]
    front_nodes = []
    for p in doc.paragraphs:
        if p._p.xpath("./w:pPr/w:sectPr"):
            sections.append([])
        else:
            sections[-1].append(p.text.strip())
            if len(sections) == 2:
                front_nodes.append(p._p)
    cover, project = read_cover(doc)
    if len(sections) != 3 or not re.fullmatch(MODULE_VERSION, version) or cover.get("文件版本号") != version:
        raise ValueError("visible cover and formal Word version must match")
    for label in COVER_LABELS:
        if label not in cover:
            raise ValueError("missing cover field: " + label)
    front = sections[1]
    if ("修订记录" not in front or "目录" not in front or
            not any(s.split()[:1] == [version] for s in front[front.index("修订记录")+1:front.index("目录")])):
        raise ValueError("current revision record does not match the cover")
    pdf_cover = pdf.pages[0].extract_text()
    module = check_table_cover(doc, cover, project, pdf_cover) if project is not None else None
    if project is not None:
        check_native_indexes(doc, front_nodes)
    if not re.search(r"(?<!\w)" + re.escape(version) + r"(?!\w)", pdf_cover):
        raise ValueError("PDF cover is missing the current release version")

    fields = [f for f in doc.element.xpath(".//w:fldSimple") if f.get(qn("w:instr"), "").split()[:1] == ["PAGEREF"]]
    anchors = [f.get(qn("w:instr")).split()[1] for f in fields]
    bookmarks = set(doc.element.xpath(".//w:bookmarkStart/@w:name"))
    if not anchors or len(set(anchors)) != len(anchors) or not set(anchors) <= bookmarks:
        raise ValueError("missing or duplicate figure, table or heading bookmark")
    if any(s.page_width is None or abs(s.page_width.mm - 210) > 0.1 or
           s.page_height is None or abs(s.page_height.mm - 297) > 0.1 for s in doc.sections):
        raise ValueError("formal report requires A4 sections")
    if [s._sectPr.xpath("w:pgNumType/@w:fmt") for s in doc.sections] != [[], ["upperRoman"], ["decimal"]]:
        raise ValueError("cover/front matter/body page numbering is inconsistent")
    destinations = {name.lstrip("/"): pdf.get_destination_page_number(dest) for name, dest in pdf.named_destinations.items()}
    if any(destinations.get(anchor) is None for anchor in anchors):
        raise ValueError("PDF export is missing indexed Word bookmarks")
    body = min(destinations[a] for a in anchors)
    if body < 2:
        raise ValueError("PDF does not include a separate cover and front matter")
    contents = destinations.get("SVContents")
    indexes = [destinations.get(name) for name in ("SVTables", "SVFigures") if name in bookmarks]
    if project is not None and (contents is None or contents <= 1):
        raise ValueError("chapter contents must start on a page after the revision record")
    if contents is None or any(n is None or not contents < n < body for n in indexes) or indexes != sorted(indexes):
        raise ValueError("chapter contents must precede the separate table and figure indexes")
    numbers = [destinations[a] - body + 1 for a in anchors]
    body_count = len(pdf.pages) - body
    if destinations.get("SVEnd") != len(pdf.pages) - 1:
        raise ValueError("body page-count bookmark does not point to the last page")
    footer_ref = doc.sections[-1].footer._element.xpath(".//w:fldSimple[@w:instr='PAGEREF SVEnd']//w:t")
    if len(footer_ref) != 1:
        raise ValueError("missing body page-count field")
    if fill:
        footer_ref[0].text = str(body_count)
    elif footer_ref[0].text != str(body_count):
        raise ValueError("cached body page count differs from the rendered document")
    labels_by_page = {}
    for field, anchor, number in zip(fields, anchors, numbers):
        cache = list(field.iter(qn("w:t")))
        local = field.getparent().xpath("./w:hyperlink/@w:anchor")
        if len(cache) != 1 or local != [anchor]:
            raise ValueError("index label and page reference do not share a destination")
        if fill:
            cache[0].text = str(number)
        elif cache[0].text != str(number):
            raise ValueError("cached Word page differs from rendered destination")
        if project is not None:
            label = "".join(field.getparent().xpath("./w:hyperlink//w:t/text()"))
            labels_by_page.setdefault(destinations[anchor], []).append(label)
    indexed, pdf_links = [], set()
    for index, page in enumerate(pdf.pages):
        footer = []
        section = doc.sections[0 if index == 0 else 1 if index < body else 2]
        # Reference and archived documents have different footer distances.
        # Their text baseline lies between the footer and the body margin.
        footer_distance = section.footer_distance.pt if section.footer_distance is not None else 36
        bottom_margin = section.bottom_margin.pt if section.bottom_margin is not None else 72
        footer_top = (footer_distance + bottom_margin) / 2

        def visit(text, cm, tm, font, size):
            if text.strip() and 0 < tm[5]*cm[3] + cm[5] < footer_top:
                footer.append(text.strip())

        text = page.extract_text(visitor_text=visit)
        printed = re.sub(r"\s+", "", "".join(footer))
        if index < body:
            correct = printed == (roman(index).upper() if index else "")
        else:
            # Font subsets can be emitted separately; Word's field order and both printed numbers are checked.
            correct = (re.findall(r"\d+", printed) == [str(index - body + 1), str(body_count)]
                       and sorted(re.sub(r"\d+", "", printed)) == sorted("第页共页"))
        if not correct:
            raise ValueError(f"incorrect printed footer on PDF page {index+1}: {footer}")
        compact = "".join(text.split())
        for label in labels_by_page.get(index, []):
            if not label or "".join(label.split()) not in compact:
                raise ValueError(f"indexed heading or caption differs on PDF page {index+1}: {label}")
        if 0 < index < body:
            indexed.extend(int(n) for n in re.findall(r"\.{2,}\s*(\d+)", text))
        for ref in page.get("/Annots", []):
            action = ref.get_object().get("/A")
            if action and "/URI" in action:
                pdf_links.add(str(action["/URI"]))
    # Writer may use the native TOC's cached text in its first PDF. That pass
    # measures bookmark destinations; the final PDF must print every measured page.
    if not fill and indexed != numbers:
        raise ValueError("printed contents or figure/table page differs from PDF destination")
    pdf_targets = {relative_target(uri, pdf_directory or directory) for uri in pdf_links}
    for rel in doc.part.rels.values():
        if not rel.is_external:
            continue
        uri = rel.target_ref
        resolved = relative_target(uri, directory)
        if resolved != pending_details and not resolved.is_file():
            raise ValueError("missing evidence link: " + uri)
        if resolved not in pdf_targets:
            raise ValueError("PDF lost or rewrote a relative evidence link: " + uri)
    links = {}
    for item in doc.element.xpath(".//w:hyperlink[@r:id]"):
        label = "".join(t.text or "" for t in item.iter(qn("w:t")))
        uri = doc.part.rels[item.get(qn("r:id"))].target_ref
        links[label] = (directory / unquote(uri)).resolve()
    if not {"验证计划", "运行与观察", "完整明细"} <= set(links):
        raise ValueError("required plan, results or details link is missing")
    plan = load_json(links["验证计划"])
    report = plan.get("module_report", {})
    if report.get("version") != version:
        raise ValueError("linked plan version differs from the visible cover")
    if project is not None:
        from render_report import public_text
        # Rendering removes private absolute paths. Resolve the same display form
        # for a plan stored in its project root or any project subdirectory.
        for expected, printed in ((plan.get("project"), project), (report.get("module"), module)):
            if expected is not None and not any(public_text(expected, root) == printed for root in (None, *links["验证计划"].parents)):
                raise ValueError("linked plan project or module differs from the visible cover")
    details = links["完整明细"]
    if details.suffix.lower() != ".csv" or not details.stem.endswith("_" + version):
        raise ValueError("linked CSV filename differs from the visible cover")
    if details != pending_details:
        with details.open(encoding="utf-8-sig", newline="") as stream:
            rows = csv.DictReader(stream)
            if "report_version" not in (rows.fieldnames or []) or any(r["report_version"] != version for r in rows):
                raise ValueError("CSV contains a different release version")
    return {"pages": len(pdf.pages), "front_pages": body-1, "index_entries": len(anchors)}


def libraries():
    try:
        from docx import Document
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("rendered formal reports require python-docx and pypdf") from None
    return Document, PdfReader


def render_and_check(content, directory, soffice, pending_details):
    Document, PdfReader = libraries()
    directory.mkdir(parents=True, exist_ok=True)
    # PDF and DOCX must share the release directory so relative evidence URLs survive conversion.
    with tempfile.NamedTemporaryFile(prefix=".sv-layout-", suffix=".docx", dir=directory, delete=False) as handle:
        source = Path(handle.name)
        handle.write(content)
    pdf = source.with_suffix(".pdf")
    try:
        with tempfile.TemporaryDirectory(prefix=".sv-profile-", dir=directory) as profile:
            options = {key: {"type": "boolean", "value": "true"} for key in
                       ("ExportBookmarksToPDFDestination", "ExportLinksRelativeFsys")}
            command = [soffice, "-env:UserInstallation=" + Path(profile).resolve().as_uri(), "--headless",
                       "--convert-to", "pdf:writer_pdf_Export:" + json.dumps(options),
                       "--outdir", str(directory), str(source)]
            for first in (True, False):
                proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
                if proc.returncode or not pdf.is_file() or not pdf.stat().st_size:
                    raise ValueError("LibreOffice did not render a fresh PDF; check the executable and renderer environment")
                doc = Document(source)
                payload = restore_pdf_links(doc, pdf.read_bytes(), directory)
                inspect_pair(doc, PdfReader(io.BytesIO(payload)), directory, pending_details, fill=first)
                if first:
                    doc.save(source)
                    pdf.unlink()
            doc.core_properties.identifier = layout_identity(source.read_bytes(), payload)
            doc.save(source)
            return source.read_bytes(), payload
    except subprocess.TimeoutExpired:
        raise ValueError("timed out rendering the formal report") from None
    finally:
        source.unlink(missing_ok=True)
        pdf.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check a published formal DOCX, same-title PDF, cached numbers and evidence links.")
    parser.add_argument("word", type=Path)
    args = parser.parse_args(argv)
    try:
        from check_evidence import MODULE_VERSION
        Document, PdfReader = libraries()
        if args.word.suffix.lower() != ".docx":
            raise ValueError("expected the formal .docx")
        word_target = args.word.resolve(strict=True)
        pdf = args.word.with_suffix(".pdf")
        # Check any explicit PDF entry so stale or dangling links are reported.
        if args.word.is_symlink() and not (pdf.exists() or pdf.is_symlink()):
            pdf = word_target.with_suffix(".pdf")
        word = word_target.read_bytes()
        doc = Document(io.BytesIO(word))
        version = doc.core_properties.version
        if not re.fullmatch(MODULE_VERSION, version) or not word_target.stem.endswith("_" + version):
            raise ValueError("filename and formal Word version must match")
        payload = pdf.read_bytes()
        info = inspect_pair(doc, PdfReader(io.BytesIO(payload)), args.word.absolute().parent,
                            pdf_directory=pdf.absolute().parent)
        if doc.core_properties.identifier != layout_identity(word, payload):
            raise ValueError("Word content and PDF no longer match the checked render")
        print(json.dumps(info))
        return 0
    except (OSError, ValueError) as error:
        print("layout check failed:", error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
