"""Create an editable module report with indexed, numbered Word sections."""
import os
from pathlib import Path
from urllib.parse import quote
from zipfile import BadZipFile

from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.exceptions import PackageNotFoundError
from docx.shared import Mm, Pt, RGBColor, Twips


MODULE_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "templates" / "module.docx"


def report_title(info):
    return info.get("title") or info["module"] + "模块验证文档"


def load_module_template():
    from docx import Document
    from verify_layout import COVER_LABELS, read_cover

    if not MODULE_TEMPLATE.is_file():
        raise ValueError("formal module Word template is missing: assets/templates/module.docx")
    try:
        doc = Document(MODULE_TEMPLATE)
    except (BadZipFile, KeyError, PackageNotFoundError, ValueError) as exc:
        raise ValueError("invalid formal module Word template: assets/templates/module.docx") from exc
    if len(doc.sections) != 3 or not doc.tables:
        raise ValueError("formal module Word template requires a table cover and three sections")
    fields, project = read_cover(doc)
    tokens = ("{{TITLE}}", "{{DOCUMENT_NUMBER}}", "{{VERSION}}", "{{TASK_NUMBER}}", "{{DATE}}")
    if project != "{{PROJECT}}" or fields != dict(zip(COVER_LABELS, tokens)):
        raise ValueError("formal module Word template has misplaced or missing cover fields")
    styles = ("Normal", "Heading 1", "Heading 2", "Heading 3", "TOC 标题1", "toc 1", "toc 2",
              "table of figures", "Caption", "插图位置", "PLM table", "表格正文")
    for name in styles:
        if name not in doc.styles:
            raise ValueError("missing formal module Word template style: " + name)
    return doc


def prepare_template(doc, info):
    """Retain the real cover, styles, headers and three original section definitions."""
    body = doc.element.body
    breaks = [p for p in body.xpath("./w:p") if p.xpath("./w:pPr/w:sectPr")]
    final_section = body.sectPr
    if len(breaks) != 2 or final_section is None:
        raise ValueError("module template must have cover, front matter and body sections")
    cover_end, front_end = breaks
    tokens = {"{{PROJECT}}": info["project"], "{{MODULE}}": info["module"],
              "{{TITLE}}": report_title(info), "{{VERSION}}": info["version"],
              "{{DATE}}": info["date"], "{{DOCUMENT_NUMBER}}": info["document_number"],
              "{{TASK_NUMBER}}": info["task_number"]}
    covered = set()

    def fill(nodes):
        for node in nodes:
            if node.text in tokens:
                value = node.text
                node.text = tokens[value]
                covered.add(value)

    children = list(body)
    for node in children[:children.index(cover_end)]:
        fill(node.xpath(".//w:t"))
    for rel in doc.part.rels.values():
        if rel.reltype == RT.HEADER:
            fill(rel.target_part.element.xpath(".//w:t"))
    if covered != tokens.keys():
        raise ValueError("missing module template cover/header fields: " + ", ".join(sorted(tokens.keys()-covered)))
    # Keep the first boundary in place. Reinsert the original second boundary
    # after the generated revision/contents and before the final body section.
    for node in children[children.index(cover_end)+1:]:
        if node is not final_section:
            body.remove(node)
    for node in list(front_end):
        if node.tag != qn("w:pPr"):
            front_end.remove(node)
    return front_end


def field(paragraph, instruction, cached):
    node = OxmlElement("w:fldSimple")
    node.set(qn("w:instr"), instruction)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = cached
    run.append(text)
    node.append(run)
    paragraph._p.append(node)


def link(paragraph, label, *, anchor=None, target=None):
    element = OxmlElement("w:hyperlink")
    if anchor:
        element.set(qn("w:anchor"), anchor)
    else:
        rid = paragraph.part.relate_to(target, RT.HYPERLINK, is_external=True)
        element.set(qn("r:id"), rid)
    run = paragraph.add_run(label)
    run.font.color.rgb = RGBColor(0, 0, 0)
    if target:
        run.underline = True
    element.append(run._r)
    paragraph._p.append(element)


