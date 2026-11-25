"""Synchronous gate the driver runs after a canary array, after each batch and after each module.

Exit codes: 0 continue, 2 abort (ABORT written). The driver treats any other code as a gate crash and
continues, so a bug here can never stop a healthy run.
"""

from __future__ import annotations

import time

from confluence.monitor import report
from confluence.monitor.assess import assess_module
from confluence.monitor.context import RunContext
from confluence.monitor.ledger import read_submissions, units_dir
from confluence.monitor.rules import ABORT, module_rules, warning_rules

EXIT_OK, EXIT_ABORT = 0, 2


def _wait_for_unit_files(ctx: RunContext, module: str, arrays: set[str], n_tasks: int, wait_s: int):
    """Unit files are written on compute nodes; give NFS a moment to show all of them."""
    deadline = time.time() + wait_s
    d = units_dir(ctx.monitor_dir, module)
    while True:
        n = sum(1 for p in d.glob("*.tsv") if p.stem.partition("_")[0] in arrays) if d.is_dir() else 0
        if n >= n_tasks or time.time() >= deadline:
            return n
        time.sleep(5)


def run_gate(run_dir: str, module: str, phase: str, driver_job: str | None = None, quiet: bool = False) -> int:
    ctx = RunContext(run_dir)
    mcfg = ctx.monitor
    if mcfg.for_module(module, "gate") is False:
        return EXIT_OK

    subs = read_submissions(ctx.monitor_dir, driver_job=driver_job, module=module)
    if not subs:
        print(f"gate: no submissions recorded for {module}; nothing to check")
        return EXIT_OK
    if phase == "canary":
        scope = [s for s in subs if s.phase == "canary"][-1:]
    elif phase == "batch":
        scope = [s for s in subs if s.phase == "batch"][-1:]
    else:
        scope = subs
    scope = scope or subs[-1:]

    arrays = {s.array_id for s in scope}
    indices: set[int] = set()
    for s in scope:
        indices.update(s.indices())
    n_tasks = sum(s.n_tasks for s in scope)
    seen = _wait_for_unit_files(ctx, module, arrays, n_tasks, mcfg.gate_wait_seconds)

    def _assess():
        a = assess_module(
            ctx,
            module,
            indices=indices,
            arrays=arrays,
            probe_sample=mcfg.gate_probe_sample,
            in_progress=False,
        )
        findings = module_rules(a, mcfg) + warning_rules(a, mcfg, final=(phase == "final"))
        return a, findings, [f for f in findings if f.severity == ABORT]

    a, findings, aborts = _assess()
    # A product written moments ago on a compute node may not be visible here yet (NFS caching).
    # If missing products are the only reason to abort, give them gate_wait_seconds to show up.
    deadline = time.time() + mcfg.gate_wait_seconds
    while aborts and all(f.code == "R4_missing_product" for f in aborts) and time.time() < deadline:
        time.sleep(10)
        a, findings, aborts = _assess()

    s = a.summary()
    cov = "n/a" if s["coverage_est"] is None else f"{s['coverage_est']:.0%}"
    line = (
        f"gate {module} [{phase}] arrays={','.join(sorted(arrays))} task-files={seen}/{n_tasks} "
        f"units={s['units']} classes={s['classes']} fail={s['fail_frac']:.0%} coverage={cov}"
    )
    report.event(ctx.monitor_dir, "gate", line)
    if not quiet:
        print(line)
        for f in findings:
            print(f"  [{f.severity}] {f.code}: {f.message}")
            for sig in f.evidence.get("top_signatures", [])[:3]:
                print(f"      {sig['count']:>5}x {sig['signature']}  ({sig['example']})")
    for f in findings:
        if f.severity != ABORT:
            report.event(ctx.monitor_dir, "gate", f"[{f.severity}] {module} {f.code}: {f.message}")

    if aborts:
        reason = f"{module} ({phase}): " + "; ".join(f"{f.code}: {f.message}" for f in aborts)
        report.abort(ctx.monitor_dir, "gate", reason, [f.to_dict() for f in findings])
        return EXIT_ABORT
    return EXIT_OK
