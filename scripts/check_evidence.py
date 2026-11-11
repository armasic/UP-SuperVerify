#!/usr/bin/env python3
"""Check declared verification scope and evidence identity; never infer product completeness."""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys

STATUSES = ("PASS", "FAIL", "BLOCKED", "SKIP", "NOT_RUN", "ERROR", "TIMEOUT")
EXIT_CODES = {"ACCEPTED": 0, "INCOMPLETE": 2, "INVALID": 1, "PLAN_VALID": 0}
BOUNDARY = "自动检查仅核对声明范围、结构和文件身份；需求完整性、判据独立性及正确性、排除授权真实性仍须语义审阅。"
UNKNOWN = {"", "UNKNOWN", "TBD", "TODO", "UNDEFINED", "待确认", "未知"}


def load_json(path):
    """Read strict JSON, rejecting duplicate object keys and non-finite numbers."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("non-finite JSON number: " + value)

    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=pairs, parse_constant=nonfinite)


def canonical_sha256(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(p not in {"", ".", ".."} for p in value.split("/"))


def resolve_inside(root, value):
    """Resolve a declared relative path without permitting traversal or symlink escape."""
    if not safe_relative(value):
        raise ValueError("unsafe relative path")
    path = root / value
    try:
        path.resolve(strict=True).relative_to(root)
    except (ValueError, OSError, RuntimeError):
        raise ValueError("missing path or path outside root: " + value) from None
    # Refuse symlinks even when internal: avoids directory loops and unstable manifests.
    for part in [path] + list(path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError("symlinks are not evidence paths: " + value)
    return path


class Validation:
    def __init__(self):
        self.errors = []
        self.ids = set()

    def error(self, where, message):
        self.errors.append(where + ": " + message)

    def obj(self, value, where, required=(), optional=()):
        if not isinstance(value, dict):
            self.error(where, "must be an object")
            return {}
        for key in required:
            if key not in value:
                self.error(where, "missing " + key)
        for key in value:
            if key not in set(required) | set(optional):
                self.error(where, "unknown field " + str(key))
        return value

    def array(self, value, where, nonempty=False):
        if not isinstance(value, list):
            self.error(where, "must be an array")
            return []
        if nonempty and not value:
            self.error(where, "must not be empty")
        return value

    def text(self, value, where):
        if not isinstance(value, str) or not value.strip():
            self.error(where, "must be nonempty text")
            return False
        return True

    def identifier(self, value, where):
        if not self.text(value, where):
            return
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value):
            self.error(where, "ID must use ASCII letters, digits, _, ., :, -")
        if value in self.ids:
            self.error(where, "duplicate ID " + value)
        self.ids.add(value)

    def strings(self, value, where, nonempty=False):
        items = self.array(value, where, nonempty)
        found = set()
        for item in items:
            if self.text(item, where):
                if item in found:
                    self.error(where, "duplicate reference " + item)
                found.add(item)
        return found

    def sha(self, value, where):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            self.error(where, "must be lowercase SHA-256")

    def number(self, value, where, integer=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
            self.error(where, "must be a finite number")
        elif integer and (not isinstance(value, int) or value < 0):
            self.error(where, "must be a nonnegative integer")


def _file_records(v, records, where, nonempty=True):
    paths = set()
    for index, record in enumerate(v.array(records, where, nonempty)):
        loc = where + "[" + str(index) + "]"
        record = v.obj(record, loc, ("path", "sha256"))
        path = record.get("path")
        if not safe_relative(path):
            v.error(loc, "path must be a safe relative POSIX path")
        elif path in paths:
            v.error(loc, "duplicate file path " + path)
        else:
            paths.add(path)
        v.sha(record.get("sha256"), loc + ".sha256")
    return paths


def validate_plan(plan):
    """Return structural errors. Missing execution/unknown expectations are blockers, not syntax errors."""
    v = Validation()
    plan = v.obj(plan, "plan", ("schema_version", "project", "inputs", "configuration", "requirements", "cases", "coverage_targets", "metrics"))
    if type(plan.get("schema_version")) is not int or plan.get("schema_version") != 1:
        v.error("plan", "unsupported schema_version")
    v.text(plan.get("project"), "plan.project")
    if not isinstance(plan.get("configuration"), dict):
        v.error("plan.configuration", "must be an object")
    inputs = v.obj(plan.get("inputs"), "inputs", ("roots", "files"))
    roots = v.strings(inputs.get("roots"), "inputs.roots")
    for root in roots:
        if not safe_relative(root):
            v.error("inputs.roots", "unsafe root path")
        for other in roots:
            if root != other and root.startswith(other + "/"):
                v.error("inputs.roots", "overlapping roots")
    paths = _file_records(v, inputs.get("files"), "inputs.files", False)
    for path in paths:
        if not any(path.startswith(root + "/") for root in roots):
            v.error("inputs.files", "file outside declared input roots: " + path)
    requirement_ids = set()
    for req in v.array(plan.get("requirements"), "requirements", True):
        req = v.obj(req, "requirement", ("id", "description", "source", "required"), ("exclusion",))
        v.identifier(req.get("id"), "requirement.id")
        for field in ("description", "source"):
            v.text(req.get(field), "requirement." + field)
        if isinstance(req.get("id"), str):
            requirement_ids.add(req["id"])
        if type(req.get("required")) is not bool:
            v.error("requirement.required", "must be boolean")
        if req.get("required") is False:
            exclusion = v.obj(req.get("exclusion"), "exclusion", ("reason", "authorization"))
            for field in ("reason", "authorization"):
                v.text(exclusion.get(field), "exclusion." + field)
        elif "exclusion" in req:
            v.error("requirement", "exclusion requires required=false")
    for case in v.array(plan.get("cases"), "cases"):
        case = v.obj(case, "case", ("id", "description", "requirements", "variants"), ("isolation", "high_risk"))
        v.identifier(case.get("id"), "case.id")
        v.text(case.get("description"), "case.description")
        for ref in v.strings(case.get("requirements"), "case.requirements", True):
            if ref not in requirement_ids:
                v.error("case.requirements", "unknown requirement " + ref)
        variants, checks, roles = {}, {}, {}
        for variant in v.array(case.get("variants"), "case.variants", True):
            variant = v.obj(variant, "variant", ("id", "parameters", "preconditions", "stimulus", "observe_at", "checks"))
            v.identifier(variant.get("id"), "variant.id")
            vid = variant.get("id")
            if isinstance(vid, str):
                variants[vid] = variant
                roles[vid] = set()
            if not isinstance(variant.get("parameters"), dict):
                v.error("variant.parameters", "must be a concrete parameter object")
            for field in ("preconditions", "stimulus", "observe_at"):
                v.text(variant.get(field), "variant." + field)
            for cp in v.array(variant.get("checks"), "variant.checks", True):
                cp = v.obj(cp, "check", ("id", "description", "requirements", "expected", "oracle", "implementation"), ("receiver", "role"))
                v.identifier(cp.get("id"), "check.id")
                case_refs = set(r for r in case.get("requirements", []) if isinstance(r, str)) if isinstance(case.get("requirements"), list) else set()
                for ref in v.strings(cp.get("requirements"), "check.requirements", True):
                    if ref not in case_refs:
                        v.error("check.requirements", "must reference a requirement of this case: " + ref)
                for field in ("description", "oracle"):
                    v.text(cp.get(field), "check." + field)
                if cp.get("expected") is not None and not isinstance(cp.get("expected"), str):
                    v.error("check.expected", "must be text or null for unresolved expectation")
                implementation = cp.get("implementation")
                if implementation is not None:
                    if not isinstance(implementation, str) or "#" not in implementation or not implementation.split("#", 1)[1]:
                        v.error("check.implementation", "must be input/path#checker or null")
                    elif implementation.split("#", 1)[0] not in paths:
                        v.error("check.implementation", "file absent from input manifest")
                if isinstance(cp.get("id"), str):
                    checks[cp["id"]] = vid if isinstance(vid, str) else None
                if "receiver" in cp or "role" in cp:
                    v.text(cp.get("receiver"), "check.receiver")
                    if cp.get("role") not in ("selected", "unselected"):
                        v.error("check.role", "must be selected or unselected")
                    if isinstance(vid, str) and isinstance(cp.get("receiver"), str) and isinstance(cp.get("role"), str):
                        roles[vid].add((cp["receiver"], cp["role"]))
        controls = set()
        if "high_risk" in case:
            risk = v.obj(case["high_risk"], "high_risk", ("activation", "activation_checks", "discriminating_checks", "normal_controls"))
            v.text(risk.get("activation"), "high_risk.activation")
            controls = v.strings(risk.get("normal_controls"), "high_risk.normal_controls", True)
            discriminators = v.strings(risk.get("discriminating_checks"), "high_risk.discriminating_checks", True)
            for ref in controls:
                if ref not in variants:
                    v.error("high_risk", "unknown normal control variant " + ref)
            activators = v.strings(risk.get("activation_checks"), "high_risk.activation_checks", True)
            for ref in activators | discriminators:
                if ref not in checks:
                    v.error("high_risk", "unknown discriminating check " + ref)
                elif checks[ref] in controls:
                    v.error("high_risk", "discriminator must differ from normal control")
        if "isolation" in case:
            isolation = v.obj(case["isolation"], "isolation", ("selected", "unselected"))
            selected = v.strings(isolation.get("selected"), "isolation.selected", True)
            unselected = v.strings(isolation.get("unselected"), "isolation.unselected", True)
            if selected & unselected:
                v.error("isolation", "selected/unselected receivers overlap")
            needed = {(r, "selected") for r in selected} | {(r, "unselected") for r in unselected}
            for vid in variants:
                if not needed.issubset(roles[vid]):
                    v.error("isolation", "variant " + vid + " lacks independent selected/unselected checks")
    for field in ("coverage_targets", "metrics"):
        for target in v.array(plan.get(field), field):
            required = ("id", "description", "unit", "condition", "op", "target")
            target = v.obj(target, field, required + (("kind",) if field == "metrics" else ()))
            v.identifier(target.get("id"), field + ".id")
            for name in ("description", "unit", "condition"):
                v.text(target.get(name), field + "." + name)
            if target.get("op") not in (">=", "<=", "==", ">", "<"):
                v.error(field, "unsupported comparison")
            v.number(target.get("target"), field + ".target")
            if field == "metrics" and target.get("kind") not in ("throughput", "latency"):
                v.error(field, "kind must be throughput or latency")
    try:
        canonical_sha256(plan)
    except (ValueError, TypeError, OverflowError):
        v.error("plan", "must contain only finite JSON values")
    return v.errors


def plan_blockers(plan):
    blockers = []
    mapped = {ref for case in plan["cases"] for variant in case["variants"] for cp in variant["checks"] for ref in cp["requirements"]}
    if not plan["inputs"]["roots"] or not plan["inputs"]["files"]:
        blockers.append("input baseline is not established")
    for req in plan["requirements"]:
        if req["required"] and req["id"] not in mapped:
            blockers.append("required requirement has no case/check: " + req["id"])
    for case in plan["cases"]:
        for variant in case["variants"]:
            for cp in variant["checks"]:
                if cp["expected"] is None or cp["expected"].strip().upper() in UNKNOWN:
                    blockers.append("unresolved expectation: " + cp["id"])
                if cp["implementation"] is None:
                    blockers.append("check implementation missing: " + cp["id"])
    return blockers


def _outcome(errors, blockers, summary):
    return {"status": "INVALID" if errors else "INCOMPLETE" if blockers else "ACCEPTED", "errors": errors,
            "blockers": blockers, "summary": summary, "boundary": BOUNDARY}


def _verify_files(plan, runs, root, errors):
    try:
        root = Path(root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("root must be an existing directory")
    except (OSError, ValueError, TypeError, RuntimeError):
        errors.append("root must be an existing directory")
        return
    found = set()
    for relative in plan["inputs"]["roots"]:
        try:
            directory = resolve_inside(root, relative)
            if not directory.is_dir():
                raise ValueError("input root is not a directory: " + relative)
            for path in directory.rglob("*"):
                rel = path.relative_to(root).as_posix()
                checked = resolve_inside(root, rel)
                if checked.is_file():
                    found.add(rel)
                elif not checked.is_dir():
                    raise ValueError("input is not a regular file: " + rel)
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(str(exc) if isinstance(exc, ValueError) else "cannot scan input root: " + relative)
    expected = {record["path"] for record in plan["inputs"]["files"]}
    if found != expected:
        errors.append("current input file manifest differs (missing=" + ",".join(sorted(expected - found)) + "; added=" + ",".join(sorted(found - expected)) + ")")
    records = plan["inputs"]["files"] + [record for run in runs for record in run["logs"]]
    for record in records:
        try:
            path = resolve_inside(root, record["path"])
            if not path.is_file():
                raise ValueError("not a regular evidence file: " + record["path"])
            if file_sha256(path) != record["sha256"]:
                errors.append("file hash mismatch: " + record["path"])
        except (ValueError, OSError, RuntimeError) as exc:
            errors.append(str(exc) if isinstance(exc, ValueError) else "cannot read evidence file: " + record["path"])


def evaluate(plan, results, root=None):
    """Evaluate only explicitly current runs against this plan and current input files."""
    errors = validate_plan(plan)
    summary = {"required_requirements": 0, "excluded_requirements": 0, "planned_checks": 0, "passed_checks": 0, "passed_check_ids": [], "current_runs": 0}
    if errors:
        return _outcome(errors, [], summary)
    blockers = plan_blockers(plan)
    summary.update(required_requirements=sum(r["required"] for r in plan["requirements"]),
                   excluded_requirements=sum(not r["required"] for r in plan["requirements"]))
    cps = {cp["id"]: (variant["id"], cp) for case in plan["cases"] for variant in case["variants"] for cp in variant["checks"]}
    summary["planned_checks"] = len(cps)
    v = Validation()
    results = v.obj(results, "results", ("schema_version", "current_runs", "runs", "observations", "coverage", "measurements"), ("summary", "defects", "limitations"))
    if type(results.get("schema_version")) is not int or results.get("schema_version") != 1:
        v.error("results", "unsupported schema_version")
    current = v.strings(results.get("current_runs"), "results.current_runs")
    runs = {}
    for run in v.array(results.get("runs"), "runs"):
        run = v.obj(run, "run", ("id", "plan_sha256", "inputs_sha256", "configuration_sha256", "status", "exit_code", "checks", "errors", "timeouts", "tool", "command", "seed", "started_at", "ended_at", "logs"), ("disposition",))
        v.identifier(run.get("id"), "run.id")
        for key in ("plan_sha256", "inputs_sha256", "configuration_sha256"):
            v.sha(run.get(key), "run." + key)
        if run.get("status") not in STATUSES:
            v.error("run.status", "unknown status")
        if type(run.get("exit_code")) is not int:
            v.error("run.exit_code", "must be an integer process exit status")
        for key in ("checks", "errors", "timeouts"):
            v.number(run.get(key), "run." + key, True)
        for key in ("tool", "command", "seed", "started_at", "ended_at"):
            v.text(run.get(key), "run." + key)
        try:
            start, end = [datetime.fromisoformat(run[key].replace("Z", "+00:00")) for key in ("started_at", "ended_at")]
            if start.utcoffset() is None or end.utcoffset() is None or end < start:
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            v.error("run", "timestamps must include timezone and end must not precede start")
        _file_records(v, run.get("logs"), "run.logs")
        if isinstance(run.get("id"), str):
            runs[run["id"]] = run
    for ref in current:
        if ref not in runs:
            v.error("current_runs", "unknown run " + ref)
    for rid, run in runs.items():
        if "disposition" in run:
            disposition = v.obj(run["disposition"], "run.disposition", ("reason", "regression_runs"))
            v.text(disposition.get("reason"), "run.disposition.reason")
            if rid in current:
                v.error("run.disposition", "only omitted historical runs can have a disposition")
            for ref in v.strings(disposition.get("regression_runs"), "run.disposition.regression_runs", True):
                if ref not in current:
                    v.error("run.disposition", "regression must reference a current run")
    observations = v.array(results.get("observations"), "observations")
    pairs = set()
    for observation in observations:
        obs = v.obj(observation, "observation", ("check_id", "variant_id", "run_id", "status", "checks", "actual"))
        for key in ("check_id", "variant_id", "run_id"):
            v.text(obs.get(key), "observation." + key)
        cid, rid = obs.get("check_id"), obs.get("run_id")
        if isinstance(cid, str) and cid in cps:
            if obs.get("variant_id") != cps[cid][0]:
                v.error("observation", "check belongs to a different variant: " + cid)
        else:
            v.error("observation", "unknown check")
        if not isinstance(rid, str) or rid not in runs:
            v.error("observation", "unknown run")
        elif rid not in current:
            v.error("observation", "observations must reference a current run")
        if isinstance(cid, str) and isinstance(rid, str):
            if (cid, rid) in pairs:
                v.error("observation", "duplicate check/run result")
            pairs.add((cid, rid))
        if obs.get("status") not in STATUSES:
            v.error("observation", "unknown status")
        v.number(obs.get("checks"), "observation.checks", True)
        v.text(obs.get("actual"), "observation.actual")
    for field, target_field in (("coverage", "coverage_targets"), ("measurements", "metrics")):
        targets = {t["id"]: t for t in plan[target_field]}
        seen = set()
        for item in v.array(results.get(field), field):
            item = v.obj(item, field, ("target_id", "run_id", "value", "unit", "condition"))
            tid, rid = item.get("target_id"), item.get("run_id")
            if not isinstance(tid, str) or tid not in targets:
                v.error(field, "unknown target")
            else:
                for key in ("unit", "condition"):
                    if item.get(key) != targets[tid][key]:
                        v.error(field, "target " + tid + " has mismatched " + key)
            if not isinstance(rid, str) or rid not in current:
                v.error(field, "measurement must reference a current run")
            if isinstance(tid, str) and isinstance(rid, str):
                if (tid, rid) in seen:
                    v.error(field, "duplicate target/run measurement")
                seen.add((tid, rid))
            v.number(item.get("value"), field + ".value")
    for defect in v.array(results.get("defects", []), "defects"):
        defect = v.obj(defect, "defect", ("id", "status", "blocking", "description"), ("regression_runs", "waiver"))
        v.identifier(defect.get("id"), "defect.id")
        v.text(defect.get("description"), "defect.description")
        if type(defect.get("blocking")) is not bool:
            v.error("defect.blocking", "must be boolean")
        if defect.get("status") not in ("OPEN", "FIXED", "WAIVED"):
            v.error("defect.status", "must be OPEN, FIXED or WAIVED")
        if defect.get("status") == "FIXED":
            for ref in v.strings(defect.get("regression_runs"), "defect.regression_runs", True):
                if ref not in current:
                    v.error("defect.regression_runs", "must reference a current run")
        elif "regression_runs" in defect:
            v.error("defect", "regression_runs requires FIXED status")
        if defect.get("status") == "WAIVED":
            waiver = v.obj(defect.get("waiver"), "defect.waiver", ("reason", "authorization"))
            for key in ("reason", "authorization"):
                v.text(waiver.get(key), "defect.waiver." + key)
        elif "waiver" in defect:
            v.error("defect", "waiver requires WAIVED status")
    if "limitations" in results:
        v.strings(results["limitations"], "results.limitations")
    if v.errors:
        return _outcome(v.errors, blockers, summary)
    summary["current_runs"] = len(current)
    if not current:
        blockers.append("no current runs selected")
    expected_hashes = {"plan_sha256": canonical_sha256(plan), "inputs_sha256": canonical_sha256(plan["inputs"]),
                       "configuration_sha256": canonical_sha256(plan["configuration"])}
    valid_runs = set()
    for rid in sorted(current):
        run = runs[rid]
        for key, expected in expected_hashes.items():
            if run[key] != expected:
                errors.append("run " + rid + " identity mismatch: " + key)
        if run["status"] != "PASS" or run["exit_code"] != 0 or run["checks"] == 0 or run["errors"] or run["timeouts"]:
            blockers.append("run " + rid + " is not an error-free PASS with active checks")
        else:
            valid_runs.add(rid)
        count = sum(obs["checks"] for obs in observations if obs["run_id"] == rid)
        if count != run["checks"]:
            errors.append("run " + rid + " check count differs from its observations")
    for rid, run in runs.items():
        if rid not in current:
            same_baseline = all(run[key] == expected for key, expected in expected_hashes.items())
            ineffective = run["status"] != "PASS" or run["exit_code"] != 0 or run["checks"] == 0 or run["errors"] or run["timeouts"]
            if same_baseline and ineffective and "disposition" not in run:
                blockers.append("same-baseline historical failure lacks disposition: " + rid)
            if "disposition" in run and any(ref not in valid_runs for ref in run["disposition"]["regression_runs"]):
                blockers.append("historical disposition lacks effective regression: " + rid)
    for defect in results.get("defects", []):
        if defect["status"] == "OPEN" and defect["blocking"]:
            blockers.append("open blocking defect: " + defect["id"])
        if defect["status"] == "FIXED" and any(ref not in valid_runs for ref in defect["regression_runs"]):
            blockers.append("fixed defect lacks effective regression: " + defect["id"])
    passed = set()
    for obs in observations:
        if obs["status"] != "PASS" or obs["checks"] == 0:
            blockers.append("check " + obs["check_id"] + " is " + obs["status"] + " or inactive")
        elif obs["run_id"] in valid_runs:
            cp = cps[obs["check_id"]][1]
            if cp["expected"] is not None and cp["expected"].strip().upper() not in UNKNOWN and cp["implementation"] is not None:
                passed.add(obs["check_id"])
    # Every selected execution of a CP must be effective; a later PASS cannot hide a selected failure.
    passed -= {obs["check_id"] for obs in observations if obs["status"] != "PASS" or obs["checks"] == 0 or obs["run_id"] not in valid_runs}
    for cid in sorted(set(cps) - passed):
        blockers.append("check has no effective current PASS: " + cid)
    summary["passed_checks"] = len(passed)
    summary["passed_check_ids"] = sorted(passed)
    if not cps:
        blockers.append("no planned checks")
    comparisons = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b,
                   ">": lambda a, b: a > b, "<": lambda a, b: a < b}
    for field, target_field in (("coverage", "coverage_targets"), ("measurements", "metrics")):
        for target in plan[target_field]:
            values = [item for item in results[field] if item["target_id"] == target["id"]]
            if not values:
                blockers.append("missing " + field + " evidence: " + target["id"])
            for item in values:
                if item["run_id"] not in valid_runs or not comparisons[target["op"]](item["value"], target["target"]):
                    blockers.append(field + " target not met on a valid run: " + target["id"])
    if root is None:
        blockers.append("current root not supplied; file identity has not been verified")
    else:
        _verify_files(plan, list(runs.values()), root, errors)
    return _outcome(errors, blockers, summary)


def load_inputs(plan_path, results_path=None, root=None):
    """CLI input files, as well as their evidence references, stay inside an explicit root."""
    paths = []
    resolved_root = None
    if root is not None:
        try:
            resolved_root = Path(root).resolve(strict=True)
            if not resolved_root.is_dir():
                raise ValueError()
        except (OSError, ValueError, RuntimeError):
            raise ValueError("root must be an existing directory") from None
    for raw in (plan_path, results_path):
        if raw is None:
            paths.append(None)
            continue
        path = Path(raw)
        if resolved_root is not None:
            if not path.is_absolute():
                path = resolved_root / path
            try:
                relative = path.relative_to(resolved_root).as_posix()
                path = resolve_inside(resolved_root, relative)
            except ValueError:
                raise ValueError("input JSON must be inside root with no traversal or symlink") from None
        try:
            paths.append(load_json(path))
        except OSError:
            raise ValueError("cannot read input JSON") from None
    return paths[0], paths[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate declared verification scope and current evidence. Exit 0=scope satisfied, 2=incomplete, 1=invalid. Does not prove undeclared requirements.")
    parser.add_argument("--plan", required=True, help="plan JSON (relative to --root when provided)")
    parser.add_argument("--results", help="results JSON; required unless --plan-only")
    parser.add_argument("--root", help="current project root; required for ACCEPTED")
    parser.add_argument("--plan-only", action="store_true", help="validate plan structure/readiness only; never product acceptance")
    args = parser.parse_args(argv)
    try:
        if not args.plan_only and not args.results:
            raise ValueError("--results is required without --plan-only")
        plan, results = load_inputs(args.plan, args.results, args.root)
        if args.plan_only:
            errors = validate_plan(plan)
            blockers = [] if errors else plan_blockers(plan)
            outcome = _outcome(errors, blockers, {})
            if outcome["status"] == "ACCEPTED":
                outcome["status"] = "PLAN_VALID"
        else:
            outcome = evaluate(plan, results, args.root)
    except (ValueError, TypeError) as exc:
        outcome = _outcome([str(exc)], [], {})
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return EXIT_CODES[outcome["status"]]


if __name__ == "__main__":
    sys.exit(main())
