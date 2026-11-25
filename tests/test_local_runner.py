"""The local (no Slurm) scheduler: rendered scripts, the runner's module plan and monitor records, and
stopping a run (gate abort, monitor abort, Ctrl-C) against a fake apptainer."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time

import pytest

from confluence.monitor import report
from confluence.utils.local_runner import LocalRunner, run_local
from confluence.utils.scripts import write_scripts
from tests.test_scripts import E2E_MODULES, E2E_MONITOR, _swot_all, fake_slurm, make_cfg  # noqa: F401


def make_local_cfg(tmp_path, local=None, momma_chunks=None, **kw):
    def tweak(raw):
        raw.pop("hpc")
        raw["scheduler"] = "local"
        raw["local"] = local or {"concurrent_jobs": 3, "reach_chunks": 20}
        if momma_chunks and "unconstrained_momma" in raw["module_templates"]:
            raw["module_templates"]["unconstrained_momma"]["reach_chunks"] = momma_chunks

    return make_cfg(tmp_path, tweak=tweak, **kw)


def _submissions(cfg):
    return [ln.split("\t") for ln in (cfg.dirs["monitor"] / "submissions.tsv").read_text().splitlines()]


@pytest.fixture
def fake_apptainer(fake_slurm, monkeypatch):  # noqa: F811
    # fake_slurm also puts sbatch/squeue on PATH; the local runner must not need them.
    for name in ("sbatch", "squeue", "scancel", "scontrol"):
        (fake_slurm / "bin" / name).write_text('#!/bin/bash\necho "$0 must not be called" >&2\nexit 99\n')
    return fake_slurm


def test_local_scripts_have_no_slurm_header_or_driver(tmp_path):
    cfg = make_local_cfg(tmp_path)
    assert write_scripts(cfg) is None
    sh = cfg.dirs["sh_scripts"]
    assert not (sh / "slurm_driver.sh").exists() and not (sh / "audit.sh").exists()
    for s in sh.glob("*.sh"):
        assert subprocess.run(["bash", "-n", str(s)], capture_output=True).returncode == 0, s.name
    momma = (sh / "unconstrained_momma.sh").read_text()
    assert "#SBATCH" not in momma
    assert 'cfl_unit "${idx}" apptainer run' in momma and "cfl_finish" in momma
    guide = (cfg.dirs["run"] / "RUN_GUIDE.md").read_text()
    assert "local scheduler" in guide and "kill_jobs.sh" not in guide


def test_local_healthy_run(fake_apptainer, monkeypatch):
    cfg = make_local_cfg(fake_apptainer, modules=E2E_MODULES, monitor=E2E_MONITOR, momma_chunks=50)
    _swot_all(cfg)
    write_scripts(cfg)
    monkeypatch.setenv("FAKE_REACHES", str(cfg.dirs["input"] / "reaches.json"))

    assert run_local(cfg) == 0
    mon = cfg.dirs["monitor"]
    assert (mon / "driver.exit").read_text().strip() == "done"
    assert not (mon / "ABORT").exists()

    subs = _submissions(cfg)
    driver_job = subs[0][1]
    assert driver_job.startswith("local-") and all(s[1] == driver_job for s in subs)
    # (module, phase, first, last, index_range, n_tasks): 100 reaches in chunks of 20 = 5 tasks, a 1-task canary,
    # then the rest in one array; momma's own reach_chunks=50 gives 2 tasks, too few for a canary.
    assert [(s[2], s[3], s[8], s[9], s[6], s[7]) for s in subs] == [
        ("prediagnostics", "canary", "0", "19", "20", "1"),
        ("prediagnostics", "batch", "20", "99", "20", "4"),
        ("sad", "canary", "0", "19", "20", "1"),
        ("sad", "batch", "20", "99", "20", "4"),
        ("unconstrained_momma", "batch", "0", "99", "50", "2"),
    ]
    assert len(list((cfg.dirs["mnt"] / "flpe" / "sad").glob("*_sad.nc"))) == 100

    # Unit ledger and task logs use the array ids from submissions.tsv, like Slurm's %A_%a.
    sad_batch = subs[3][4]
    assert sorted(p.name for p in (mon / "units" / "sad").glob(f"{sad_batch}_*.tsv")) == [
        f"{sad_batch}_{t}.tsv" for t in range(4)
    ]
    log = (cfg.dirs["report"] / f"sad.{sad_batch}_3.out").read_text()
    assert "CFL_TASK_SUMMARY module=sad units=20 failed=0" in log

    events = (mon / "events.log").read_text()
    assert "gate sad [canary]" in events and "gate unconstrained_momma [final]" in events
    assert (mon / "audit.html").exists() and "audit verdict" in events
    assert (cfg.dirs["log"] / f"t_{driver_job}.out").exists()


def test_local_gate_aborts_after_canary(fake_apptainer, monkeypatch):
    cfg = make_local_cfg(fake_apptainer, modules=E2E_MODULES, monitor=E2E_MONITOR)
    _swot_all(cfg)
    write_scripts(cfg)
    monkeypatch.setenv("FAKE_REACHES", str(cfg.dirs["input"] / "reaches.json"))
    monkeypatch.setenv("FAKE_FAIL_SIF", "sad")

    assert run_local(cfg) == 3
    mon = cfg.dirs["monitor"]
    ab = json.loads((mon / "ABORT").read_text())
    assert ab["source"] == "gate" and "sad (canary)" in ab["reason"]
    subs = _submissions(cfg)
    assert [s[3] for s in subs if s[2] == "sad"] == ["canary"]
    assert not any(s[2] == "unconstrained_momma" for s in subs)
    assert (mon / "driver.exit").read_text().strip() == "aborted"
    assert (mon / "audit.html").exists()  # an aborted run is still audited


SLOW_TASK = """#!/bin/bash
echo $$ >> {pids}
sleep 60 &
echo $! >> {pids}
wait
"""


@pytest.mark.parametrize("how", ["ctrl-c", "abort"])
def test_stopping_a_local_run_kills_running_tasks(tmp_path, how):
    local = {"concurrent_jobs": 2, "reach_chunks": 20}
    cfg = make_local_cfg(tmp_path, modules=["sad", "unconstrained_momma"], local=local)
    write_scripts(cfg)
    pids = tmp_path / "pids"
    (cfg.dirs["sh_scripts"] / "sad.sh").write_text(SLOW_TASK.format(pids=pids))

    def stop():
        while not pids.exists() or len(pids.read_text().split()) < 4:  # both tasks and their children started
            time.sleep(0.1)
        if how == "ctrl-c":
            os.kill(os.getpid(), signal.SIGINT)
        else:
            report.abort(cfg.dirs["monitor"], "user", "wrong config")

    threading.Thread(target=stop, daemon=True).start()
    t0 = time.monotonic()
    assert run_local(cfg) == 3
    assert time.monotonic() - t0 < 30, "the run must not wait for running or queued tasks"

    started = [int(p) for p in pids.read_text().split()]
    assert len(started) == 4, "only the first 2 of 5 tasks may start"
    for pid in started:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    mon = cfg.dirs["monitor"]
    assert (mon / "driver.exit").read_text().strip() == ("terminated" if how == "ctrl-c" else "aborted")
    assert not any(s[2] == "unconstrained_momma" for s in _submissions(cfg))
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    progress = next(cfg.dirs["log"].glob("t_local-*.out")).read_text()
    assert "sad                            0 ok, 2 killed" in progress


def test_local_run_aborts_on_missing_index(tmp_path):
    cfg = make_local_cfg(tmp_path, modules=["sad"])
    write_scripts(cfg)
    (cfg.dirs["input"] / "reaches.json").write_text("")
    assert run_local(cfg) == 3
    assert "missing or empty" in json.loads((cfg.dirs["monitor"] / "ABORT").read_text())["reason"]


def test_local_concurrency_and_throttle(tmp_path):
    cfg = make_local_cfg(tmp_path, local={"concurrent_jobs": 8, "module_concurrency": {"output": 4}})
    runner = LocalRunner(cfg)
    mon = cfg.dirs["monitor"]
    assert (runner.concurrency("sad"), runner.concurrency("output")) == (8, 4)
    report.set_concurrent_jobs(mon, 2)
    assert (runner.concurrency("sad"), runner.concurrency("output")) == (2, 2)
    report.set_concurrent_jobs(mon, 10)
    assert (runner.concurrency("sad"), runner.concurrency("output")) == (10, 4)  # never above its own limit
    report.set_concurrent_jobs(mon, None)
    cfg.local.concurrent_jobs = None
    assert runner.concurrency("sad") == (os.cpu_count() or 1)


def test_throttle_cli_on_local_run(tmp_path, capsys):
    from confluence.monitor.__main__ import main

    cfg = make_local_cfg(tmp_path, local={"concurrent_jobs": 6, "module_concurrency": {"output": 2}})
    run, mon = str(cfg.dirs["run"]), cfg.dirs["monitor"]
    assert main(["throttle", run]) == 0
    assert "concurrent_jobs: 6 (local.concurrent_jobs" in capsys.readouterr().out
    (mon / "driver.heartbeat").touch()
    assert main(["throttle", run, "3"]) == 0
    out = capsys.readouterr().out
    assert "local runner" in out and "module_concurrency (output)" in out and "batch_size" not in out
    assert (mon / "concurrent_jobs").read_text() == "3\n"
