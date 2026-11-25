"""Run the generated module scripts on a workstation instead of through slurm.

Rendered with `scheduler: local` the module scripts have no #SBATCH header, and the reach fan-out is
driven entirely by the OFFSET, INDEX_RANGE, MAX_LIMIT and SLURM_ARRAY_TASK_ID environment variables,
which this runner sets itself. It follows the slurm driver step by step so the run monitor works the
same way for both schedulers:

- the same module plan (`scripts.module_plan`): index file, reach_chunks and canary per module, with
  job counts read just before each module runs (setfinder writes reaches.json partway through a run);
- one row per "array" in monitor/submissions.tsv, task logs named report/<module>.<array>_<task>.out,
  and the per-unit ledger written by cfl_lib.sh;
- the gate after a canary and after each module, which can abort the run;
- monitor/ABORT (`python -m confluence.monitor abort`) and monitor/concurrent_jobs
  (`python -m confluence.monitor throttle`) take effect while tasks are running;
- monitor/driver.exit records how the run ended, and the run is audited at the end.

There is no in-flight watcher (it needs squeue) and no batching: after its canary, a module's tasks
run as one array, at most local.concurrent_jobs at a time.

Ctrl-C (or SIGTERM / SIGHUP) stops the run: no new tasks start and the running ones are killed. Each
task runs in its own session, so killing its process group takes the apptainer processes with it.
"""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess as sp
import time
from collections import Counter, deque
from pathlib import Path

from confluence.monitor import report
from confluence.monitor.ledger import task_log_path
from confluence.utils.config import Config
from confluence.utils.scripts import module_plan

POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 10
# Time between SIGTERM and SIGKILL when stopping running tasks (a second Ctrl-C skips the wait).
KILL_GRACE_SECONDS = 10
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class RunStopped(Exception):
    """The run has to stop. state is what goes into driver.exit: 'aborted' or 'terminated'."""

    def __init__(self, state: str, reason: str):
        super().__init__(reason)
        self.state = state
        self.reason = reason


def _json_length(path: Path) -> int | None:
    """Length of a json array, or None if the file is absent, empty or unreadable."""
    try:
        if path.stat().st_size == 0:
            return None
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return len(data) if isinstance(data, list) else None


def _signal_group(proc: sp.Popen, sig: int):
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


