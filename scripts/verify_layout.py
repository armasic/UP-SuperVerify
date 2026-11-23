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


def inspect_pair(doc, pdf, directory, pending_details=None, fill=False):
    from docx.oxml.ns import qn
    from check_evidence import MODULE_VERSION, load_json

    version = doc.core_properties.version
    sections = [[]]
    for p in doc.paragraphs:
        if p._p.xpath("./w:pPr/w:sectPr"):
            sections.append([])
        else:
            sections[-1].append(p.text.strip())
    cover = dict(line.split("\t", 1) for line in sections[0] if "\t" in line)
    if len(sections) != 3 or not re.fullmatch(MODULE_VERSION, version) or cover.get("文件版本号") != version:
        raise ValueError("visible cover and formal Word version must match")
    for label in ("文件名称", "文件编号", "项目任务号", "编制日期"):
        if not any(line.split("\t")[0] == label for line in sections[0]):
            raise ValueError("missing cover field: " + label)
    front = sections[1]
    if "修订记录" not in front or not any(s.split()[:1] == [version] for s in front[front.index("修订记录")+1:front.index("目录")]):
        raise ValueError("current revision record does not match the cover")
    if not re.search(r"(?<!\w)" + re.escape(version) + r"(?!\w)", pdf.pages[0].extract_text()):
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
    for field, number in zip(fields, numbers):
        cache = list(field.iter(qn("w:t")))
        local = field.getparent().xpath("./w:hyperlink/@w:anchor")
        if len(cache) != 1 or local != [field.get(qn("w:instr")).split()[1]]:
            raise ValueError("index label and page reference do not share a destination")
        if fill:
            cache[0].text = str(number)
        elif cache[0].text != str(number):
            raise ValueError("cached Word page differs from rendered destination")
    indexed, pdf_links = [], set()
    for index, page in enumerate(pdf.pages):
        footer = []

        def visit(text, cm, tm, font, size):
            if text.strip() and 0 < tm[5]*cm[3] + cm[5] < 42:
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
        if 0 < index < body:
            indexed.extend(int(n) for n in re.findall(r"\.{2,}\s*(\d+)", text))
        for ref in page.get("/Annots", []):
            action = ref.get_object().get("/A")
            if action and "/URI" in action:
                pdf_links.add(str(action["/URI"]))
    if indexed != numbers:
        raise ValueError("printed contents or figure/table page differs from PDF destination")
    pdf_targets = {relative_target(uri, directory) for uri in pdf_links}
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
    if load_json(links["验证计划"]).get("module_report", {}).get("version") != version:
        raise ValueError("linked plan version differs from the visible cover")
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
        pdf = args.word.with_suffix(".pdf")
        word = args.word.read_bytes()
        doc = Document(io.BytesIO(word))
        version = doc.core_properties.version
        if not re.fullmatch(MODULE_VERSION, version) or not args.word.resolve().stem.endswith("_" + version):
            raise ValueError("filename and formal Word version must match")
        payload = pdf.read_bytes()
        info = inspect_pair(doc, PdfReader(io.BytesIO(payload)), args.word.absolute().parent)
        if doc.core_properties.identifier != layout_identity(word, payload):
            raise ValueError("Word content and PDF no longer match the checked render")
        print(json.dumps(info))
        return 0
    except (OSError, ValueError) as error:
        print("layout check failed:", error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
