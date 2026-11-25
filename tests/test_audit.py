import json
import re
from pathlib import Path

from confluence.monitor.audit import run_audit
from confluence.monitor.locate import locate
from confluence.monitor.slurm import Slurm


def fake_sacct(rows):
    def runner(cmd):
        if cmd[0] == "sacct":
            return 0, "\n".join(rows), ""
        return 0, "", ""

    return Slurm(runner=runner, user="me")


def _populate(run):
    run.swot(run.reach_ids[2:])
    run.swot(run.reach_ids[2:3], empty=True)  # one 0-byte download
    run.submit("input", "10", "batch", 0, 39)
    msgs = {i: [f"root INFO No data returned for reach_id = {run.reach_ids[i]}, moving on."] for i in range(2)}
    for t in (0, 1):
        run.units("input", "10", t, [(i, 0) for i in range(t * 20, t * 20 + 20)], msgs)
    run.output("flpe/momma/{unit}_momma.nc", run.reach_ids[3:30])
    run.submit("unconstrained_momma", "11", "batch", 0, 39)
    for t in (0, 1):
        items = [(i, 1 if i < 3 else 0) for i in range(t * 20, t * 20 + 20)]
        run.units("unconstrained_momma", "11", t, items)


SACCT = [
    "10_0|input_t_cfl|COMPLETED|0:0|00:10:00|600|01:00:00|60|4G||n1|600|x|y",
    "10_0.batch|batch|COMPLETED|0:0|00:10:00|600|||||n1|600|x|y|",
    "11_0|unconstrained_momma_t_cfl|OUT_OF_MEMORY|0:125|00:59:00|3540|01:00:00|60|4G||n2|3540|x|y",
    "11_0.batch|batch|OUT_OF_MEMORY|0:125|00:59:00|3540||||3900M|n2|3540|x|y",
]


def test_audit_synthetic_run(make_run):
    run = make_run(modules=["input", "unconstrained_momma"])
    _populate(run)
    verdict, res = run_audit(str(run.run_dir), sample=50, slurm=fake_sacct(SACCT))
    codes = {f["code"] for f in res["findings"]}
    assert "A2_zero_byte_downloads" in codes
    assert {"J1_bad_job_states", "J2_memory_headroom", "J3_time_headroom"} <= codes
    assert "D2_driver_stopped" not in codes  # no driver.exit at all: pre-monitor style, not flagged
    momma = res["modules"]["unconstrained_momma"]
    # 2 reaches without SWOT + the 0-byte one are EXPLAINED; 27 outputs; the rest rc 0 -> NODATA
    assert momma["classes"]["EXPLAINED"] == 3
    assert momma["classes"]["OK"] == 27
    inp = res["modules"]["input"]
    assert inp["classes"]["NODATA"] == 2 and inp["classes"]["CORRUPT"] == 1
    assert verdict == "FAIL"
    assert (run.monitor / "audit.md").exists() and (run.monitor / "audit.json").exists()
    html = (run.monitor / "audit.html").read_text()
    assert html.startswith("<!doctype html>") and 'class="verdict FAIL"' in html and "<table>" in html

    guide = (run.run_dir / "RUN_GUIDE.md").read_text()
    assert f"# RUN GUIDE: {run.name}" in guide and "**Audit verdict:** **FAIL**" in guide
    # every absolute path the guide cites under the run dir exists (templates with <...> excluded)
    for p in re.findall(r"`(/[^`<>*]+)`", guide):
        if p.startswith(str(run.run_dir)):
            assert Path(p).exists(), p


def test_audit_out_dir_leaves_run_untouched(make_run, tmp_path):
    run = make_run(modules=["input"])
    _populate(run)
    out = tmp_path / "elsewhere"
    run_audit(str(run.run_dir), out_dir=str(out), use_sacct=False)
    assert (out / "audit.json").exists() and (out / "RUN_GUIDE.md").exists()
    assert not (run.monitor / "audit.json").exists() and not (run.run_dir / "RUN_GUIDE.md").exists()