class LocalRunner:
    def __init__(self, cfg: Config):
        # Imported here, once, so a long run keeps one consistent snapshot of the monitor code.
        from confluence.monitor.audit import run_audit
        from confluence.monitor.gate import EXIT_ABORT, run_gate

        self._run_gate, self._gate_abort, self._run_audit = run_gate, EXIT_ABORT, run_audit
        self.cfg = cfg
        self.run_dir = Path(cfg.dirs["run"])
        self.mon = self.run_dir / "monitor"
        self.driver_job = f"local-{int(time.time())}"
        self.log_path = Path(cfg.dirs["log"]) / f"{cfg.run_name}_{self.driver_job}.out"
        self.n_arrays = 0
        self.running: dict[int, tuple[sp.Popen, object]] = {}
        # Task exit codes per module, plus "killed" for tasks stopped with the run.
        self.outcomes: dict[str, Counter] = {}
        self.current_module: str | None = None
        self.stop: tuple[str, str] | None = None
        self.force_kill = False
        self._last_beat = 0.0

    # ------------------------------------------------------------------ bookkeeping
    def log(self, msg: str):
        line = f"[{time.strftime('%F %T')}] {msg}"
        print(line, flush=True)
        try:
            with open(self.log_path, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def heartbeat(self, force: bool = False):
        now = time.monotonic()
        if force or now - self._last_beat >= HEARTBEAT_SECONDS:
            (self.mon / "driver.heartbeat").touch()
            self._last_beat = now

    def _on_signal(self, signum, frame):
        if self.stop is None:
            why = "interrupted (Ctrl-C)" if signum == signal.SIGINT else f"received {signal.Signals(signum).name}"
            self.stop = ("terminated", why)
        else:
            self.force_kill = True

    def check_stop(self):
        if self.stop is not None:
            raise RunStopped(*self.stop)
        if (self.mon / "ABORT").exists():
            ab = report.read_abort(self.mon) or {}
            raise RunStopped("aborted", str(ab.get("reason") or "monitor/ABORT is present"))

    def abort(self, reason: str):
        report.abort(self.mon, "driver", reason)
        raise RunStopped("aborted", reason)

    def concurrency(self, module: str) -> int:
        local = self.cfg.local
        base = local.concurrent_jobs or os.cpu_count() or 1
        override = report.read_concurrent_jobs(self.mon)
        if override is not None and override > 0:
            base = override
        cap = local.module_concurrency.get(module)
        if cap is None:
            return base
        # module_concurrency replaces concurrent_jobs for its module; a throttle can lower it, never raise it.
        return min(cap, override) if override is not None and override > 0 else cap

    # ------------------------------------------------------------------ tasks
    def start_task(
        self, module: str, script: Path, array_id: str, task: int, offset: int, index_range: int, max_limit: int
    ):
        env = {
            **os.environ,
            "SLURM_ARRAY_JOB_ID": array_id,
            "SLURM_JOB_ID": array_id,
            "SLURM_ARRAY_TASK_ID": str(task),
            "OFFSET": str(offset),
            "INDEX_RANGE": str(index_range),
            "MAX_LIMIT": str(max_limit),
        }
        log = open(task_log_path(self.cfg.dirs["report"], module, array_id, str(task)), "w")
        try:
            proc = sp.Popen(["bash", str(script)], stdout=log, stderr=sp.STDOUT, env=env, start_new_session=True)
        except BaseException:
            log.close()
            raise
        self.running[task] = (proc, log)

    def kill_running(self):
        if not self.running:
            return
        self.log(f"Stopping {len(self.running)} running tasks")
        procs = [p for p, _ in self.running.values()]
        for p in procs:
            _signal_group(p, signal.SIGTERM)
        deadline = time.monotonic() + KILL_GRACE_SECONDS
        while not self.force_kill and time.monotonic() < deadline and any(p.poll() is None for p in procs):
            time.sleep(0.2)
        # Also for groups whose leader (bash) already exited: its children may still be alive.
        for p in procs:
            _signal_group(p, signal.SIGKILL)
        for p, log in self.running.values():
            p.wait()
            log.close()
        if self.current_module is not None:
            self.outcomes.setdefault(self.current_module, Counter())["killed"] += len(self.running)
        self.running.clear()

    def run_array(
        self,
        module: str,
        phase: str,
        offset: int,
        index_range: int,
        max_limit: int,
        n_tasks: int,
        first: int,
        last: int,
    ):
        """Run one 'array' (n_tasks tasks of the module script) to completion; the slurm driver's submit+wait."""
        self.check_stop()
        self.n_arrays += 1
        array_id = f"{self.driver_job}-{self.n_arrays}"
        script = Path(self.cfg.dirs["sh_scripts"]) / f"{module}.sh"
        row = [
            int(time.time()),
            self.driver_job,
            module,
            phase,
            array_id,
            offset,
            index_range,
            n_tasks,
            first,
            last,
            max_limit,
        ]
        with open(self.mon / "submissions.tsv", "a") as f:
            f.write("\t".join(map(str, row)) + "\n")
        report.event(self.mon, "driver", f"submitted {module} {phase} {array_id} {first}-{last}")
        self.log(
            f"Started {phase} array {array_id} for {module}: {n_tasks} tasks "
            f"(at most {self.concurrency(module)} at once), indices {first}-{last}"
        )

        started = time.monotonic()
        pending = deque(range(n_tasks))
        exits: Counter = Counter()
        module_exits = self.outcomes.setdefault(module, Counter())
        self.current_module = module
        while pending or self.running:
            self.check_stop()
            limit = self.concurrency(module)
            while pending and len(self.running) < limit:
                self.start_task(module, script, array_id, pending.popleft(), offset, index_range, max_limit)
            for task, (proc, log) in list(self.running.items()):
                rc = proc.poll()
                if rc is not None:
                    log.close()
                    del self.running[task]
                    exits[rc] += 1
                    module_exits[rc] += 1
            self.heartbeat()
            time.sleep(POLL_SECONDS)

        # cfl_finish: 0 = every unit ok, 3 = some units failed, 4 = every unit failed; anything else = the task died.
        other = sum(n for rc, n in exits.items() if rc not in (0, 3, 4))
        self.log(
            f"Array {array_id} finished in {time.monotonic() - started:.0f}s: {exits[0]} tasks ok, "
            f"{exits[3]} with failed units, {exits[4]} all units failed, {other} died"
        )

    def gate(self, module: str, phase: str):
        if not self.cfg.monitor.enabled:
            return
        self.log(f"Gate: {module} ({phase})")
        # The gate can wait on files for a while; let Ctrl-C interrupt it directly (no tasks are running).
        signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            rc = self._run_gate(str(self.run_dir), module, phase, self.driver_job)
        except KeyboardInterrupt:
            raise RunStopped("terminated", "interrupted (Ctrl-C)") from None
        except Exception as e:  # a monitoring bug must never stop a healthy run
            self.log(f"WARNING: gate for {module} crashed ({type(e).__name__}: {e}); continuing without it")
            report.event(self.mon, "driver", f"gate_crashed {module} {phase}")
            return
        finally:
            signal.signal(signal.SIGINT, self._on_signal)
        if rc == self._gate_abort:
            if not (self.mon / "ABORT").exists():
                self.abort(f"gate requested abort for {module} ({phase})")
            self.check_stop()

    # ------------------------------------------------------------------ modules
    def run_fixed(self, m: dict):
        count = m["count"]
        self.run_array(m["name"], "fixed", 0, 1, count, count, 0, count - 1)
        self.gate(m["name"], "final")

    def run_reach(self, m: dict):
        module, index_json, index_range = m["name"], Path(m["index_json"]), m["index_range"]
        num_jobs = _json_length(index_json)
        if not num_jobs:
            self.abort(f"index file {index_json} for {module} is missing or empty (did an upstream module fail?)")
        self.log(f"Using {index_json.name} job count ({num_jobs}) for {module}")
        if self.cfg.max_reaches and num_jobs > self.cfg.max_reaches:
            self.log(f"Limiting job count for {module} from {num_jobs} to {self.cfg.max_reaches} (test mode)")
            num_jobs = self.cfg.max_reaches

        start = 0
        total_tasks = math.ceil(num_jobs / index_range)
        canary = m["canary_tasks"]
        if self.cfg.monitor.enabled and canary > 0 and total_tasks >= 3 * canary:
            start = canary * index_range
            self.run_array(module, "canary", 0, index_range, num_jobs, canary, 0, start - 1)
            self.gate(module, "canary")
        if start < num_jobs:
            n_tasks = math.ceil((num_jobs - start) / index_range)
            self.run_array(module, "batch", start, index_range, num_jobs, n_tasks, start, num_jobs - 1)
        self.gate(module, "final")

    # ------------------------------------------------------------------ run
    def _prepare(self):
        self.mon.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Same housekeeping as the slurm driver: an old ABORT would stop this run at once, and a throttle
        # set for an earlier run does not carry over.
        if (self.mon / "ABORT").exists():
            (self.mon / "ABORT").rename(self.mon / f"ABORT.{time.strftime('%Y%m%dT%H%M%S')}.prev")
            self.log("Moved a previous ABORT file aside")
        if (self.mon / "concurrent_jobs").exists():
            self.log("Ignoring the concurrent_jobs throttle set for a previous run")
            (self.mon / "concurrent_jobs").unlink()
        (self.mon / "driver.exit").unlink(missing_ok=True)
        self.heartbeat(force=True)
        report.event(self.mon, "driver", f"started driver {self.driver_job}")

    def run(self) -> int:
        self._prepare()
        self.log(f"Run: {self.cfg.run_name} (local driver {self.driver_job}); task logs in {self.cfg.dirs['report']}")
        previous = {s: signal.signal(s, self._on_signal) for s in STOP_SIGNALS}
        state, reason = "done", ""
        try:
            n_continents = _json_length(Path(self.cfg.dirs["input"]) / "continent.json") or 1
            for m in module_plan(self.cfg, n_continents):
                self.check_stop()
                self.log(f"Starting module: {m['name']}")
                if m["kind"] == "fixed":
                    self.run_fixed(m)
                else:
                    self.run_reach(m)
        except RunStopped as e:
            state, reason = e.state, e.reason
            self.log(f"Run {self.cfg.run_name} stopping: {reason}")
        except BaseException as e:
            state, reason = f"lost (local runner error: {type(e).__name__}: {e})", ""
            raise
        finally:
            self.kill_running()
            report.write_atomic(self.mon / "driver.exit", state + "\n")
            report.event(self.mon, "driver", state if not reason else f"{state}: {reason}")
            for s, handler in previous.items():
                signal.signal(s, handler)

        self._summary(state)
        self._audit(state)
        return 0 if state == "done" else 3

    def _summary(self, state: str):
        self.log("=" * 60)
        for module, exits in self.outcomes.items():
            killed = exits["killed"]
            not_ok = sum(exits.values()) - exits[0] - killed
            parts = [f"{exits[0]} ok"] + [f"{n} {what}" for n, what in ((not_ok, "not ok"), (killed, "killed")) if n]
            self.log(f"  {module:<30} " + ", ".join(parts))
        self.log("=" * 60)
        self.log(f"Run {self.cfg.run_name} {'has finished' if state == 'done' else state}.")

    def _audit(self, state: str):
        cmd = f"python -m confluence.monitor audit {self.run_dir}"
        mcfg = self.cfg.monitor
        if not (mcfg.enabled and mcfg.watcher) or state == "terminated":
            self.log(f"Audit: {cmd}")
            return
        self.log("Auditing the run (Ctrl-C skips it)")
        try:
            verdict, _ = self._run_audit(str(self.run_dir), use_sacct=False)
        except KeyboardInterrupt:
            self.log(f"Audit skipped. Run it later with: {cmd}")
            return
        except Exception as e:
            self.log(f"WARNING: audit failed ({type(e).__name__}: {e}). Retry with: {cmd}")
            return
        report.event(self.mon, "driver", f"audit verdict: {verdict}")
        self.log(f"Audit verdict: {verdict}; report: {self.mon / 'audit.html'}")


def run_local(cfg: Config) -> int:
    """Run every module of the run in this process. Returns 0 if the run finished, 3 if it was stopped."""
    return LocalRunner(cfg).run()
