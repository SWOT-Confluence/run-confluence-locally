"""Live run status (status.json / status.txt), computed from the ledger, squeue and output dirs."""

from __future__ import annotations

import glob
import time
from collections import Counter

from confluence.monitor import report
from confluence.monitor.context import RunContext
from confluence.monitor.ledger import UnitTail, latest_driver_job, read_submissions, task_log_path
from confluence.monitor.slurm import QueueRow, Slurm, task_id_of


def _count_outputs(ctx: RunContext, module: str) -> int | None:
    spec = ctx.spec(module)
    if not spec.output:
        return None
    return len(glob.glob(str(ctx.path(spec.output, "*"))))


def build_status(
    ctx: RunContext,
    queue: list[QueueRow] | None,
    driver_job: str | None,
    tail: UnitTail | None = None,
    findings_by_module: dict[str, list] | None = None,
) -> dict:
    tail = tail or UnitTail(ctx.monitor_dir)
    now = int(time.time())
    driver_job = driver_job or latest_driver_job(ctx.monitor_dir)
    subs = read_submissions(ctx.monitor_dir, driver_job=driver_job) if driver_job else []
    queue_by_array: dict[str, list[QueueRow]] = {}
    for row in queue or []:
        queue_by_array.setdefault(row.array_id, []).append(row)

    modules = []
    for module in ctx.modules_to_run:
        msubs = [s for s in subs if s.module == module]
        arrays = {s.array_id for s in msubs}
        q_rows = [r for a in arrays for r in queue_by_array.get(a, [])]
        q_states = Counter(r.state for r in q_rows)
        tasks = tail.refresh(module, arrays) if arrays else []
        recs = [r for t in tasks for r in t.records]
        finished = [r for r in recs if r.finished]
        ok = sum(1 for r in finished if r.rc == 0)
        failed = len(finished) - ok
        running = sum(1 for r in recs if not r.finished)
        recent = [r for r in finished if r.end and r.end >= now - 600]
        rate = len(recent) / 10 if recent else 0.0
        spec = ctx.spec(module)
        expected = len(ctx.units(spec.index)) if (spec.index is None or (ctx.input_dir / spec.index).exists()) else None
        if spec.index is None:
            expected = 1
        remaining = None if expected is None else max(expected - len(finished), 0)
        eta_min = round(remaining / rate) if rate and remaining is not None else None

        if not msubs:
            state = "waiting"
        elif q_rows:
            state = "running"
        else:
            state = "done"
        # log age of running tasks (informational: no stall handling)
        ages = []
        for r in q_rows:
            if r.state != "RUNNING":
                continue
            tid = task_id_of(r.job_id)
            if tid is None:
                continue
            lp = task_log_path(ctx.report_dir, module, r.array_id, tid)
            try:
                ages.append(now - int(lp.stat().st_mtime))
            except OSError:
                pass
        modules.append(
            {
                "module": module,
                "state": state,
                "arrays": sorted(arrays),
                "tasks_running": q_states.get("RUNNING", 0),
                "tasks_pending": q_states.get("PENDING", 0),
                "task_files": len(tasks),
                "units_expected": expected,
                "units_ok": ok,
                "units_failed": failed,
                "units_running": running,
                "outputs": _count_outputs(ctx, module) if state != "waiting" else None,
                "rate_per_min": round(rate, 1),
                "eta_min": eta_min,
                "oldest_log_age_s": max(ages) if ages else None,
                "findings": [
                    f.to_dict() if hasattr(f, "to_dict") else f for f in (findings_by_module or {}).get(module, [])
                ],
            }
        )
    ab = report.read_abort(ctx.monitor_dir)
    exit_file = ctx.monitor_dir / "driver.exit"
    return {
        "run_name": ctx.run_name,
        "run_dir": str(ctx.run_dir),
        "updated": report.now_iso(),
        "driver_job": driver_job,
        "driver_exit": exit_file.read_text().strip() if exit_file.exists() else None,
        "abort": ab,
        "squeue_ok": queue is not None,
        "modules": modules,
    }


def render_status(st: dict) -> str:
    head = f"Run {st['run_name']}  driver {st['driver_job']}  updated {st['updated']}"
    if st.get("abort"):
        head += f"\nABORTED ({st['abort'].get('source')}): {st['abort'].get('reason')}"
    elif st.get("driver_exit"):
        head += f"\ndriver exit: {st['driver_exit']}"
    if not st.get("squeue_ok"):
        head += "\n(squeue unavailable: task counts may be stale)"
    rows = []
    for m in st["modules"]:
        rows.append(
            [
                m["module"],
                m["state"],
                f"{m['tasks_running']}/{m['tasks_pending']}",
                m["units_expected"],
                m["units_ok"],
                m["units_failed"],
                m["units_running"],
                m["outputs"],
                m["rate_per_min"] or "",
                m["eta_min"] if m["eta_min"] is not None else "",
                "" if m["oldest_log_age_s"] is None else f"{m['oldest_log_age_s'] // 60}m",
            ]
        )
    table = report.fmt_table(
        rows,
        [
            "module",
            "state",
            "jobs run/wait",
            "total",
            "ok",
            "failed",
            "in progress",
            "outputs",
            "per min",
            "ETA min",
            "log age",
        ],
    )
    notes = []
    for m in st["modules"]:
        for f in m.get("findings", []):
            notes.append(f"[{f['severity']}] {m['module']} {f['code']}: {f['message']}")
    return head + "\n\n" + table + ("\n\n" + "\n".join(notes) if notes else "") + "\n"


def write_status(ctx: RunContext, st: dict):
    report.write_json(ctx.out_dir / "status.json", st)
    report.write_atomic(ctx.out_dir / "status.txt", render_status(st))


def status_now(run_dir: str, slurm: Slurm | None = None) -> str:
    """For `python -m confluence.monitor status`: compute a fresh status without writing files."""
    ctx = RunContext(run_dir)
    q = (slurm or Slurm()).queue()
    return render_status(build_status(ctx, q, None))