def numbered_section(doc, fmt, example, module):
    section = doc.add_section(WD_SECTION.NEW_PAGE)
    page = OxmlElement("w:pgNumType")
    page.set(qn("w:fmt"), fmt)
    page.set(qn("w:start"), "1")
    for old in section._sectPr.xpath("./w:pgNumType"):
        section._sectPr.remove(old)
    section._sectPr.insert_element_before(page, "w:cols", "w:docGrid")
    section.footer.is_linked_to_previous = False
    section.header.is_linked_to_previous = False
    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    header.add_run(module)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if fmt == "decimal":
        footer.add_run("第 ")
        field(footer, "PAGE", example)
        footer.add_run(" 页 共 ")
        field(footer, "PAGEREF SVEnd", "?")
        footer.add_run(" 页")
    else:
        field(footer, "PAGE", example)
    return section


class FormalLayout:
    def __init__(self, doc, info):
        self.doc = doc
        self.info = info
        self.anchors = []
        self.bookmark_id = 0
        self.major = self.minor = self.num_figures = self.num_tables = 0
        self.from_template = len(doc.sections) == 3
        front_end = None
        self.index_starts = {}
        if self.from_template:
            # Keep the template's three native Word index instructions. Their
            # sample result rows will be replaced with this report's entries.
            instructions = doc.element.body.xpath('./w:p/w:r[w:instrText[starts-with(normalize-space(.), "TOC ")]]')
            if len(instructions) != 3:
                raise ValueError("module template requires chapter, table and figure index fields")
            for group, run in zip(("目录", "表目录", "图目录"), instructions):
                self.index_starts[group] = (run.getprevious(), run, run.getnext())
            front_end = prepare_template(doc, info)
        else:
            section = doc.sections[0]
            section.page_height, section.page_width = Mm(297), Mm(210)
            section.top_margin, section.left_margin = Twips(1644), Twips(1418)
            section.bottom_margin, section.right_margin = Twips(1418), Twips(1134)
            section.footer_distance = Mm(10)
            doc.styles["Normal"].paragraph_format.space_after = Pt(4)
            doc.styles["Normal"].paragraph_format.line_spacing = 1.05
            for name in ("Normal", "Title", "Heading 1", "Heading 2", "Header", "Footer"):
                style = doc.styles[name]
                style.font.name = "Times New Roman"
                fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
                for key in list(fonts.attrib):
                    if "theme" in key.lower():
                        del fonts.attrib[key]
                fonts.set(qn("w:eastAsia"), "黑体" if name in ("Title", "Heading 1", "Heading 2") else "宋体")
            title = report_title(info)
            self.cover_line(info["project"], 22, 65)
            self.cover_line(title, 29, 15)
            for index, (label, value) in enumerate((
                ("文件名称", title), ("文件编号", info["document_number"]), ("文件版本号", info["version"]),
                ("项目任务号", info["task_number"]), ("编制日期", info["date"]))):
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Mm(22)
                p.paragraph_format.tab_stops.add_tab_stop(Mm(57))
                p.paragraph_format.space_before = Pt(40 if index == 0 else 8)
                p.add_run(label)
                p.add_run("\t" + value)
            numbered_section(doc, "upperRoman", "I", info["module"])
        section = doc.sections[0]
        self.width = section.page_width - section.left_margin - section.right_margin
        doc.core_properties.version = info["version"]
        doc.core_properties.subject = info["module"]
        doc.core_properties.title = report_title(info)
        index_title = "TOC 标题1" if self.from_template else "Title"
        doc.add_paragraph("修订记录", index_title)
        doc.add_paragraph(info["version"] + "　" + info["revision"])
        self.front = {}
        groups = [("目录", "SVContents")]
        if info["has_tables"]:
            groups.append(("表目录", "SVTables"))
        if info["has_figures"]:
            groups.append(("图目录", "SVFigures"))
        for index, (group, name) in enumerate(groups):
            title = doc.add_paragraph(group, index_title)
            if index == 1 or (self.from_template and index == 0):
                title.paragraph_format.page_break_before = True
            self.mark(title, name)
            end = doc.add_paragraph()
            end.paragraph_format.line_spacing = Pt(1)
            end.paragraph_format.space_after = Pt(0)
            self.front[group] = end
        if front_end is None:
            numbered_section(doc, "decimal", "1", info["module"])
        else:
            doc.element.body.sectPr.addprevious(front_end)

    def cover_line(self, text, size, before):
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(before)
        run = p.add_run(text)
        run.font.size = Pt(size)
        run.bold = size > 20

    def mark(self, paragraph, anchor):
        self.bookmark_id += 1
        start = OxmlElement("w:bookmarkStart")
        start.set(qn("w:id"), str(self.bookmark_id))
        start.set(qn("w:name"), anchor)
        paragraph._p.insert(1 if paragraph._p.pPr is not None else 0, start)
        end = OxmlElement("w:bookmarkEnd")
        end.set(qn("w:id"), str(self.bookmark_id))
        paragraph._p.append(end)

    def add_entry(self, paragraph, group, label, level=1):
        anchor = "SV" + str(len(self.anchors) + 1)
        self.anchors.append(anchor)
        self.mark(paragraph, anchor)
        item = self.front[group].insert_paragraph_before()
        if self.from_template:
            item.style = "toc " + str(level) if group == "目录" else "table of figures"
            for run in self.index_starts.pop(group, ()):
                item._p.append(run)
        else:
            item.paragraph_format.space_after = Pt(2)
            item.paragraph_format.line_spacing = 1.05
            item.paragraph_format.left_indent = Mm(5 * (level - 1))
            item.paragraph_format.tab_stops.add_tab_stop(self.width - Mm(1), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        link(item, label, anchor=anchor)
        item.add_run("\t")
        field(item, "PAGEREF " + anchor + r" \h", "?")

    def heading(self, name, level):
        if level == 1:
            self.major += 1
            self.minor = 0
            self.num_figures = self.num_tables = 0
            number = str(self.major)
        else:
            self.minor += 1
            number = f"{self.major}.{self.minor}"
        label = number + "　" + name
        self.add_entry(self.doc.add_heading(name if self.from_template else label, level), "目录", label, level)

    def caption(self, kind, label):
        if kind == "图":
            self.num_figures += 1
            number = self.num_figures
        else:
            self.num_tables += 1
            number = self.num_tables
        title = f"{kind} {self.major}-{number}　{label}"
        p = self.doc.add_paragraph(style="Caption" if self.from_template else None)
        p.paragraph_format.keep_with_next = True
        if self.from_template:
            p.paragraph_format.space_before = p.paragraph_format.space_after = Twips(114)
            p.add_run(kind + " ")
            field(p, r"STYLEREF 1 \n", str(self.major))
            p.add_run("-")
            # Writer keeps SEQ numbers across chapters even when given Word's
            # heading reset switch. Reset each chapter's first item explicitly.
            switch = r"\r 1" if number == 1 else r"\s 1"
            field(p, "SEQ " + kind + r" \* ARABIC " + switch, str(number))
            p.add_run("　" + label)
        else:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(3)
            run = p.add_run(title)
            run.bold = True
            run.font.size = Pt(10)
        self.add_entry(p, kind + "目录", title)

    def evidence(self, pairs, paragraph=None):
        p = paragraph if paragraph is not None else self.doc.add_paragraph()
        if paragraph is not None:
            p.add_run("　")
        for index, (label, path) in enumerate(pairs):
            if index:
                p.add_run("　")
            directory = self.info["output_dir"]
            root = self.info["root"]
            if path.is_relative_to(root) and directory.is_relative_to(root):
                # Both docs/ and its sibling latest/ resolve the path through the package root.
                relative = os.path.join(os.path.relpath(root, directory), path.relative_to(root))
            else:
                relative = os.path.relpath(path, directory)
            target = quote(relative.replace(os.sep, "/"), safe="/")
            link(p, label, target=target)
        p.paragraph_format.space_after = Pt(6)
        return p

    def finish(self):
        if self.from_template:
            for spacer in self.front.values():
                last_entry = spacer._p.getprevious()
                if last_entry.xpath('./w:fldSimple'):
                    marker = OxmlElement("w:fldChar")
                    marker.set(qn("w:fldCharType"), "end")
                    last_entry.add_r().append(marker)
        self.mark(self.evidence(self.info["links"]), "SVEnd")
