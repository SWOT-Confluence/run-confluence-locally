"""CLI: python -m confluence.monitor {gate,watch,finish,status,audit,guide,locate,abort} ..."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m confluence.monitor", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gate", help="(driver) check a finished canary/batch/module; exit 2 = abort")
    g.add_argument("--run-dir", required=True)
    g.add_argument("--module", required=True)
    g.add_argument("--phase", choices=["canary", "batch", "final"], default="final")
    g.add_argument("--driver-job", default=None)

    w = sub.add_parser("watch", help="(driver) watch a running driver's arrays and keep status current")
    w.add_argument("--run-dir", required=True)
    w.add_argument("--driver-job", required=True)
    w.add_argument("--poll", type=int, default=None)
    w.add_argument("--driver-pid", type=int, default=None, help="watch this process instead of the driver job")
    w.add_argument("--no-audit", action="store_true", help="leave the audit to the finish job")

    fi = sub.add_parser("finish", help="(audit job, afterany on the driver) record how the driver ended, audit")
    fi.add_argument("--run-dir", required=True)
    fi.add_argument("--driver-job", required=True)

    s = sub.add_parser("status", help="print the live status of a run")
    s.add_argument("run_dir")
    s.add_argument("--json", action="store_true")

    a = sub.add_parser("audit", help="post-run meta-validation (exit 0 PASS, 1 WARN, 2 FAIL)")
    a.add_argument("run_dir")
    a.add_argument("--baseline", default=None, help="another run dir to compare against")
    a.add_argument("--out", default=None, help="write audit/guide here instead of <run>/monitor and <run>")
    a.add_argument("--sample", type=int, default=300, help="files opened per module (default 300)")
    a.add_argument("--full", action="store_true", help="open every output file")
    a.add_argument("--no-sacct", action="store_true")
    a.add_argument("--hash-sifs", action="store_true", help="sha256 every .sif (slow)")

    gd = sub.add_parser("guide", help="(re)write RUN_GUIDE.md")
    gd.add_argument("run_dir")
    gd.add_argument("--out", default=None)

    lo = sub.add_parser("locate", help="where a reach is in every module (index, class, output, log)")
    lo.add_argument("run_dir")
    lo.add_argument("reach_id")
    lo.add_argument("--json", action="store_true")

    ab = sub.add_parser("abort", help="request an abort of a running run (driver + monitor honour it)")
    ab.add_argument("--run-dir", required=True)
    ab.add_argument("--reason", required=True)
    ab.add_argument("--source", default="user")

    th = sub.add_parser("throttle", help="change how many array tasks of a running run may run at once")
    th.add_argument("run_dir")
    th.add_argument("n", nargs="?", type=int, help="new limit (omit to show the current one)")
    th.add_argument("--reset", action="store_true", help="go back to concurrent_jobs from the config")

    args = p.parse_args(argv)

    if args.cmd == "gate":
        from confluence.monitor.gate import run_gate

        return run_gate(args.run_dir, args.module, args.phase, args.driver_job)

    if args.cmd == "watch":
        from confluence.monitor.watch import Watcher

        return Watcher(
            args.run_dir, args.driver_job, poll=args.poll, driver_pid=args.driver_pid, audit=not args.no_audit
        ).run()

    if args.cmd == "finish":
        from confluence.monitor.watch import finish_run

        return finish_run(args.run_dir, args.driver_job)

    if args.cmd == "status":
        from confluence.monitor.context import RunContext
        from confluence.monitor.slurm import Slurm
        from confluence.monitor.status import build_status, render_status

        st = build_status(RunContext(args.run_dir), Slurm().queue(), None)
        print(json.dumps(st, indent=2, default=str) if args.json else render_status(st))
        return 0

    if args.cmd == "audit":
        from confluence.monitor.audit import exit_code, run_audit

        verdict, result = run_audit(
            args.run_dir,
            out_dir=args.out,
            baseline=args.baseline,
            sample=args.sample,
            full=args.full,
            use_sacct=not args.no_sacct,
            hash_sifs=args.hash_sifs,
        )
        where = args.out or f"{result['run_dir']}/monitor"
        print(f"Audit verdict: {verdict}  ({where}/audit.md)")
        for f in result["findings"][:15]:
            print(f"  [{f['severity']}] {f['module']} {f['code']}: {f['message']}")
        return exit_code(verdict)

    if args.cmd == "guide":
        from confluence.monitor.context import RunContext
        from confluence.monitor.guide import write_guide

        ctx = RunContext(args.run_dir, args.out)
        print(write_guide(ctx, out_dir=args.out))
        return 0

    if args.cmd == "locate":
        from confluence.monitor.locate import locate, render_locate

        rows = locate(args.run_dir, args.reach_id)
        print(json.dumps(rows, indent=2, default=str) if args.json else render_locate(rows, args.reach_id))
        return 0

    if args.cmd == "abort":
        from pathlib import Path

        from confluence.monitor.report import abort

        created = abort(Path(args.run_dir) / "monitor", args.source, args.reason)
        print("ABORT written" if created else "ABORT already present (first reason kept)")
        return 0

    if args.cmd == "throttle":
        return _throttle(args.run_dir, args.n, args.reset)

    return 1


def _throttle(run_dir: str, n: int | None, reset: bool) -> int:
    from confluence.monitor.context import RunContext
    from confluence.monitor.report import read_concurrent_jobs, set_concurrent_jobs

    ctx = RunContext(run_dir)
    mon = ctx.monitor_dir
    local = ctx.scheduler == "local"
    key = f"{'local' if local else 'hpc'}.concurrent_jobs"
    configured = ctx.scheduler_settings.get("concurrent_jobs") or ("one per CPU core" if local else None)
    batch_size = None if local else ctx.hpc.get("batch_size")

    if n is None and not reset:
        current = read_concurrent_jobs(mon)
        if current is None:
            print(f"concurrent_jobs: {configured} ({key} in config.yml)")
        else:
            print(f"concurrent_jobs: {current} (set with throttle; {key} in config.yml is {configured})")
        return 0
    if reset:
        set_concurrent_jobs(mon, None)
        print(f"Override removed: the driver goes back to {key} = {configured} within ~10 s.")
        return 0
    if n < 1:
        print("concurrent_jobs must be at least 1 (use 'abort' to stop the run)", file=sys.stderr)
        return 2

    exit_file = mon / "driver.exit"
    if exit_file.exists() or not (mon / "driver.heartbeat").exists():
        state = f"it exited ({exit_file.read_text().strip()})" if exit_file.exists() else "none has started yet"
        print(
            f"No driver is running for this run ({state}). A new driver always starts from {key}, "
            "so change it in the config instead.",
            file=sys.stderr,
        )
        return 1

    previous = read_concurrent_jobs(mon) or configured
    set_concurrent_jobs(mon, n)
    if local:
        print(
            f"concurrent_jobs set to {n} (was {previous}; config: {configured}). The local runner uses it from its "
            "next task start on, for this and every later module."
        )
        caps = ctx.local.get("module_concurrency") or {}
        if caps:
            names = ", ".join(sorted(caps))
            print(f"Modules in local.module_concurrency ({names}) never run more than their own limit.")
    else:
        print(
            f"concurrent_jobs set to {n} (was {previous}; config: {configured}). Within ~10 s the driver applies it "
            "to the array that is running, and it submits every later array with it."
        )
    if isinstance(previous, int) and n < previous:
        print(f"Lowering it does not stop tasks that are already running; new ones start once fewer than {n} run.")
    if batch_size and n > int(batch_size):
        print(f"Note: each array holds at most batch_size = {batch_size} tasks, so no more than that run at once.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
