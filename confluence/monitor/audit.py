"""Post-run meta-validation: coverage funnel, file integrity, download health, job health,
error signatures, provenance and an optional baseline diff. Writes audit.json, audit.md and audit.html.

Exit codes: 0 PASS, 1 WARN, 2 FAIL.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from confluence.monitor import outputs as O
from confluence.monitor import report
from confluence.monitor.assess import ModuleAssessment, assess_module
from confluence.monitor.context import RunContext
from confluence.monitor.guide import write_guide as _write_guide
from confluence.monitor.ledger import driver_log_arrays, driver_logs, module_logs, read_log, read_submissions
from confluence.monitor.provenance import collect_provenance
from confluence.monitor.registry import EXPANDED, REACHES
from confluence.monitor.rules import FAIL, INFO, SEVERITY_RANK, WARN, Finding, module_rules, warning_rules, worst
from confluence.monitor.signatures import error_excerpt
from confluence.monitor.slurm import Slurm, mem_bytes

EPOCH_2000 = datetime(2000, 1, 1, tzinfo=UTC)
VERDICT = {INFO: "PASS", WARN: "WARN", FAIL: "FAIL", "abort": "FAIL"}
EXIT_CODES = {"PASS": 0, "WARN": 1, "FAIL": 2}


# ------------------------------------------------------------------------------- helpers
def _swot_time_range(cfg_range: str | None) -> tuple[datetime, datetime] | None:
    if not cfg_range:
        return None
    m1 = re.search(r"start_time=([\dT:\-]+)Z?", cfg_range)
    m2 = re.search(r"end_time=([\dT:\-]+)Z?", cfg_range)
    if not (m1 and m2):
        return None
    try:
        start = datetime.fromisoformat(m1.group(1)).replace(tzinfo=UTC)
        end = datetime.fromisoformat(m2.group(1)).replace(tzinfo=UTC)
    except ValueError:
        return None
    return start, end


def _fmt_dt(seconds: float) -> str:
    return (EPOCH_2000 + timedelta(seconds=seconds)).strftime("%Y-%m-%d")


def _pct(x: float | None) -> str:
    return "" if x is None else f"{x:.0%}"


# ------------------------------------------------------------------------------- sections
def roi_section(ctx: RunContext) -> tuple[dict, list[Finding]]:
    roi = [str(r) for r in ctx.index("reaches_of_interest.json")]
    expanded = {str(e.get("reach_id")) for e in ctx.index(EXPANDED) if isinstance(e, dict)}
    reaches = {str(e.get("reach_id")) for e in ctx.index(REACHES) if isinstance(e, dict)}
    findings: list[Finding] = []
    sec = {"roi": len(roi), "expanded": len(expanded), "reaches": len(reaches)}
    if roi and expanded:
        missing = [r for r in roi if r not in expanded]
        sec["roi_not_in_expanded"] = len(missing)
        sec["roi_not_in_expanded_examples"] = missing[:10]
        frac = 1 - len(missing) / len(roi)
        sec["roi_found_frac"] = round(frac, 4)
        # ~88% is normal for full runs: setfinder drops reaches it can't place in a set.
        if frac < 0.8:
            findings.append(
                Finding(
                    WARN if frac > 0.5 else FAIL,
                    "A1_roi_not_found",
                    "expanded_setfinder",
                    f"only {frac:.0%} of ROI reaches are in expanded_reaches_of_interest.json (usually ~88%; "
                    f"do the ROI ids match sword_version {ctx.cfg.get('sword_version')}?)",
                    {"missing_examples": missing[:10]},
                )
            )
    return sec, findings


def download_section(ctx: RunContext, a: ModuleAssessment | None, sample: int) -> tuple[dict, list[Finding]]:
    """SWOT download health (input module, or whatever is in input/swot for derived runs)."""
    findings: list[Finding] = []
    swot_dir = ctx.swot_dir
    files = sorted(swot_dir.glob("*_SWOT.nc")) if swot_dir.is_dir() else []
    zero = [p.name for p in files if p.stat().st_size == 0]
    sec: dict = {
        "swot_dir": str(swot_dir),
        "swot_files": len(files),
        "zero_byte": len(zero),
        "zero_byte_examples": zero[:10],
    }
    if zero:
        findings.append(
            Finding(
                FAIL,
                "A2_zero_byte_downloads",
                "input",
                f"{len(zero)} SWOT files are 0 bytes",
                {"examples": zero[:10]},
            )
        )

    # log-derived counts
    http = Counter()
    reuse = 0
    for lp in module_logs(ctx.report_dir, "input"):
        for ln in read_log(lp):
            if "Server error" in ln:
                http["5xx_retry"] += 1
            elif "Rate limit hit" in ln:
                http["429"] += 1
            elif "Client error" in ln:
                http["4xx_client_error"] += 1
            elif "Request failed" in ln or "Unexpected exception" in ln:
                http["connection_retry"] += 1
            elif "Hydrocron returned unexpected message" in ln or "Hydrocron error in payload" in ln:
                http["hydrocron_error"] += 1
            elif "already downloaded" in ln:
                reuse += 1
    sec["http"] = dict(http)
    sec["skip_existing_messages"] = reuse
    if http.get("4xx_client_error"):
        findings.append(
            Finding(
                FAIL,
                "A3_client_errors",
                "input",
                f"{http['4xx_client_error']} Hydrocron client errors (bad collection name or query?)",
                {"http": dict(http)},
            )
        )
    if http.get("429"):
        findings.append(Finding(WARN, "A4_rate_limited", "input", f"{http['429']} HTTP 429 rate-limit exits", {}))

    if a is not None:
        silent = [r.unit for r in a.results if r.cls in ("SILENT", "MISSING")]
        sec["missing_without_nodata_message"] = len(silent)
        sec["missing_without_nodata_examples"] = silent[:10]
        if silent:
            findings.append(
                Finding(
                    WARN,
                    "A5_silent_missing_downloads",
                    "input",
                    f"{len(silent)} reaches have no SWOT file and no 'no data' log line",
                    {"examples": silent[:10]},
                )
            )

    # time span of a sample vs configured time_range
    tr = _swot_time_range((ctx.module_args("input") or {}).get("time_range"))
    spans = [s for s in (O.time_span(p) for p in O.sample_evenly(files, min(sample, 100))) if s]
    if spans:
        tmin, tmax = min(s[0] for s in spans), max(s[1] for s in spans)
        sec["sampled_time_span"] = [_fmt_dt(tmin), _fmt_dt(tmax)]
        if tr:
            sec["configured_time_range"] = [tr[0].date().isoformat(), tr[1].date().isoformat()]
            lo = EPOCH_2000 + timedelta(seconds=tmin)
            hi = EPOCH_2000 + timedelta(seconds=tmax)
            if lo < tr[0] - timedelta(days=1) or hi > tr[1] + timedelta(days=1):
                findings.append(
                    Finding(
                        WARN,
                        "A6_time_outside_range",
                        "input",
                        f"SWOT times {_fmt_dt(tmin)}..{_fmt_dt(tmax)} fall outside the configured time_range "
                        f"(reused downloads from another run?)",
                        {},
                    )
                )
    return sec, findings


def jobs_section(ctx: RunContext, slurm: Slurm) -> tuple[dict, list[Finding]]:
    """sacct-based job health per module."""
    findings: list[Finding] = []
    subs = read_submissions(ctx.monitor_dir)
    arrays: dict[str, list[str]] = {}
    if subs:
        for s in subs:
            arrays.setdefault(s.module, []).append(s.array_id)
    else:
        arrays = driver_log_arrays(ctx.log_dir)
    out: dict = {}
    for module, ids in arrays.items():
        rows = slurm.sacct(ids)
        if not rows:
            continue
        states = Counter(r["State"].split()[0] for r in rows)
        exit_codes = Counter(r["ExitCode"] for r in rows)
        cpu_h = sum(int(r["CPUTimeRAW"] or 0) for r in rows) / 3600
        rss = [mem_bytes(r.get("MaxRSS")) for r in rows]
        req = mem_bytes(rows[0].get("ReqMem"))
        max_rss = max((x for x in rss if x), default=None)
        elapsed = [int(r["ElapsedRaw"] or 0) for r in rows]
        tl = [int(r["TimelimitRaw"]) * 60 for r in rows if (r.get("TimelimitRaw") or "").isdigit()]
        node_fail = Counter()
        for r in rows:
            # "None assigned" = never started (e.g. cancelled while pending): not a node.
            if r["NodeList"] in ("", "None assigned"):
                continue
            if r["State"].split()[0] in ("FAILED", "NODE_FAIL", "TIMEOUT", "OUT_OF_MEMORY", "CANCELLED"):
                node_fail[r["NodeList"]] += 1
        m = {
            "arrays": ids,
            "tasks": len(rows),
            "states": dict(states),
            "exit_codes": dict(exit_codes.most_common(5)),
            "cpu_hours": round(cpu_h, 2),
            "max_rss_gb": None if max_rss is None else round(max_rss / 1024**3, 2),
            "req_mem_gb": None if req is None else round(req / 1024**3, 2),
            "max_elapsed_s": max(elapsed, default=0),
            "time_limit_s": max(tl, default=None),
            "failing_nodes": dict(node_fail.most_common(5)),
        }
        out[module] = m
        bad_states = {k: v for k, v in states.items() if k in ("TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL")}
        if bad_states:
            findings.append(Finding(WARN, "J1_bad_job_states", module, f"tasks ended {bad_states}", m))
        if max_rss and req and max_rss > 0.8 * req:
            findings.append(
                Finding(
                    WARN,
                    "J2_memory_headroom",
                    module,
                    f"peak memory use {max_rss / 1024**3:.1f} GB is over 80% of the {req / 1024**3:.1f} GB requested "
                    "(raise `mem` for this module)",
                    {},
                )
            )
        if tl and max(elapsed, default=0) > 0.8 * max(tl):
            findings.append(
                Finding(
                    WARN,
                    "J3_time_headroom",
                    module,
                    f"longest task {max(elapsed) / 60:.0f} min is over 80% of the {max(tl) / 60:.0f} min time limit",
                    {},
                )
            )
        if node_fail and sum(node_fail.values()) >= 5:
            node, n = node_fail.most_common(1)[0]
            if n >= 0.5 * sum(node_fail.values()) and n >= 5:
                findings.append(Finding(INFO, "J4_node_concentration", module, f"{n} failed tasks ran on {node}", {}))
    return out, findings


def driver_section(ctx: RunContext) -> dict:
    logs = driver_logs(ctx.log_dir)
    sec: dict = {"driver_logs": [str(p) for p in logs]}
    if logs:
        lines = read_log(logs[-1])
        sec["last_lines"] = lines[-5:]
        sec["finished_line"] = any("has finished" in ln for ln in lines)
    exit_file = ctx.monitor_dir / "driver.exit"
    if exit_file.exists():
        sec["driver_exit"] = exit_file.read_text().strip()
    ab = report.read_abort(ctx.monitor_dir)
    if ab:
        sec["abort"] = {k: ab.get(k) for k in ("time", "source", "reason")}
    return sec


# ------------------------------------------------------------------------------- baseline
def baseline_section(cur: dict[str, dict], base_ctx: RunContext, sample: int) -> tuple[dict, list[Finding]]:
    findings: list[Finding] = []
    out: dict = {"run_dir": str(base_ctx.run_dir), "modules": {}}
    for module, cur_sum in cur.items():
        if module not in base_ctx.audited_modules:
            continue
        b = assess_module(base_ctx, module, probe_sample=sample, read_logs=False)
        bs = b.summary()
        cur_units = set(cur_sum.get("_units_with_output", []))
        base_units = {r.unit for r in b.results if r.output_state in O.GOOD_STATES}
        lost = sorted(base_units - cur_units) if cur_units or base_units else []
        gained = sorted(cur_units - base_units)
        d = {
            "units": [cur_sum["units"], bs["units"]],
            "outputs_present": [cur_sum["outputs_present"], bs["outputs_present"]],
            "coverage_est": [cur_sum["coverage_est"], bs["coverage_est"]],
            "lost_vs_baseline": len(lost),
            "gained_vs_baseline": len(gained),
            "lost_examples": lost[:10],
            "gained_examples": gained[:10],
        }
        out["modules"][module] = d
        c, bcov = cur_sum["coverage_est"], bs["coverage_est"]
        if c is not None and bcov is not None and bcov - c > 0.10:
            findings.append(
                Finding(
                    WARN,
                    "B1_coverage_drop",
                    module,
                    f"has-data coverage {c:.0%} vs {bcov:.0%} in baseline {base_ctx.run_name}",
                    d,
                )
            )
    return out, findings


def _with_excerpts(sigs: list[dict], log_cache: dict[str, list[str]]) -> list[dict]:
    """Attach the full multi-line error message to each signature, and merge signatures that are
    just different lines of the same message (R's `Error in f()` + `Caused by error in g()`,
    Python's `raise X` + `X: ...`)."""
    for s in sigs:
        ex = s.get("example") or ""
        if ex and ex not in log_cache:
            log_cache[ex] = read_log(Path(ex), max_bytes=5_000_000)
        s["excerpt"] = error_excerpt(log_cache.get(ex, []), s["signature"]) if ex else ""
    merged: list[dict] = []
    for s in sorted(sigs, key=lambda x: -len(x["excerpt"])):
        host = next(
            (m for m in merged if s["excerpt"] and m["example"] == s["example"] and s["excerpt"] in m["excerpt"]),
            None,
        )
        if host is None:
            merged.append(dict(s))
        else:
            host["count"] = max(host["count"], s["count"])
            host["fatal"] = host["fatal"] or s["fatal"]
    return sorted(merged, key=lambda x: -x["count"])


# ------------------------------------------------------------------------------- main
def run_audit(
    run_dir: str,
    out_dir: str | None = None,
    baseline: str | None = None,
    sample: int = 300,
    full: bool = False,
    use_sacct: bool = True,
    hash_sifs: bool = False,
    slurm: Slurm | None = None,
    write_guide: bool = True,
) -> tuple[str, dict]:
    ctx = RunContext(run_dir, out_dir)
    mcfg = ctx.monitor
    findings: list[Finding] = []
    modules: dict[str, dict] = {}
    assessments: dict[str, ModuleAssessment] = {}

    roi, f = roi_section(ctx)
    findings += f

    # In a monitored run every module the driver started is in submissions.tsv. A module with no
    # submission and no unit records never ran (the run stopped first): list it, don't score it.
    submitted = {s.module for s in read_submissions(ctx.monitor_dir)}
    not_run: list[str] = []
    for module in ctx.audited_modules:
        if submitted and module not in submitted and not (ctx.monitor_dir / "units" / module).is_dir():
            not_run.append(module)
            continue
        a = assess_module(ctx, module, probe_sample=sample, deep_all=full)
        assessments[module] = a
        s = a.summary()
        s["_units_with_output"] = [r.unit for r in a.results if r.output_state in O.GOOD_STATES]
        modules[module] = s
        findings += module_rules(a, mcfg, systemic_severity=FAIL)
        findings += warning_rules(a, mcfg, final=True)

    dl, f = download_section(ctx, assessments.get("input"), sample)
    findings += f

    jobs: dict = {}
    if use_sacct:
        jobs, f = jobs_section(ctx, slurm or Slurm())
        findings += f

    drv = driver_section(ctx)
    if drv.get("abort"):
        findings.append(Finding(FAIL, "D1_run_aborted", "driver", f"run was aborted: {drv['abort'].get('reason')}", {}))
    elif drv.get("driver_exit") not in (None, "done"):
        findings.append(
            Finding(FAIL, "D2_driver_stopped", "driver", f"driver exit state: {drv.get('driver_exit')}", {})
        )

    base: dict | None = None
    if baseline:
        base, f = baseline_section(modules, RunContext(baseline), sample)
        findings += f

    prov = collect_provenance(ctx, hash_sifs=hash_sifs)
    log_cache: dict[str, list[str]] = {}
    for m in modules.values():
        m.pop("_units_with_output", None)
        m["top_signatures"] = _with_excerpts(m["top_signatures"], log_cache)

    findings.sort(key=lambda x: -SEVERITY_RANK[x.severity])
    verdict = VERDICT[worst(findings)]
    result = {
        "run_name": ctx.run_name,
        "run_dir": str(ctx.run_dir),
        "generated": report.now_iso(),
        "verdict": verdict,
        "options": {"sample": sample, "full": full, "baseline": baseline},
        "roi": roi,
        "modules": modules,
        "not_run": not_run,
        "downloads": dl,
        "jobs": jobs,
        "driver": drv,
        "baseline": base,
        "provenance": prov,
        "findings": [x.to_dict() for x in findings],
    }
    report.write_json(ctx.out_dir / "audit.json", result)
    md = render_audit_md(result)
    report.write_atomic(ctx.out_dir / "audit.md", md)
    try:
        report.write_html(ctx.out_dir / "audit.html", md, title=f"Run audit: {ctx.run_name}")
    except Exception as e:  # the HTML is a convenience copy; never fail the audit over it
        print(f"audit: could not write audit.html ({type(e).__name__}: {e})")
    if write_guide:
        _write_guide(ctx, audit=result, out_dir=out_dir)
    return verdict, result


VERDICT_TEXT = {
    "PASS": "no problems found.",
    "WARN": "the run finished and its results are usable, but some findings below are worth a look.",
    "FAIL": "something went wrong that affects the results; see the findings below.",
}
UNIT_WORD = {
    "reach": ("reach", "reaches"),
    "range": ("reach", "reaches"),
    "set": ("set", "sets"),
    "basin": ("basin", "basins"),
    "continent": ("continent", "continents"),
    "global": ("job", "jobs"),
}
# Errors from reaches that have no SWOT file: expected, shown last (they are the "Upstream missing" units).
_MISSING_FILE = re.compile(r"(?i)no such file|not found")
_SWOT_FILE = re.compile(r"(?i)_SWOT\.nc|swot_file|reach_files\$swot|SWOT Reach file")


def _is_no_swot(text: str) -> bool:
    return bool(_MISSING_FILE.search(text) and _SWOT_FILE.search(text))


BAD_CLASSES = ("FAIL", "SILENT", "LOST", "CORRUPT", "NOT_RUN")
CLASS_LEGEND = [
    ("OK", "The module wrote an output with real values."),
    (
        "No data",
        "The module ran without errors but had nothing to compute: e.g. the reach has no usable SWOT "
        "observations, an algorithm decided not to run, or (validation) the reach has no gauge.",
    ),
    (
        "Upstream missing",
        "No output because a file from an earlier step is missing, almost always because the reach "
        "has no SWOT file. This is not the module's fault, and the same reaches show up here in every module.",
    ),
    ("Empty output", "The module wrote a file, but it holds no values (e.g. discharge is all fill values)."),
    (
        "Failed",
        "The module crashed on this unit, was killed (time limit, memory, node problem), wrote a broken "
        "file, or never started it. The error messages are listed under Errors.",
    ),
    (
        "Unknown",
        "Only for runs made before monitoring existed: no output and no record of why, so the audit "
        "can't tell 'no data' from 'failed'.",
    ),
]


FINDING_TITLES = {
    "A1_roi_not_found": "Many ROI reaches not found",
    "A2_zero_byte_downloads": "Empty SWOT downloads",
    "A3_client_errors": "Hydrocron rejected requests",
    "A4_rate_limited": "Hydrocron rate limits",
    "A5_silent_missing_downloads": "SWOT files missing without explanation",
    "A6_time_outside_range": "SWOT dates outside the requested range",
    "B1_coverage_drop": "Fewer results than the baseline run",
    "D1_run_aborted": "The run was stopped",
    "D2_driver_stopped": "The driver did not finish",
    "J1_bad_job_states": "Slurm tasks timed out, ran out of memory or lost their node",
    "J2_memory_headroom": "Memory nearly used up",
    "J3_time_headroom": "Close to the time limit",
    "J4_node_concentration": "Failures concentrated on one node",
    "R1_error_storm": "Most units failed",
    "R2_low_yield": "Almost no output with data",
    "R3_fatal_signature": "The module can't start",
    "R4_all_tasks_failed": "Every task failed",
    "R4_missing_product": "Expected file not written",
    "W1_elevated_failures": "More failures than usual",
    "W2_low_coverage": "Low coverage",
    "W3_corrupt_outputs": "Broken output files",
    "W4_partial_outputs": "Partly filled output files",
    "W5_lost_units": "Units killed mid-run",
    "W6_missing_outputs": "Outputs missing without a record of why",
}


def _n(x) -> str:
    return "–" if x is None else f"{x:,}" if isinstance(x, int) else str(x)


def _share(a: int, b: int) -> str:
    if not b:
        return f"{a:,}"
    pct = 100 * a / b
    txt = f"{pct:.0f}%" if a in (0, b) or 1 <= pct <= 99 else f"{pct:.1f}%"
    return f"{a:,} of {b:,} ({txt})"


def _rel(path: str, run_dir: str) -> str:
    return path[len(run_dir) + 1 :] if path.startswith(run_dir + "/") else path


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def render_audit_md(r: dict) -> str:
    L: list[str] = []
    verdict = r["verdict"]
    L += [f"# Run audit: {r['run_name']}", "", f"**Verdict: {verdict}**: {VERDICT_TEXT.get(verdict, '')}", ""]
    opts = r["options"]
    L.append(f"Run directory `{r['run_dir']}`, audited {r['generated']}.")
    if opts["baseline"]:
        L.append(f"Compared with `{opts['baseline']}`.")
    L += ["", "## Findings", ""]
    if not r["findings"]:
        L.append("None.")
    for f in r["findings"]:
        title = FINDING_TITLES.get(f["code"], f["code"])
        L.append(f"- **{f['severity'].upper()} · {f['module']}: {title}.** {f['message']} `[{f['code']}]`")
    L.append("")

    # ---------------------------------------------------------------- reaches
    roi, dl, mods = r["roi"], r["downloads"], r["modules"]
    inp = mods.get("input", {}).get("classes", {})
    L += ["## Reaches", ""]
    if roi.get("roi"):
        L.append(f"- **Reaches of interest (ROI file):** {roi['roi']:,}.")
    if roi.get("expanded") is not None:
        found = roi.get("roi", 0) - roi.get("roi_not_in_expanded", 0)
        L.append(
            f"- **After expansion:** {roi['expanded']:,} reaches. setfinder groups reaches into sets of neighbours "
            f"(set-based algorithms such as MetroMan need whole sets) and adds the other reaches of each set. "
            + (
                f"{_share(found, roi['roi'])} ROI reaches were placed in a set; the rest are not run "
                f"(about 88% is normal)."
                if roi.get("roi")
                else ""
            )
        )
    if roi.get("reaches") is not None:
        L.append(f"- **Run by the modules:** {roi['reaches']:,} reaches (reaches.json).")
    if dl.get("swot_files") is not None:
        nod = inp.get("NODATA", 0)
        L.append(
            f"- **With SWOT data:** {dl['swot_files']:,} reaches got a SWOT file"
            + (f"; {nod:,} had no observations in the time range" if nod else "")
            + "."
        )
    L.append("")

    # ---------------------------------------------------------------- module results
    any_unknown = any(m["classes"].get("MISSING") for m in mods.values())
    L += ["## Results per module", ""]
    L.append(
        "One row per module. A unit is what the module processes in one go: a reach for most modules, a set for "
        "MetroMan, a continent for setfinder, metroman_consolidation and output, a single job for combine_data."
    )
    L.append("")
    rows = []
    for name, m in mods.items():
        cl = m["classes"]
        one, many = UNIT_WORD.get(m.get("scale"), ("unit", "units"))
        probed = f"{m['probed_with_data']}/{m['probed']}" if m["probed"] else ""
        row = [
            name,
            f"{m['units']:,} {one if m['units'] == 1 else many}",
            _n(m["outputs_present"]),
            probed,
            _pct(m["coverage_est"]) if m["coverage_est"] is not None else "",
            _n(cl.get("OK", 0)),
            _n(cl.get("NODATA", 0)),
            _n(cl.get("EXPLAINED", 0)),
            _n(cl.get("STUB", 0)),
            _n(sum(cl.get(k, 0) for k in BAD_CLASSES)),
        ]
        if any_unknown:
            row.append(_n(cl.get("MISSING", 0)))
        rows.append(row)
    headers = [
        "module",
        "units",
        "files written",
        "files checked: with data",
        "coverage",
        "OK",
        "No data",
        "Upstream missing",
        "Empty output",
        "Failed",
    ]
    if any_unknown:
        headers.append("Unknown")
    L += [report.md_table(rows, headers), ""]
    if r.get("not_run"):
        L += [f"Not run (the run stopped before reaching them): {', '.join(r['not_run'])}.", ""]
    L += ["**How to read this table**", ""]
    L.append("- **files written**: output files the module wrote.")
    L.append(
        f"- **files checked: with data**: the audit opens a random sample of up to {opts['sample']} of those files "
        "and counts how many hold real values."
    )
    L.append(
        "- **coverage**: estimated share of units that had their inputs and ended with a usable output "
        "(files written × share of checked files with data, divided by units with inputs). Low coverage is "
        "normal for validation (only gauged reaches) and for algorithms that skip reaches they can't solve."
    )
    L.append("- The remaining columns sort every unit into one outcome:")
    for k, v in CLASS_LEGEND:
        if k == "Unknown" and not any_unknown:
            continue
        L.append(f"    - **{k}**: {v}")
    if not any_unknown:
        L.append(
            "- Outcomes come from the per-unit records the module scripts write (`monitor/units/`: start, end and "
            "exit code for every reach), so 'no data' and 'failed' can be told apart."
        )
    else:
        L.append(
            "- Modules without per-unit records (runs made before monitoring existed) can't tell 'no data' from "
            "'failed'; those units are counted as Unknown."
        )
    L.append("")

    if r.get("baseline"):
        L += ["## Compared with the baseline run", "", f"Baseline: `{r['baseline']['run_dir']}`", ""]
        rows = [
            [
                m,
                *d["outputs_present"],
                _pct(d["coverage_est"][0]),
                _pct(d["coverage_est"][1]),
                d["lost_vs_baseline"],
                d["gained_vs_baseline"],
            ]
            for m, d in r["baseline"]["modules"].items()
        ]
        L.append(
            report.md_table(
                rows,
                [
                    "module",
                    "files written",
                    "baseline files",
                    "coverage",
                    "baseline coverage",
                    "reaches lost",
                    "reaches gained",
                ],
            )
        )
        L.append("")

    # ---------------------------------------------------------------- downloads
    L += ["## SWOT download", ""]
    if dl.get("swot_files") is not None:
        n_reach = roi.get("reaches") or 0
        L.append(
            f"- {_share(dl['swot_files'], n_reach) if n_reach else _n(dl['swot_files'])} reaches have a SWOT file."
        )
        if inp.get("NODATA"):
            L.append(f"- {inp['NODATA']:,} reaches have no file because Hydrocron had no observations for them.")
        if inp.get("STUB"):
            L.append(f"- {_plural(inp['STUB'], 'SWOT file holds', 'SWOT files hold')} no valid observations.")
        L.append(f"- Empty (0-byte) files: {dl.get('zero_byte', 0):,}.")
    http = dl.get("http") or {}
    names = {
        "5xx_retry": "server errors (retried)",
        "429": "rate-limit hits",
        "4xx_client_error": "client errors (bad collection name or query)",
        "connection_retry": "connection retries",
        "hydrocron_error": "unexpected Hydrocron replies",
    }
    if http:
        L.append("- Hydrocron problems: " + ", ".join(f"{v:,} {names.get(k, k)}" for k, v in http.items()) + ".")
    else:
        L.append("- Hydrocron problems: none (no server errors, rate limits or retries in the input logs).")
    if dl.get("missing_without_nodata_message"):
        L.append(
            f"- {dl['missing_without_nodata_message']:,} reaches have no SWOT file and no 'no data' message "
            "(possible silent download failure)."
        )
    if dl.get("skip_existing_messages"):
        L.append(f"- {dl['skip_existing_messages']:,} files were reused from an earlier download attempt.")
    if dl.get("sampled_time_span"):
        t0, t1 = dl["sampled_time_span"]
        line = f"- Observation dates (in a sample of files): {t0} to {t1}"
        if dl.get("configured_time_range"):
            c0, c1 = dl["configured_time_range"]
            line += f"; requested {c0} to {c1}"
        L.append(line + ". SWOT science data starts 2023-03-29.")
    L.append("")

    # ---------------------------------------------------------------- jobs
    if r["jobs"]:
        L += ["## Slurm jobs", ""]
        rows = []
        for m, j in r["jobs"].items():
            st = j["states"]
            n = j["tasks"]
            failed = st.get("FAILED", 0)
            other = {k: v for k, v in st.items() if k not in ("COMPLETED", "FAILED")}
            limit_min = None if j["time_limit_s"] is None else round(j["time_limit_s"] / 60)
            rows.append(
                [
                    m,
                    n,
                    f"{failed} ({failed / n:.0%})" if n else failed,
                    ", ".join(f"{v} {k.lower().replace('_', ' ')}" for k, v in other.items()),
                    j["cpu_hours"],
                    f"{_n(j['max_rss_gb'])} / {_n(j['req_mem_gb'])}",
                    f"{round(j['max_elapsed_s'] / 60, 1)} / {_n(limit_min)}",
                ]
            )
        L.append(
            report.md_table(
                rows,
                [
                    "module",
                    "tasks",
                    "failed",
                    "other problems",
                    "CPU hours",
                    "peak / requested memory (GB)",
                    "longest / limit (min)",
                ],
            )
        )
        L += [
            "",
            "A task is one Slurm array job and processes a batch of units (`hpc.reach_chunks`). Slurm marks a "
            "task failed only when its last unit failed, so these counts are rough; the per-unit outcomes above "
            "are exact. 'Other problems' lists "
            "tasks that timed out, ran out of memory, lost their node or were cancelled.",
            "",
        ]

    # ---------------------------------------------------------------- errors
    L += ["## Errors", ""]
    L.append(
        "The most common error messages per module. Each count is the number of units that hit the error; the "
        "same message on different reaches is counted together (numbers and file paths are ignored when "
        "comparing). The quoted message is the full error from one example log."
    )
    L.append("")
    for name, m in mods.items():
        sigs = m["top_signatures"][:5]
        if not sigs:
            continue
        no_swot = [s for s in sigs if _is_no_swot(s.get("excerpt") or s["signature"])]
        L += [f"### {name}", ""]
        for s in [s for s in sigs if s not in no_swot] + no_swot:
            fatal = " **FATAL: the module can't run at all.**" if s["fatal"] else ""
            note = " Expected: these are the reaches without a SWOT file." if s in no_swot else ""
            L.append(f"**{s['count']:,}×**{fatal}{note} Example log: `{_rel(s['example'], r['run_dir'])}`")
            L += ["", "```", s.get("excerpt") or s["signature"], "```", ""]

    drv = r["driver"]
    L += ["## Driver", ""]
    if drv.get("abort"):
        L.append(f"The run was stopped: {drv['abort'].get('reason')}")
    elif drv.get("driver_exit") == "done":
        L.append("The driver finished normally: every module was submitted and passed its checks.")
    else:
        L.append(f"Driver state: {drv.get('driver_exit')}.")
    return "\n".join(L) + "\n"


def exit_code(verdict: str) -> int:
    return EXIT_CODES.get(verdict, 2)
