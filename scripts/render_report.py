#!/usr/bin/env python3
"""Render a concise evaluated report plus lossless declared test-detail CSV."""
import argparse
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from check_evidence import BOUNDARY, EXIT_CODES, evaluate, load_inputs

DETAIL_FIELDS = ("requirement_ids", "requirement_sources", "case_id", "case_description", "variant_id", "parameters", "preconditions",
                 "stimulus", "observe_at", "check_id", "check_description", "expected", "oracle", "implementation",
                 "receiver", "role", "risk_activation", "activation_checks", "discriminating_checks", "normal_controls", "status", "actual",
                 "check_count", "run_id", "run_metadata", "log_paths")


def public_text(value, root=None):
    """Retain relative reproduction paths while removing absolute host paths."""
    def clean(item):
        if isinstance(item, dict):
            return {key: clean(child) for key, child in item.items()}
        if isinstance(item, list):
            return [clean(child) for child in item]
        if not isinstance(item, str):
            return item
        if root is not None:
            item = item.replace(str(Path(root).resolve()), "<project>")
        # Sanitize string leaves before JSON encoding, preserving quotes/escapes.
        quoted = r'''(["'])(?:[A-Za-z]:[\\/]|/(?=\S))[^\r\n]*?\1'''
        item = re.sub(quoted, lambda match: match[1] + "<absolute-path>" + match[1], item)
        # A standalone slash or numeric divisor is not a host path.
        pattern = r"(?<![\w/)\]>])(?:[A-Za-z]:[\\/][^\s,;，；\"'<>]+|/(?:[^\s,;，；\"'<>/]+/)+[^\s,;，；\"'<>]*|/[A-Za-z_.-][A-Za-z0-9_.-]*\.[A-Za-z0-9]+)"
        return re.sub(pattern, "<absolute-path>", item)
    if isinstance(value, (dict, list)):
        return json.dumps(clean(value), ensure_ascii=False, sort_keys=True)
    return clean("" if value is None else str(value))


def detail_rows(plan, results):
    sources = {req["id"]: req["source"] for req in plan["requirements"]}
    runs = {run["id"]: run for run in results["runs"]}
    observations = {}
    for observation in results["observations"]:
        observations.setdefault(observation["check_id"], []).append(observation)
    for case in plan["cases"]:
        risk = case.get("high_risk", {})
        for variant in case["variants"]:
            for cp in variant["checks"]:
                base = dict(requirement_ids=";".join(cp["requirements"]), requirement_sources={ref: sources[ref] for ref in cp["requirements"]}, case_id=case["id"], case_description=case["description"],
                            variant_id=variant["id"], parameters=variant["parameters"], preconditions=variant["preconditions"],
                            stimulus=variant["stimulus"], observe_at=variant["observe_at"], check_id=cp["id"],
                            check_description=cp["description"], expected=cp["expected"], oracle=cp["oracle"],
                            implementation=cp["implementation"], receiver=cp.get("receiver", ""), role=cp.get("role", ""),
                            risk_activation=risk.get("activation", ""), activation_checks=";".join(risk.get("activation_checks", [])), discriminating_checks=";".join(risk.get("discriminating_checks", [])),
                            normal_controls=";".join(risk.get("normal_controls", [])))
                for obs in observations.get(cp["id"], [{"status": "NOT_RUN", "actual": "", "checks": 0, "run_id": ""}]):
                    row = dict(base, status=obs["status"], actual=obs["actual"], check_count=obs["checks"], run_id=obs["run_id"])
                    run = runs.get(obs["run_id"], {})
                    row["run_metadata"] = run
                    row["log_paths"] = ";".join(record["path"] for record in run.get("logs", []))
                    yield row


