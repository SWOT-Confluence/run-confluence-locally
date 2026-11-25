"""The run monitor.

`Watcher` runs as a background process inside the driver job: it aborts systemic failures mid-array and
keeps status files current. `finish_run` is the end-of-run audit, run by a small job the driver submits
with --dependency=afterany on itself, so it runs however the driver ends (done, aborted, time limit,
scancel, node failure). No stall/hung-node handling by design.
"""

from __future__ import annotations

import os
import time
from datetime import datetime

# audit/guide are imported eagerly so a long-running monitor keeps one consistent snapshot of the
# code: a lazy import hours later could mix in edits made to the repo since the job started.
from confluence.monitor import audit as _audit
from confluence.monitor import guide as _guide  # noqa: F401  (imported for the snapshot)
from confluence.monitor import report
from confluence.monitor.assess import assess_module
from confluence.monitor.context import RunContext
from confluence.monitor.ledger import UnitTail, read_submissions
from confluence.monitor.rules import ABORT, module_rules
from confluence.monitor.slurm import FATAL_PENDING_REASONS, QueueRow, Slurm
from confluence.monitor.status import build_status, write_status


def _run_jobs(queue: list[QueueRow], run_name: str, own_job: str | None) -> list[QueueRow]:
    suffix = f"_{run_name}_cfl"
    return [
        r
        for r in queue
        if r.name.endswith(suffix) and not r.name.startswith(("monitor_", "audit_")) and r.array_id != (own_job or "")
    ]


def _age_minutes(submit: str) -> float:
    try:
        return (datetime.now() - datetime.fromisoformat(submit)).total_seconds() / 60
    except ValueError:
        return 0.0


