"""Per-module assessment: joins the unit ledger, task logs and output files into one class per unit.

Unit classes
------------
OK         usable output (or rc 0 for modules without per-unit outputs)
NODATA     rc 0, no output, legitimately nothing to do (no-data reach, ungauged reach, ...)
EXPLAINED  failed/no output, but an upstream file is missing (e.g. no SWOT file for the reach)
STUB       output exists but holds no data (hivdi's variable-less files, all-fill Q)
PARTIAL    consensus Q written but time_str all no_data
FAIL       rc != 0 with all inputs present
SILENT     rc 0, no output and no "no data" line, for modules where that means a silent failure (input)
LOST       BEGIN without END after the task ended (OOM, time limit, node failure)
CORRUPT    output is 0 bytes or not a valid netCDF
NOT_RUN    no unit record although the module ran (task died before the unit, or was cancelled)
MISSING    pre-monitor runs only: no output, inputs present, no record to tell NODATA from FAIL
RUNNING / PENDING  watcher only: still in flight
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from confluence.monitor import outputs as O
from confluence.monitor.context import RunContext
from confluence.monitor.ledger import (
    TaskUnits,
    UnitRecord,
    UnitTail,
    has_unit_ledger,
    module_logs,
    read_log,
    read_task_units,
    segment_log,
    task_log_path,
)
from confluence.monitor.registry import ModuleSpec, is_fixed
from confluence.monitor.signatures import SignatureMatcher, SignatureTable

OK, NODATA, EXPLAINED, STUB, PARTIAL = "OK", "NODATA", "EXPLAINED", "STUB", "PARTIAL"
FAIL, SILENT, LOST, CORRUPT, NOT_RUN = "FAIL", "SILENT", "LOST", "CORRUPT", "NOT_RUN"
MISSING, RUNNING, PENDING = "MISSING", "RUNNING", "PENDING"

BAD = {FAIL, SILENT, LOST, CORRUPT, NOT_RUN}
IN_FLIGHT = {RUNNING, PENDING}
CLASS_ORDER = [OK, NODATA, EXPLAINED, STUB, PARTIAL, MISSING, FAIL, SILENT, LOST, CORRUPT, NOT_RUN, RUNNING, PENDING]


@dataclass
class UnitResult:
    idx: int
    unit: str
    cls: str
    rc: int | None = None
    output_state: str | None = None
    upstream_ok: bool = True
    node: str = ""
    log: str | None = None


@dataclass
class ModuleAssessment:
    module: str
    spec: ModuleSpec
    ledger: bool
    results: list[UnitResult] = field(default_factory=list)
    signatures: SignatureTable = field(default_factory=SignatureTable)
    fatal_units: int = 0
    n_tasks: int = 0
    n_task_logs: int = 0
    probed: int = 0
    probed_data: int = 0
    products: list[dict] = field(default_factory=list)
    logs_read: bool = True

    # ------------------------------------------------------------------ derived numbers
    @property
    def counts(self) -> Counter:
        return Counter(r.cls for r in self.results)

    @property
    def scope(self) -> int:
        return len(self.results)

    @property
    def eligible(self) -> list[UnitResult]:
        return [r for r in self.results if r.upstream_ok]

    @property
    def eligible_finished(self) -> int:
        return sum(1 for r in self.eligible if r.cls not in IN_FLIGHT)

    @property
    def finished(self) -> int:
        return sum(1 for r in self.results if r.cls not in IN_FLIGHT)

    @property
    def bad(self) -> int:
        return sum(1 for r in self.eligible if r.cls in BAD)

    @property
    def fail_frac(self) -> float:
        n = self.eligible_finished
        return self.bad / n if n else 0.0

    @property
    def present(self) -> int:
        """Units whose output exists and passed the cheap check (product files for modules without
        per-unit outputs, e.g. combine_data, metroman_consolidation)."""
        if not self.spec.per_unit_output and self.products:
            return sum(p.get("count", 1 if p.get("ok") else 0) for p in self.products)
        return sum(1 for r in self.results if r.output_state in O.GOOD_STATES or r.output_state == O.STUB)

    @property
    def data_frac_probed(self) -> float | None:
        return self.probed_data / self.probed if self.probed else None

    @property
    def has_data_est(self) -> float:
        """Estimated number of units with data: probed DATA + unprobed PRESENT x probed data fraction."""
        n_data = sum(1 for r in self.results if r.output_state == O.DATA)
        n_unprobed = sum(1 for r in self.results if r.output_state == O.PRESENT)
        p = self.data_frac_probed
        return n_data + n_unprobed * (p if p is not None else 1.0)

    @property
    def coverage(self) -> float | None:
        """Estimated has-data fraction of eligible finished units (None without per-unit outputs)."""
        if not self.spec.per_unit_output:
            return None
        n = self.eligible_finished
        if not n:
            return None
        eligible_data = self.has_data_est - sum(
            1 for r in self.results if not r.upstream_ok and r.output_state in (O.DATA, O.PRESENT)
        )
        return max(0.0, min(1.0, eligible_data / n))

    def summary(self) -> dict:
        c = self.counts
        return {
            "module": self.module,
            "spec": self.spec.key,
            "scale": self.spec.scale,
            "ledger": self.ledger,
            "units": self.scope,
            "classes": {k: c[k] for k in CLASS_ORDER if c.get(k)},
            "eligible_finished": self.eligible_finished,
            "bad": self.bad,
            "fail_frac": round(self.fail_frac, 4),
            "outputs_present": self.present,
            "probed": self.probed,
            "probed_with_data": self.probed_data,
            "coverage_est": None if self.coverage is None else round(self.coverage, 4),
            "fatal_units": self.fatal_units,
            "tasks": self.n_tasks,
            "products": self.products,
            "top_signatures": self.signatures.top(8),
        }


# ---------------------------------------------------------------------------------------- core
def _upstream_ok(ctx: RunContext, spec: ModuleSpec, unit: str) -> bool:
    for pattern in spec.requires:
        p = ctx.path(pattern, unit)
        try:
            if p.stat().st_size == 0:
                return False
        except OSError:
            return False
    return True


def classify(
    spec: ModuleSpec,
    out_state: str | None,
    upstream_ok: bool,
    rec: UnitRecord | None,
    nodata_hit: bool,
    ledger: bool,
    in_progress: bool,
) -> str:
    if rec is not None and rec.rc is None:
        return RUNNING if in_progress else LOST
    if spec.per_unit_output:
        if out_state in (O.DATA, O.PRESENT):
            return OK
        if out_state == O.PARTIAL:
            return PARTIAL
        if out_state == O.STUB:
            return STUB
        if out_state in (O.EMPTY, O.BAD):
            return CORRUPT
    if not spec.per_unit_output:
        if rec is None:
            if in_progress:
                return PENDING
            return NOT_RUN if ledger else MISSING
        if rec.rc == 0:
            return OK
        return FAIL if upstream_ok else EXPLAINED
    # per-unit output expected but absent
    if not upstream_ok:
        return EXPLAINED
    if rec is None:
        if in_progress:
            return PENDING
        if ledger:
            return NOT_RUN
        return NODATA if (nodata_hit or spec.sparse_output) else MISSING
    if rec.rc != 0:
        return FAIL
    if spec.silent_is_failure and not nodata_hit:
        return SILENT
    return NODATA


def assess_module(
    ctx: RunContext,
    module: str,
    *,
    indices: set[int] | None = None,
    arrays: set[str] | None = None,
    probe_sample: int = 200,
    deep_all: bool = False,
    in_progress: bool = False,
    read_logs: bool = True,
    matcher: SignatureMatcher | None = None,
    tail: UnitTail | None = None,
) -> ModuleAssessment:
    spec = ctx.spec(module)
    matcher = matcher or SignatureMatcher(ctx.monitor.benign_patterns, ctx.monitor.fatal_patterns)
    ledger = has_unit_ledger(ctx.monitor_dir, module)
    a = ModuleAssessment(module=module, spec=spec, ledger=ledger, logs_read=read_logs)

    expected = ctx.units(spec.index)
    if indices is not None:
        expected = [(i, u) for i, u in expected if i in indices]

    # ---- unit ledger: latest record per index
    tasks: list[TaskUnits] = tail.refresh(module, arrays) if tail else read_task_units(ctx.monitor_dir, module, arrays)
    a.n_tasks = len(tasks)
    rec_by_idx: dict[int, UnitRecord] = {}
    for t in tasks:
        for rec in t.records:
            for i in rec.indices():
                prev = rec_by_idx.get(i)
                if prev is None or (rec.begin or 0) >= (prev.begin or 0):
                    rec_by_idx[i] = rec

    # ---- logs: per-unit signatures (marker mode) or per-log (old runs) + no-data attribution
    nodata_res = [re.compile(p) for p in spec.nodata_patterns]
    nodata_units: set[str] = set()
    nodata_idx: set[str] = set()
    unit_sigs: dict[str, set[str]] = {}
    unit_fatal: dict[str, set[str]] = {}
    unit_log: dict[str, str] = {}
    if read_logs:
        if ledger:
            log_paths = [task_log_path(ctx.report_dir, module, t.array_id, t.task_id) for t in tasks]
        else:
            log_paths = module_logs(ctx.report_dir, module)
        for lp in log_paths:
            lines = read_log(lp)
            if not lines:
                continue
            a.n_task_logs += 1
            segments, other = segment_log(lines)
            if segments:
                for idx, seg in segments.items():
                    unit_log[idx] = str(lp)
                    sigs = matcher.signatures(seg)
                    fat = matcher.fatal_signatures(seg)
                    unit_sigs[idx] = sigs
                    unit_fatal[idx] = fat
                    if any(r.search(ln) for r in nodata_res for ln in seg):
                        nodata_idx.add(idx)
                    a.signatures.add(sigs, str(lp), fat)
                block = other
            else:
                block = lines
            # unmarked lines (old runs, or output before the first unit)
            sigs = matcher.signatures(block)
            fat = matcher.fatal_signatures(block)
            if sigs or fat:
                a.signatures.add(sigs, str(lp), fat)
            if fat and not segments:
                a.fatal_units += 1  # old runs: count per task log
            for r in nodata_res:
                if "unit" in r.groupindex:
                    for ln in block:
                        m = r.search(ln)
                        if m:
                            nodata_units.add(m.group("unit"))

    # ---- outputs
    states: dict[int, str | None] = {}
    if spec.per_unit_output:
        for i, u in expected:
            states[i] = O.cheap_state(O.resolve(ctx.path(spec.output, u)), spec.probe)
        present_idx = [i for i, _ in expected if states[i] == O.PRESENT]
        to_probe = present_idx if deep_all else O.sample_evenly(present_idx, probe_sample)
        unit_of = dict(expected)
        for i in to_probe:
            st = O.deep_state(O.resolve(ctx.path(spec.output, unit_of[i])), spec.probe)
            states[i] = st
            a.probed += 1
            if st in (O.DATA, O.PARTIAL):
                a.probed_data += 1

    # ---- classify
    for i, u in expected:
        rec = rec_by_idx.get(i)
        key = rec.idx if rec is not None else str(i)
        up = _upstream_ok(ctx, spec, u) if spec.requires else True
        hit = key in nodata_idx or u in nodata_units
        cls = classify(spec, states.get(i), up, rec, hit, ledger, in_progress)
        a.results.append(
            UnitResult(
                idx=i,
                unit=u,
                cls=cls,
                rc=rec.rc if rec else None,
                output_state=states.get(i),
                upstream_ok=up,
                node=rec.node if rec else "",
                log=unit_log.get(key),
            )
        )
        if ledger and unit_fatal.get(key):
            a.fatal_units += 1

    # ---- whole-module products
    for pattern in spec.products:
        p = ctx.path(pattern)
        if any(c in str(p) for c in "*?["):
            import glob as _glob

            n = len(_glob.glob(str(p)))
            a.products.append({"pattern": pattern, "count": n, "ok": n > 0})
        else:
            st = O.deep_state(p if _exists_nfs(p) else None, _json_probe(pattern))
            a.products.append({"pattern": pattern, "state": st, "ok": st in (O.DATA, O.PRESENT)})
    return a


def _exists_nfs(p: Path) -> bool:
    """exists() that sees files just created on another node.

    NFS can cache a negative lookup on this node for a while after a compute node creates the
    file, so a product written seconds earlier can look missing. Listing the parent directory
    revalidates it, so fall back to that before calling the file missing.
    """
    if p.exists():
        return True
    try:
        return p.name in os.listdir(p.parent)
    except OSError:
        return False


def _json_probe(pattern: str):
    from confluence.monitor.registry import Probe

    return Probe("json") if pattern.endswith(".json") else None


def required_units(a: ModuleAssessment, min_units: int, floor: int) -> int:
    """How many eligible units must have finished before rate rules apply (never fewer than floor,
    so tiny test runs are judged by the fixed-module and fatal rules only)."""
    return max(floor, min(min_units, len(a.eligible)))


def fatal_threshold(a: ModuleAssessment, fatal_min_units: int, fatal_frac: float) -> int:
    n = a.finished if a.ledger else max(a.n_task_logs, 1)
    return max(1, min(fatal_min_units, n), math.ceil(fatal_frac * n))


def is_fixed_module(a: ModuleAssessment) -> bool:
    return is_fixed(a.spec)
