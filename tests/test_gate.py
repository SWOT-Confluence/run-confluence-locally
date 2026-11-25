import json

import numpy as np

from confluence.monitor import report
from confluence.monitor.assess import assess_module
from confluence.monitor.context import RunContext
from confluence.monitor.gate import EXIT_ABORT, EXIT_OK, run_gate

MOMMA_OUT = "flpe/momma/{unit}_momma.nc"
FAST = {"gate_wait_seconds": 0}


def _canary(run, module="unconstrained_momma", array="100", n=40):
    run.submit(module, array, "canary", 0, n - 1)


def test_healthy_canary_with_nodata_cascade_passes(make_run):
    run = make_run(monitor=FAST)
    no_swot = set(run.reach_ids[:5])
    run.swot([r for r in run.reach_ids if r not in no_swot])
    run.output(MOMMA_OUT, run.reach_ids[5:38])  # 2 reaches "decided not to run"
    _canary(run)
    for task in (0, 1):
        items, lines = [], {}
        for idx in range(task * 20, task * 20 + 20):
            if idx < 5:
                items.append((idx, 1))
                lines[idx] = ["Error in RNetCDF::open.nc(swot_file) : No such file or directory", "Execution halted"]
            elif idx >= 38:
                items.append((idx, 0))
                lines[idx] = ['[1] "decided not to run"']
            else:
                items.append((idx, 0))
        run.units("unconstrained_momma", "100", task, items, lines)

    assert run_gate(str(run.run_dir), "unconstrained_momma", "canary", "999", quiet=True) == EXIT_OK
    assert not (run.monitor / "ABORT").exists()
    a = assess_module(RunContext(run.run_dir), "unconstrained_momma", indices=set(range(40)), arrays={"100"})
    c = a.counts
    assert c["EXPLAINED"] == 5 and c["NODATA"] == 2 and c["OK"] == 33
    assert a.bad == 0


def test_fatal_canary_aborts(make_run):
    run = make_run(monitor=FAST)
    run.swot()
    _canary(run)
    fatal = ["FATAL:   could not open image /run/sif/momma.sif: failed to retrieve path: no such file"]
    for task in (0, 1):
        idxs = range(task * 20, task * 20 + 20)
        run.units("unconstrained_momma", "100", task, [(i, 255) for i in idxs], {i: fatal for i in idxs})

    assert run_gate(str(run.run_dir), "unconstrained_momma", "canary", "999", quiet=True) == EXIT_ABORT
    ab = json.loads((run.monitor / "ABORT").read_text())
    assert ab["source"] == "gate"
    assert "R1_error_storm" in ab["reason"] and "R3_fatal_signature" in ab["reason"]
    events = (run.monitor / "events.log").read_text()
    assert "ABORT" in events


def test_low_yield_stubs_abort(make_run):
    run = make_run(monitor=FAST, modules=["hivdi"])
    run.swot()
    run.output("flpe/hivdi/{unit}_hivdi.nc", run.reach_ids, stub=True)
    run.submit("hivdi", "200", "canary", 0, 39)
    for task in (0, 1):
        run.units("hivdi", "200", task, [(i, 0) for i in range(task * 20, task * 20 + 20)])
    assert run_gate(str(run.run_dir), "hivdi", "canary", "999", quiet=True) == EXIT_ABORT
    assert "R2_low_yield" in json.loads((run.monitor / "ABORT").read_text())["reason"]


def test_input_silent_failures_abort(make_run):
    """Rejected hydrocron collection: every reach exits 0, writes nothing, logs a client error."""
    run = make_run(monitor=FAST, modules=["input"])
    run.submit("input", "300", "canary", 0, 39)
    err = ["root ERROR Client error, will not retry. Exiting... status = 400"]
    for task in (0, 1):
        idxs = range(task * 20, task * 20 + 20)
        run.units("input", "300", task, [(i, 0) for i in idxs], {i: err for i in idxs})
    assert run_gate(str(run.run_dir), "input", "canary", "999", quiet=True) == EXIT_ABORT
    a = assess_module(RunContext(run.run_dir), "input", arrays={"300"})
    assert a.counts["SILENT"] == 40


def test_input_nodata_reaches_are_not_failures(make_run):
    run = make_run(monitor=FAST, modules=["input"])
    run.swot(run.reach_ids[3:])
    run.submit("input", "300", "canary", 0, 39)
    msg = {i: [f"root INFO No data returned for reach_id = {run.reach_ids[i]}, moving on."] for i in range(3)}
    for task in (0, 1):
        run.units("input", "300", task, [(i, 0) for i in range(task * 20, task * 20 + 20)], msg)
    assert run_gate(str(run.run_dir), "input", "canary", "999", quiet=True) == EXIT_OK
    a = assess_module(RunContext(run.run_dir), "input", arrays={"300"})
    assert a.counts["NODATA"] == 3 and a.counts["OK"] == 37


def test_lost_and_not_run_units(make_run):
    run = make_run(monitor=FAST)
    run.swot()
    _canary(run)
    run.units("unconstrained_momma", "100", 0, [(i, None) for i in range(20)])  # OOM-killed mid-unit
    # task 1 never wrote a unit file (died before its first unit)
    assert run_gate(str(run.run_dir), "unconstrained_momma", "canary", "999", quiet=True) == EXIT_ABORT
    a = assess_module(RunContext(run.run_dir), "unconstrained_momma", arrays={"100"}, indices=set(range(40)))
    assert a.counts["NOT_RUN"] == 20


