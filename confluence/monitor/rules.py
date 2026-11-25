"""Deterministic rules over a ModuleAssessment. Shared by the gate, the watcher and the audit.

R1 error storm      bad / eligible-finished >= abort_fail_frac (after enough units finished)
R2 zero yield       has-data coverage of eligible-finished units < abort_coverage
R3 fatal signature  apptainer FATAL / ImportError / ... in enough units
R4 fixed module     every task of a continent/global module failed, or its products are missing/empty
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from confluence.monitor.assess import BAD, ModuleAssessment, fatal_threshold, is_fixed_module, required_units
from confluence.monitor.config import MonitorConfig

ABORT, FAIL, WARN, INFO = "abort", "fail", "warn", "info"
SEVERITY_RANK = {INFO: 0, WARN: 1, FAIL: 2, ABORT: 3}


@dataclass
class Finding:
    severity: str
    code: str
    module: str
    message: str
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _examples(a: ModuleAssessment, classes: set[str], n: int = 5) -> list[dict]:
    out = []
    for r in a.results:
        if r.cls in classes:
            out.append({"idx": r.idx, "unit": r.unit, "class": r.cls, "rc": r.rc, "log": r.log})
            if len(out) >= n:
                break
    return out


def module_rules(
    a: ModuleAssessment, mcfg: MonitorConfig, *, systemic_severity: str = ABORT, check_products: bool = True
) -> list[Finding]:
    """Rules that indicate a systemically broken module. systemic_severity is ABORT for the gate and
    watcher, FAIL for the audit (the run is already over)."""
    m = a.module
    findings: list[Finding] = []
    n_req = required_units(a, mcfg.min_units, mcfg.min_units_floor)
    n_fin = a.eligible_finished
    abort_frac = mcfg.for_module(m, "abort_fail_frac")
    top = a.signatures.top(5)

    if not is_fixed_module(a) and n_fin >= n_req and a.fail_frac >= abort_frac:
        findings.append(
            Finding(
                systemic_severity,
                "R1_error_storm",
                m,
                f"{a.bad}/{n_fin} eligible units failed ({a.fail_frac:.0%} >= {abort_frac:.0%})",
                {"classes": a.summary()["classes"], "top_signatures": top, "examples": _examples(a, BAD)},
            )
        )

    cov_thr = mcfg.for_module(m, "abort_coverage", a.spec.abort_coverage)
    cov = a.coverage
    if cov is not None and cov_thr > 0 and n_fin >= (1 if is_fixed_module(a) else n_req) and cov < cov_thr:
        findings.append(
            Finding(
                systemic_severity,
                "R2_low_yield",
                m,
                f"only {cov:.0%} of {n_fin} eligible units produced output with data (< {cov_thr:.0%})",
                {
                    "classes": a.summary()["classes"],
                    "probed": a.probed,
                    "probed_with_data": a.probed_data,
                    "top_signatures": top,
                },
            )
        )

    if a.fatal_units:
        thr = fatal_threshold(a, mcfg.fatal_min_units, mcfg.fatal_frac)
        if a.fatal_units >= thr:
            fatal_sigs = [s for s in top if s["fatal"]] or top
            findings.append(
                Finding(
                    systemic_severity,
                    "R3_fatal_signature",
                    m,
                    f"fatal error in {a.fatal_units} units (threshold {thr}): "
                    + (fatal_sigs[0]["signature"] if fatal_sigs else ""),
                    {"top_signatures": fatal_sigs},
                )
            )

    if is_fixed_module(a) and a.finished:
        if a.results and all(r.cls in BAD for r in a.results):
            findings.append(
                Finding(
                    systemic_severity,
                    "R4_all_tasks_failed",
                    m,
                    f"all {len(a.results)} task(s) of {m} failed",
                    {"classes": a.summary()["classes"], "top_signatures": top, "examples": _examples(a, BAD)},
                )
            )
    for prod in a.products if check_products else []:
        if not prod.get("ok"):
            findings.append(
                Finding(
                    systemic_severity,
                    "R4_missing_product",
                    m,
                    f"{m} did not produce {prod['pattern']} ({prod.get('state', 'count=0')})",
                    {"product": prod, "top_signatures": top},
                )
            )
    return findings


def warning_rules(a: ModuleAssessment, mcfg: MonitorConfig, *, final: bool) -> list[Finding]:
    m = a.module
    out: list[Finding] = []
    n_fin = a.eligible_finished
    if n_fin and a.fail_frac > mcfg.warn_fail_frac and a.fail_frac < mcfg.for_module(m, "abort_fail_frac"):
        out.append(
            Finding(
                WARN,
                "W1_elevated_failures",
                m,
                f"{a.bad}/{n_fin} eligible units failed ({a.fail_frac:.0%})",
                {"top_signatures": a.signatures.top(5), "examples": _examples(a, BAD)},
            )
        )
    if final:
        warn_cov = mcfg.for_module(m, "warn_coverage", a.spec.warn_coverage)
        cov = a.coverage
        abort_cov = mcfg.for_module(m, "abort_coverage", a.spec.abort_coverage)
        # Skip only when R2 already reported it (R2 needs enough units; tiny runs still get the warning).
        r2_fired = (
            abort_cov > 0
            and cov is not None
            and cov < abort_cov
            and n_fin >= required_units(a, mcfg.min_units, mcfg.min_units_floor)
        )
        if cov is not None and warn_cov > 0 and cov < warn_cov and not r2_fired:
            out.append(
                Finding(
                    WARN,
                    "W2_low_coverage",
                    m,
                    f"has-data coverage {cov:.0%} is below the usual minimum {warn_cov:.0%}",
                    {"classes": a.summary()["classes"]},
                )
            )
        c = a.counts
        if c.get("CORRUPT"):
            out.append(
                Finding(
                    WARN,
                    "W3_corrupt_outputs",
                    m,
                    f"{c['CORRUPT']} outputs are 0-byte or not valid netCDF",
                    {"examples": _examples(a, {"CORRUPT"}, 10)},
                )
            )
        if c.get("PARTIAL"):
            out.append(
                Finding(
                    WARN,
                    "W4_partial_outputs",
                    m,
                    f"{c['PARTIAL']} outputs (of {a.probed} probed) have data but time_str all no_data",
                    {"examples": _examples(a, {"PARTIAL"}, 10)},
                )
            )
        n_missing = c.get("MISSING", 0)
        if n_missing and n_fin and n_missing / n_fin > 0.02 and a.spec.per_unit_output and not a.spec.sparse_output:
            # Pre-monitor runs only: no exit codes. Modules that log a "no data" line for skipped reaches
            # make MISSING ambiguous (INFO); for the others missing outputs usually mean per-reach crashes.
            sev = INFO if a.spec.nodata_patterns else WARN
            out.append(
                Finding(
                    sev,
                    "W6_missing_outputs",
                    m,
                    f"{n_missing} eligible units ({n_missing / n_fin:.0%}) have no output and no exit code "
                    "(pre-monitor run): likely per-reach failures, see the top signatures",
                    {"top_signatures": a.signatures.top(5), "examples": _examples(a, {"MISSING"}, 10)},
                )
            )
        if c.get("LOST"):
            out.append(
                Finding(
                    WARN,
                    "W5_lost_units",
                    m,
                    f"{c['LOST']} units started but never finished (OOM, time limit or node failure)",
                    {"examples": _examples(a, {"LOST"}, 10)},
                )
            )
    return out


def worst(findings: list[Finding]) -> str:
    if not findings:
        return INFO
    return max(findings, key=lambda f: SEVERITY_RANK[f.severity]).severity