def report_sections(plan, results, outcome, details_name):
    summary = outcome["summary"]
    conclusion = "声明范围内的检查与当前输入身份核对通过。" if outcome["status"] == "ACCEPTED" else "产品验收未完成，以下缺口仍需关闭。"
    sections = [("结论", [outcome["status"] + "。" + conclusion,
                         "必验需求 {required_requirements} 项；排除需求 {excluded_requirements} 项；有效通过检查点 {passed_checks}/{planned_checks} 个；当前运行 {current_runs} 次。".format(**summary)], [])]
    if outcome["blockers"]:
        sections.append(("验收缺口", outcome["blockers"], []))
    environment = [("配置", public_text(plan["configuration"]))]
    current = [run for run in results["runs"] if run["id"] in results["current_runs"]]
    if current:
        first = current[0]  # evaluate already requires a common final baseline.
        starts = [datetime.fromisoformat(run["started_at"].replace("Z", "+00:00")) for run in current]
        ends = [datetime.fromisoformat(run["ended_at"].replace("Z", "+00:00")) for run in current]
        environment.extend([
            ("工具版本", "; ".join(dict.fromkeys(run["tool"] for run in current))),
            ("基线", "plan " + first["plan_sha256"][:12] + "；inputs " + first["inputs_sha256"][:12] + "；config " + first["configuration_sha256"][:12]),
            ("运行范围", str(len(current)) + " 次；" + min(starts).astimezone(timezone.utc).isoformat() + " 至 " + max(ends).astimezone(timezone.utc).isoformat())])
    sections.append(("环境配置", ["逐次命令、种子、完整身份与日志索引见明细附件。"], [(('项目', '记录'), environment)]))
    rows = []
    for req in plan["requirements"]:
        checks = [cp for case in plan["cases"] for variant in case["variants"] for cp in variant["checks"] if req["id"] in cp["requirements"]]
        passed = sum(cp["id"] in summary["passed_check_ids"] for cp in checks)
        state = "已授权排除" if not req["required"] else str(passed) + "/" + str(len(checks)) + " CP PASS"
        rows.append((req["id"], req["description"], state))
    sections.append(("功能与协议", [], [(('需求', '内容', '检查结果'), rows)]))
    targets = []
    for target_field, result_field in (("coverage_targets", "coverage"), ("metrics", "measurements")):
        for target in plan[target_field]:
            measured = [str(m["value"]) + " " + m["unit"] + " (" + m["run_id"] + ")" for m in results[result_field] if m["target_id"] == target["id"]]
            targets.append((target["id"], target.get("kind", "coverage"), target["op"] + " " + str(target["target"]) + " " + target["unit"], "; ".join(measured) or "未实测"))
    condition_groups = {}
    for target in plan["coverage_targets"] + plan["metrics"]:
        condition_groups.setdefault(target["condition"], []).append(target["id"])
    conditions = [", ".join(ids) + "：" + condition for condition, ids in condition_groups.items()]
    sections.append(("覆盖 吞吐与延迟", conditions or ["本计划未声明量化目标；没有据此推断覆盖率或性能达标。"], [(('目标', '类别', '阈值', '实测与运行'), targets)] if targets else []))
    defects = []
    for defect in results.get("defects", []):
        detail = defect["id"] + " " + defect["status"] + "：" + defect["description"] + "；阻断验收=" + str(defect["blocking"])
        if defect["status"] == "FIXED":
            detail += "；当前回归 " + ", ".join(defect["regression_runs"])
        if defect["status"] == "WAIVED":
            detail += "；豁免 " + defect["waiver"]["reason"] + "；授权 " + defect["waiver"]["authorization"]
        defects.append(detail)
    for run in results["runs"]:
        if run["id"] not in results["current_runs"]:
            detail = "历史运行 " + run["id"] + "：" + run["status"] + "；输入 " + run["inputs_sha256"][:12] + "；不计入当前验收。"
            if "disposition" in run:
                detail += "处置：" + run["disposition"]["reason"] + "；当前回归 " + ", ".join(run["disposition"]["regression_runs"])
            defects.append(detail)
    sections.append(("缺陷修复与回归", defects or ["本结果文件未登记缺陷修复记录。"], []))
    limits = list(results.get("limitations", []))
    limits.extend(req["id"] + " 排除：" + req["exclusion"]["reason"] + "；授权：" + req["exclusion"]["authorization"] for req in plan["requirements"] if not req["required"])
    limits += [BOUNDARY, "完整明细：" + details_name + "。原始日志、计划和结果台账随证据包提供。"]
    sections.append(("范围与证据边界", limits, []))
    return sections