def test_watcher_mode_treats_open_units_as_running(make_run):
    run = make_run(monitor=FAST)
    run.swot()
    _canary(run)
    run.units("unconstrained_momma", "100", 0, [(i, None) for i in range(20)])
    a = assess_module(
        RunContext(run.run_dir), "unconstrained_momma", arrays={"100"}, indices=set(range(40)), in_progress=True
    )
    assert a.counts["RUNNING"] == 20 and a.counts["PENDING"] == 20 and a.bad == 0


def test_gate_disabled_per_module(make_run):
    run = make_run(monitor={**FAST, "modules": {"unconstrained_momma": {"gate": False}}})
    _canary(run)
    run.units("unconstrained_momma", "100", 0, [(i, 1) for i in range(20)])
    assert run_gate(str(run.run_dir), "unconstrained_momma", "canary", "999", quiet=True) == EXIT_OK


def test_consensus_partial_and_small_failures_warn_only(make_run):
    run = make_run(monitor=FAST, modules=["consensus"])
    run.swot()
    ts = ["no_data"] * 5
    run.output("flpe/consensus/{unit}_consensus.nc", run.reach_ids[:10], var="consensus_q", time_str=ts)
    good = [f"2024-01-0{i + 1}" for i in range(5)]
    run.output("flpe/consensus/{unit}_consensus.nc", run.reach_ids[10:], var="consensus_q", time_str=good)
    run.submit("consensus", "400", "batch", 0, 39)
    run.units("consensus", "400", 0, [("0-19", 0)])
    run.units("consensus", "400", 1, [("20-39", 0)])
    assert run_gate(str(run.run_dir), "consensus", "final", "999", quiet=True) == EXIT_OK
    ctx = RunContext(run.run_dir)
    a = assess_module(ctx, "consensus", deep_all=True)
    assert a.counts["PARTIAL"] == 10 and a.counts["OK"] == 30


def test_fixed_module_missing_product_aborts(make_run):
    run = make_run(monitor=FAST, modules=["non_expanded_combine_data"])
    (run.input / "reaches.json").write_text("[]")
    run.submit("non_expanded_combine_data", "500", "fixed", 0, 0, n_tasks=1)
    run.units("non_expanded_combine_data", "500", 0, [(0, 0)])
    assert run_gate(str(run.run_dir), "non_expanded_combine_data", "final", "999", quiet=True) == EXIT_ABORT
    assert "R4_missing_product" in json.loads((run.monitor / "ABORT").read_text())["reason"]


def test_abort_is_exclusive(tmp_path):
    assert report.abort(tmp_path, "gate", "first")
    assert not report.abort(tmp_path, "monitor", "second")
    assert json.loads((tmp_path / "ABORT").read_text())["reason"] == "first"


def test_has_values_ignores_fill(tmp_path):
    from confluence.monitor import outputs as O
    from confluence.monitor.registry import Probe
    from tests.conftest import write_nc

    p = tmp_path / "x.nc"
    write_nc(p, {"Q": np.full(5, -999999999999.0)})
    assert O.deep_state(p, Probe("q", "Q")) == O.STUB
    write_nc(p, {"Q": np.array([np.nan, 1.0, np.nan, np.nan, np.nan])})
    assert O.deep_state(p, Probe("q", "Q")) == O.DATA
    p.write_bytes(b"")
    assert O.deep_state(p, Probe("q", "Q")) == O.EMPTY
    p.write_bytes(b"<html>not netcdf</html>")
    assert O.cheap_state(p) == O.BAD


def test_watcher_in_driver_job_follows_driver_pid(make_run):
    """Inside the driver job the watcher stops when the driver process is gone, without asking squeue."""
    import subprocess

    from confluence.monitor.slurm import Slurm
    from confluence.monitor.watch import Watcher

    run = make_run(monitor=FAST)
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return 0, "", ""

    proc = subprocess.Popen(["sleep", "30"])
    w = Watcher(str(run.run_dir), "999", slurm=Slurm(runner=runner, user="me"), driver_pid=proc.pid, audit=False)
    assert w.step() is True
    proc.kill()
    proc.wait()
    assert w.step() is False
    assert not any(c[:3] == ["squeue", "-h", "-j"] for c in calls)
    assert w.run() == 0 and not (run.monitor / "audit.json").exists()


def test_finish_records_a_driver_killed_without_cleanup(make_run):
    from confluence.monitor.slurm import Slurm
    from confluence.monitor.watch import finish_run

    run = make_run(modules=["unconstrained_momma"])
    run.swot()

    def runner(cmd):
        if cmd[0] == "sacct":
            return 0, "999|confluence_driver_t|TIMEOUT|0:0|01:00:00|3600|01:00:00|60|8G||n1|3600|x|y", ""
        return 0, "", ""

    assert finish_run(str(run.run_dir), "999", slurm=Slurm(runner=runner, user="me")) == 0
    assert (run.monitor / "driver.exit").read_text().strip() == "lost (Slurm state TIMEOUT)"
    assert "driver_crashed" in (run.monitor / "events.log").read_text()
    audit = json.loads((run.monitor / "audit.json").read_text())
    assert "D2_driver_stopped" in {f["code"] for f in audit["findings"]}
    assert (run.monitor / "status.json").exists() and (run.monitor / "audit.html").exists()


def test_finish_leaves_a_clean_driver_exit_alone(make_run):
    from confluence.monitor.slurm import Slurm
    from confluence.monitor.watch import finish_run

    run = make_run(modules=["unconstrained_momma"])
    run.swot()
    (run.monitor / "driver.exit").write_text("done\n")
    finish_run(str(run.run_dir), "999", slurm=Slurm(runner=lambda cmd: (0, "", ""), user="me"))
    assert (run.monitor / "driver.exit").read_text().strip() == "done"
    assert "driver_crashed" not in (run.monitor / "events.log").read_text()