class Watcher:
    def __init__(
        self,
        run_dir: str,
        driver_job: str,
        slurm: Slurm | None = None,
        poll: int | None = None,
        driver_pid: int | None = None,
        audit: bool = True,
    ):
        self.ctx = RunContext(run_dir)
        self.driver_job = str(driver_job)
        # Inside the driver job: the driver is our parent process, watched by pid instead of squeue,
        # and the audit is left to the afterany job (finish_run).
        self.driver_pid = driver_pid
        self.audit = audit
        self.slurm = slurm or Slurm()
        self.poll = poll or self.ctx.monitor.poll_seconds
        self.tail = UnitTail(self.ctx.monitor_dir)
        self.own_job = os.environ.get("SLURM_JOB_ID")
        self.abort_seen_polls = 0
        self.cancelled_arrays: set[str] = set()

    # ------------------------------------------------------------------ one poll
    def step(self) -> bool:
        """One monitoring cycle. Returns False when the driver is gone and the watcher should stop."""
        ctx, mcfg = self.ctx, self.ctx.monitor
        queue = self.slurm.queue()
        run_jobs = _run_jobs(queue or [], ctx.run_name, self.own_job)
        live_arrays = {r.array_id for r in run_jobs}
        subs = read_submissions(ctx.monitor_dir, driver_job=self.driver_job)
        findings_by_module: dict[str, list] = {}

        # 1. mid-array rules on modules with arrays still in the queue
        if not (ctx.monitor_dir / "ABORT").exists():
            for module in dict.fromkeys(s.module for s in subs if s.array_id in live_arrays):
                if mcfg.for_module(module, "gate") is False:
                    continue
                msubs = [s for s in subs if s.module == module]
                indices = {i for s in msubs for i in s.indices()}
                a = assess_module(
                    ctx,
                    module,
                    indices=indices,
                    arrays={s.array_id for s in msubs},
                    probe_sample=mcfg.gate_probe_sample,
                    in_progress=True,
                    tail=self.tail,
                )
                f = module_rules(a, mcfg, check_products=False)
                findings_by_module[module] = f
                aborts = [x for x in f if x.severity == ABORT]
                if aborts:
                    reason = f"{module} (mid-array): " + "; ".join(f"{x.code}: {x.message}" for x in aborts)
                    report.abort(ctx.monitor_dir, "monitor", reason, [x.to_dict() for x in f])
                    break

        # 2. jobs stuck pending for a reason that will never clear
        if queue is not None and not (ctx.monitor_dir / "ABORT").exists():
            for r in run_jobs:
                if r.state == "PENDING" and r.reason in FATAL_PENDING_REASONS:
                    if _age_minutes(r.submit) >= mcfg.pending_fatal_minutes:
                        report.abort(
                            ctx.monitor_dir, "monitor", f"job {r.job_id} ({r.name}) pending with reason {r.reason}"
                        )
                        break

        # 3. enforce an abort (from us, the gate or the driver)
        if (ctx.monitor_dir / "ABORT").exists():
            to_cancel = sorted(live_arrays - self.cancelled_arrays)
            if to_cancel:
                report.event(ctx.monitor_dir, "monitor", f"cancelling arrays {to_cancel}")
                self.slurm.scancel(to_cancel)
                self.cancelled_arrays |= set(to_cancel)
            self.abort_seen_polls += 1
            if self.abort_seen_polls > 2 and self.slurm.job_present(self.driver_job):
                report.event(
                    ctx.monitor_dir, "monitor", f"driver {self.driver_job} still running after ABORT; cancelling"
                )
                self.slurm.scancel([self.driver_job])

        # 4. status files
        st = build_status(ctx, queue, self.driver_job, self.tail, findings_by_module)
        write_status(ctx, st)

        # 5. driver gone?
        if self.driver_pid is not None:
            return _pid_alive(self.driver_pid)
        present = self.slurm.job_present(self.driver_job)
        if present is False:
            exit_file = ctx.monitor_dir / "driver.exit"
            if not exit_file.exists() and not (ctx.monitor_dir / "ABORT").exists():
                report.event(
                    ctx.monitor_dir,
                    "monitor",
                    f"driver_crashed: driver {self.driver_job} left the queue without driver.exit or ABORT "
                    "(time limit? see the driver .err log)",
                )
            return False
        return True

    def run(self) -> int:
        report.event(self.ctx.monitor_dir, "monitor", f"watching driver {self.driver_job} every {self.poll}s")
        while True:
            try:
                keep_going = self.step()
            except Exception as e:  # a monitoring bug must not take the watcher down silently
                report.event(self.ctx.monitor_dir, "monitor", f"poll error: {type(e).__name__}: {e}")
                keep_going = True
            if not keep_going:
                break
            time.sleep(self.poll)

        aborted = (self.ctx.monitor_dir / "ABORT").exists()
        if self.audit:
            report.event(self.ctx.monitor_dir, "monitor", "driver finished; running audit")
            _run_audit_logged(self.ctx)
        return 1 if aborted else 0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _run_audit_logged(ctx: RunContext):
    try:
        verdict, _ = _audit.run_audit(str(ctx.run_dir))
        report.event(ctx.monitor_dir, "monitor", f"audit verdict: {verdict}")
    except Exception as e:
        report.event(ctx.monitor_dir, "monitor", f"audit failed: {type(e).__name__}: {e}")


def _visible(path) -> bool:
    """exists() that does not trust a cached negative NFS lookup (the file was written on another node)."""
    return path.exists() or path.name in os.listdir(path.parent)


def finish_run(run_dir: str, driver_job: str, slurm: Slurm | None = None) -> int:
    """End-of-run job: record how the driver ended, write a final status and audit the run.

    If the driver left no driver.exit and no ABORT it was killed without a chance to clean up (SIGKILL
    after the time limit, node failure, OOM): write driver.exit from its Slurm state so the audit flags it.
    """
    ctx = RunContext(run_dir)
    slurm = slurm or Slurm()
    mon = ctx.monitor_dir
    exit_file = mon / "driver.exit"
    if not _visible(exit_file) and not _visible(mon / "ABORT"):
        rows = [r for r in slurm.sacct([str(driver_job)]) if r.get("JobID") == str(driver_job)]
        state = rows[0].get("State", "unknown") if rows else "unknown"
        report.write_atomic(exit_file, f"lost (Slurm state {state})\n")
        report.event(
            mon,
            "monitor",
            f"driver_crashed: driver {driver_job} ended without driver.exit or ABORT "
            f"(Slurm state {state}; see the driver .err log)",
        )
    try:
        write_status(ctx, build_status(ctx, slurm.queue(), str(driver_job)))
    except Exception as e:
        report.event(mon, "monitor", f"final status failed: {type(e).__name__}: {e}")
    report.event(mon, "monitor", f"driver {driver_job} finished; running audit")
    _run_audit_logged(ctx)
    return 0