def markdown_text(title, sections, root):
    def cell(value):
        return public_text(value, root).replace("|", "\\|").replace("\n", "<br>")
    lines = ["# " + public_text(title, root), ""]
    for heading, paragraphs, tables in sections:
        lines += ["## " + heading, ""]
        for paragraph in paragraphs:
            lines += [public_text(paragraph, root), ""]
        for headers, rows in tables:
            lines += ["| " + " | ".join(map(cell, headers)) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
            lines += ["| " + " | ".join(map(cell, row)) + " |" for row in rows]
            lines += [""]
    return "\n".join(lines)


def word_bytes(title, sections, root):
    try:
        from docx import Document
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
        from docx.shared import Inches, Pt, RGBColor
    except ImportError:
        raise ValueError("Word export requires optional python-docx; no package was installed. Use .md or provide an environment with python-docx.") from None
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.27), Inches(11.69)
    section.top_margin = section.bottom_margin = Inches(0.7)
    section.left_margin = section.right_margin = Inches(0.75)
    for name, size in (("Normal", 11), ("Title", 20), ("Heading 1", 13.5), ("Heading 2", 12)):
        style = doc.styles[name]
        style.font.name = "Arial"
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.underline = False
        fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
        for key in list(fonts.attrib):
            if "theme" in key.lower():
                del fonts.attrib[key]
        fonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        # The bundled default template contains a blue Title paragraph border.
        for border in style.element.xpath("./w:pPr/w:pBdr"):
            border.getparent().remove(border)
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.line_spacing = 1.1
    doc.styles["Title"].paragraph_format.space_after = Pt(12)
    doc.styles["Heading 1"].paragraph_format.space_before = Pt(10)
    doc.core_properties.author = ""
    doc.core_properties.last_modified_by = ""
    doc.core_properties.title = public_text(title, root)
    doc.add_paragraph(public_text(title, root), "Title")
    for heading, paragraphs, tables in sections:
        doc.add_heading(heading, 1)
        for paragraph in paragraphs:
            doc.add_paragraph(public_text(paragraph, root))
        for headers, rows in tables:
            table = doc.add_table(rows=1, cols=len(headers))
            table.style = "Table Grid"
            table.autofit = False
            borders = OxmlElement("w:tblBorders")
            for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
                element = OxmlElement("w:" + edge)
                for key, value in (("val", "single"), ("sz", "4"), ("color", "B8C0C8")):
                    element.set(qn("w:" + key), value)
                borders.append(element)
            table._tbl.tblPr.append(borders)
            widths = {2: (1.25, 5.52), 3: (1.35, 3.82, 1.6), 4: (1.4, 1.05, 1.7, 2.62)}[len(headers)]
            for column, width in zip(table.columns, widths):
                column.width = Inches(width)
            for index, header in enumerate(headers):
                table.rows[0].cells[index].text = public_text(header, root)
            repeat = OxmlElement("w:tblHeader")
            table.rows[0]._tr.get_or_add_trPr().append(repeat)
            for row in rows:
                cells = table.add_row().cells
                for index, text in enumerate(row):
                    cells[index].text = public_text(text, root)
            for row_index, row in enumerate(table.rows):
                row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
                for index, cell in enumerate(row.cells):
                    cell.width = Inches(widths[index])
                    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                    if row_index == 0:
                        shading = OxmlElement("w:shd")
                        shading.set(qn("w:fill"), "EDF0F3")
                        cell._tc.get_or_add_tcPr().append(shading)
                    for paragraph in cell.paragraphs:
                        paragraph.paragraph_format.space_after = Pt(3)
                        paragraph.paragraph_format.space_before = Pt(3)
                        for run in paragraph.runs:
                            run.font.size = Pt(9)
                            run.bold = row_index == 0
            spacer = doc.add_paragraph()
            spacer.paragraph_format.space_after = Pt(0)
            spacer.paragraph_format.line_spacing = Pt(1)
    stream = io.BytesIO()
    doc.save(stream)
    return stream.getvalue()


def _atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate evaluated .md/.docx body and complete CSV details. Exit codes match check_evidence: 0 satisfied, 2 incomplete report written, 1 invalid/no report.")
    parser.add_argument("--plan", required=True, help="plan JSON, relative to --root")
    parser.add_argument("--results", required=True, help="results JSON, relative to --root")
    parser.add_argument("--root", help="current project root; omission prevents ACCEPTED")
    parser.add_argument("--output", required=True, help="report path ending .md or .docx (relative to current directory)")
    parser.add_argument("--details", help="CSV attachment path; default OUTPUT-STEM-details.csv")
    args = parser.parse_args(argv)
    try:
        output = Path(args.output)
        details = Path(args.details) if args.details else output.with_name(output.stem + "-details.csv")
        if output.suffix.lower() not in {".md", ".docx"} or details.suffix.lower() != ".csv":
            raise ValueError("output must be .md or .docx and details must be .csv")
        if output.resolve() == details.resolve():
            raise ValueError("report and details must have different output paths")
        plan, results = load_inputs(args.plan, args.results, args.root)
        outcome = evaluate(plan, results, args.root)
        if outcome["status"] == "INVALID":
            print(json.dumps(outcome, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        root = Path(args.root).resolve() if args.root else None
        input_paths = [(root / p).resolve() if root else Path(p).resolve() for p in (args.plan, args.results)]
        # With no verified root, use the plan directory only as a conservative write guard.
        # This does not establish an input baseline or upgrade the INCOMPLETE result.
        guard_root = root or input_paths[0].parent
        protected = [(guard_root / p).resolve() for p in plan["inputs"]["roots"]]
        protected_files = [(guard_root / record["path"]).resolve() for run in results["runs"] for record in run["logs"]]
        protected_files += input_paths
        for candidate in (output.resolve(), details.resolve()):
            if candidate in protected_files or any(candidate == p or p in candidate.parents for p in protected):
                raise ValueError("output must not replace inputs or evidence")
        title = plan["project"] + " 验证报告"
        sections = report_sections(plan, results, outcome, details.name)
        # Finish generation in memory before touching outputs, including dependency checks.
        body = markdown_text(title, sections, args.root).encode("utf-8") if output.suffix.lower() == ".md" else word_bytes(title, sections, args.root)
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=DETAIL_FIELDS)
        writer.writeheader()
        for row in detail_rows(plan, results):
            writer.writerow({key: public_text(value, args.root) for key, value in row.items()})
        _atomic_write(details, stream.getvalue().encode("utf-8-sig"))
        _atomic_write(output, body)
        print(json.dumps({"status": outcome["status"], "report": output.name, "details": details.name}, ensure_ascii=False))
        return EXIT_CODES[outcome["status"]]
    except (ValueError, TypeError) as exc:
        print("report error: " + public_text(exc, args.root), file=sys.stderr)
        return 1
    except OSError:
        print("report error: could not read or write the requested files", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