def test_baseline_diff(make_run, tmp_path):
    a = make_run(name="a", modules=["unconstrained_momma"])
    _populate(a)
    from tests.conftest import SyntheticRun

    b = SyntheticRun(tmp_path / "b", name="b", modules=["unconstrained_momma"])
    b.swot()
    b.output("flpe/momma/{unit}_momma.nc", b.reach_ids)
    _, res = run_audit(str(a.run_dir), baseline=str(b.run_dir), use_sacct=False)
    d = res["baseline"]["modules"]["unconstrained_momma"]
    assert d["outputs_present"] == [27, 40]
    assert d["lost_vs_baseline"] == 13


def test_pre_monitor_run_falls_back_to_logs(make_run):
    """No ledger: classes come from outputs + upstream, no-data from log lines with reach ids."""
    run = make_run(modules=["consensus"])
    run.swot()
    run.output("flpe/consensus/{unit}_consensus.nc", run.reach_ids[:30], var="consensus_q")
    log = [f"reach {r}" for r in run.reach_ids] + [f"No data for reach '{r}'" for r in run.reach_ids[30:35]]
    (run.report / "consensus.123_0.out").write_text("\n".join(log))
    _, res = run_audit(str(run.run_dir), use_sacct=False)
    c = res["modules"]["consensus"]["classes"]
    assert c == {"OK": 30, "NODATA": 5, "MISSING": 5}
    assert res["modules"]["consensus"]["ledger"] is False


def test_locate(make_run):
    run = make_run(modules=["input", "unconstrained_momma", "output"])
    _populate(run)
    rows = {r["module"]: r for r in locate(str(run.run_dir), run.reach_ids[1])}
    assert rows["input"]["cls"] == "NODATA"
    assert rows["unconstrained_momma"]["cls"] == "EXPLAINED"
    assert rows["unconstrained_momma"]["log"].endswith("unconstrained_momma.11_0.out")
    assert rows["output"]["unit"] == "na"
    assert json.dumps(rows)  # serialisable for --json


def test_derived_run_reads_bound_swot(make_run, tmp_path):
    """swot_input_bind_dir: inputs live in the source run, so reaches must not look EXPLAINED."""
    import yaml

    from confluence.monitor.assess import assess_module
    from confluence.monitor.context import RunContext
    from tests.conftest import SyntheticRun

    src = SyntheticRun(tmp_path / "src", name="src")
    src.swot()
    run = make_run(modules=["unconstrained_momma"])
    cfg = yaml.safe_load((run.run_dir / "config.yml").read_text())
    cfg["swot_input_bind_dir"] = str(src.input / "swot")
    (run.run_dir / "config.yml").write_text(yaml.safe_dump(cfg))
    run.output("flpe/momma/{unit}_momma.nc", run.reach_ids[:20])
    ctx = RunContext(run.run_dir)
    assert ctx.swot_dir == src.input / "swot"
    a = assess_module(ctx, "unconstrained_momma")
    assert a.counts.get("EXPLAINED", 0) == 0 and a.coverage == 0.5


def test_missing_outputs_warning_only_for_per_unit_modules(make_run):
    run = make_run(modules=["non_expanded_combine_data", "sic4dvar"])
    run.swot()
    run.output("flpe/sic4dvar/{unit}_sic4dvar.nc", run.reach_ids[:20], var="Q_da")
    _, res = run_audit(str(run.run_dir), use_sacct=False)
    w6 = {f["module"]: f["severity"] for f in res["findings"] if f["code"] == "W6_missing_outputs"}
    assert w6 == {"sic4dvar": "warn"}


def test_modules_never_reached_are_listed_not_scored(make_run):
    """Run stopped before consensus: no submission, no unit records -> 'not run', no findings for it."""
    run = make_run(modules=["input", "unconstrained_momma", "consensus"])
    _populate(run)
    _, res = run_audit(str(run.run_dir), use_sacct=False)
    assert res["not_run"] == ["consensus"] and "consensus" not in res["modules"]
    assert not [f for f in res["findings"] if f["module"] == "consensus"]
    md = (run.monitor / "audit.md").read_text()
    assert "Not run (the run stopped before reaching them): consensus." in md
