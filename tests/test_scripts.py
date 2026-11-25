"""Rendered scripts: syntax, cfl_lib bookkeeping, and the driver's canary/gate/abort flow end-to-end
against fake sbatch / squeue / scancel / apptainer commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

from confluence.utils.config import Config
from confluence.utils.scripts import write_scripts
from tests.conftest import REPO, write_nc

EXAMPLE = REPO / "configs" / "examples" / "1_end_to_end.yml"


def make_cfg(tmp_path: Path, modules=None, n_reaches=100, monitor=None, tweak=None) -> Config:
    raw = yaml.safe_load(EXAMPLE.read_text())
    roi = tmp_path / "roi.json"
    rids = [f"7315060{i:03d}1" for i in range(n_reaches)]
    roi.write_text(json.dumps(rids))
    raw.update(root_dir=str(tmp_path), run_name="t", roi_file=str(roi), submit_driver=False, no_build=True)
    raw["hpc"] = {"partition": "", "account": "acct", "time": "1:00:00", "batch_size": 2,
                  "concurrent_jobs": 4, "reach_chunks": 20}
    if modules:
        raw["modules_to_run"] = modules
    if monitor is not None:
        raw["monitor"] = monitor
    if tweak is not None:
        tweak(raw)
    cfg = Config(**raw)
    run_dir = tmp_path / "confluence_t"
    mnt = run_dir / "t_mnt"
    dirs = {k: run_dir / k for k in ("log", "modules", "monitor", "report", "sh_scripts", "sif")}
    dirs.update(run=run_dir, mnt=mnt, input=mnt / "input")
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    for sub in ("input/swot", "flpe/sad", "flpe/momma", "diagnostics/prediagnostics"):
        (mnt / sub).mkdir(parents=True, exist_ok=True)
    cfg.dirs = dirs
    entries = [{"reach_id": int(r), "sword": "na_sword_v17.nc", "swot": f"{r}_SWOT.nc"} for r in rids]
    for f in ("reaches.json", "expanded_reaches_of_interest.json"):
        (mnt / "input" / f).write_text(json.dumps(entries))
    (mnt / "input" / "reaches_of_interest.json").write_text(json.dumps(rids))
    (mnt / "input" / "continent.json").write_text(json.dumps([{"na": [7, 8, 9]}, {"af": [1]}]))
    cfg.to_file(run_dir / "config.yml")
    return cfg


def test_all_scripts_render_and_parse(tmp_path):
    cfg = make_cfg(tmp_path)
    driver = write_scripts(cfg)
    scripts = sorted(cfg.dirs["sh_scripts"].glob("*.sh"))
    assert driver in scripts and (cfg.dirs["sh_scripts"] / "audit.sh") in scripts
    for s in scripts:
        r = subprocess.run(["bash", "-n", str(s)], capture_output=True, text=True)
        assert r.returncode == 0, f"{s.name}: {r.stderr}"
    text = driver.read_text()
    assert 'run_reach "input"' in text and "expanded_reaches_of_interest.json" in text
    assert 'run_fixed "output" "output.sh" 1' in text  # continent.json rewritten to the ROI's continent
    momma = (cfg.dirs["sh_scripts"] / "unconstrained_momma.sh").read_text()
    assert 'cfl_unit "${idx}" apptainer run' in momma and "%A_%a" in momma and "cfl_finish" in momma
    assert (cfg.dirs["run"] / "RUN_GUIDE.md").exists()
    assert (cfg.dirs["monitor"] / "provenance.json").exists()


def test_monitor_disabled_renders_plain_driver(tmp_path):
    cfg = make_cfg(tmp_path, monitor={"enabled": False})
    text = write_scripts(cfg).read_text()
    assert "GATE_ENABLED=0" in text and "MONITOR_ENABLED=0" in text


def test_cfl_lib_records_units_and_exit_codes(tmp_path):
    lib = REPO / "confluence" / "scripts" / "cfl_lib.sh"
    idx_json = tmp_path / "reaches.json"
    idx_json.write_text(json.dumps([{"reach_id": 11}, {"reach_id": 22}, {"reach_id": 33}]))
    script = f"""
CFL_MODULE=sad; CFL_MONITOR_DIR={tmp_path}/monitor; CFL_INDEX_JSON={idx_json}
source {lib}
cfl_unit 0 true
cfl_unit 1 false
cfl_unit 2 bash -c 'exit 7'
cfl_finish
"""
    env = {**os.environ, "SLURM_ARRAY_JOB_ID": "55", "SLURM_ARRAY_TASK_ID": "3"}
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert r.returncode == 3
    assert "CFL_UNIT_END module=sad idx=1 unit=22 rc=1" in r.stdout
    rows = [ln.split("\t") for ln in (tmp_path / "monitor/units/sad/55_3.tsv").read_text().splitlines()]
    ends = [(row[4], row[5], row[6]) for row in rows if row[0] == "END"]
    assert ends == [("0", "11", "0"), ("1", "22", "1"), ("2", "33", "7")]

    all_fail = script.replace("cfl_unit 0 true", "cfl_unit 0 false").replace("cfl_unit 2 bash -c 'exit 7'", "")
    r = subprocess.run(["bash", "-c", all_fail], capture_output=True, text=True, env=env)
    assert r.returncode == 4


def test_module_cpus_reach_chunks_and_prediagnostics_config(tmp_path):
    """A module's own cpus / reach_chunks override the defaults; prediagnostics can bind its own config.R."""

    def tweak(raw):
        raw["modules_to_run"] = ["prediagnostics", "unconstrained_momma"]
        raw["module_templates"]["unconstrained_momma"].update(cpus=2, reach_chunks=5)
        raw["module_templates"]["prediagnostics"]["module_args"] = {"config_file": "/x/config.R"}

    cfg = make_cfg(tmp_path, tweak=tweak)
    driver = write_scripts(cfg).read_text()
    reaches = cfg.dirs["input"] / "reaches.json"
    assert f'run_reach "unconstrained_momma" "unconstrained_momma.sh" "{reaches}" 5 0' in driver
    assert f'run_reach "prediagnostics" "prediagnostics.sh" "{reaches}" 20 0' in driver
    momma = (cfg.dirs["sh_scripts"] / "unconstrained_momma.sh").read_text()
    assert "#SBATCH --cpus-per-task=2" in momma
    prediag = (cfg.dirs["sh_scripts"] / "prediagnostics.sh").read_text()
    assert "#SBATCH --cpus-per-task=1" in prediag
    assert "--bind /x/config.R:/app/prediagnostics/config.R:ro" in prediag
    assert "-r reaches.json" in prediag


# --------------------------------------------------------------------------- fake slurm e2e
FAKE_SBATCH = r"""#!/bin/bash
# Fake sbatch: runs every array task synchronously, writing logs where #SBATCH --output says.
state="$FAKE_STATE"; mkdir -p "$state"
id=$(( $(cat "$state/next_id" 2>/dev/null || echo 1000) + 1 )); echo $id > "$state/next_id"
exports=""; array=""; script=""
for a in "$@"; do
  case "$a" in
    --export=*) exports="${a#--export=}";;
    --array=*) array="${a#--array=}";;
    --parsable) ;;
    *) script="$a";;
  esac
done
echo "$(basename "$script") $array $exports" >> "$state/sbatch.log"
if [[ "$(basename "$script")" == "audit.sh" ]]; then
  [[ -n "$FAKE_FAIL_AUDIT" ]] && { echo "sbatch: error: fake failure" >&2; exit 1; }
  echo $id; exit 0
fi
spec="${array%%%*}"; first="${spec%-*}"; last="${spec#*-}"
out_pat=$(grep -m1 '^#SBATCH --output=' "$script" | cut -d= -f2-)
for (( t=first; t<=last; t++ )); do
  log="${out_pat//%A/$id}"; log="${log//%a/$t}"; log="${log//%j/$id}"
  ( IFS=','; for kv in $exports; do export "$kv"; done
    SLURM_JOB_ID=$id SLURM_ARRAY_JOB_ID=$id SLURM_ARRAY_TASK_ID=$t bash "$script" > "$log" 2>&1 )
done
# A user changing concurrent_jobs while the first array of $FAKE_THROTTLE_AT is in flight.
if [[ -n "$FAKE_THROTTLE_AT" && "$(basename "$script")" == "$FAKE_THROTTLE_AT" && ! -e "$state/throttled" ]]; then
  touch "$state/throttled"; echo $id >> "$state/running_once"
  "$FAKE_PY" -m confluence.monitor throttle "$FAKE_RUN_DIR" "$FAKE_THROTTLE_TO" >> "$state/throttle.out" 2>&1
fi
echo $id
"""

# Fake squeue: every array has left the queue, except that the driver's first poll of an array listed in
# running_once reports it as running.
FAKE_SQUEUE = r"""#!/bin/bash
id=""; fmt=""; prev=""
for a in "$@"; do [[ "$prev" == "-j" ]] && id="$a"; [[ "$prev" == "-o" ]] && fmt="$a"; prev="$a"; done
f="$FAKE_STATE/running_once"
if [[ "$fmt" == "%i %T %R" && -e "$f" ]] && grep -qx "$id" "$f"; then
  sed -i "/^${id}\$/d" "$f"; echo "${id}_1 RUNNING node1"
fi
exit 0
"""

FAKE_APPTAINER = r"""#!/bin/bash
# Fake apptainer: 'run ... <x>.sif args'. Fails like a missing image for $FAKE_FAIL_SIF,
# otherwise writes <rid>_<algo>.nc into the host dir bound to /mnt/data/output.
if [[ "$1" == "--version" ]]; then echo "apptainer fake"; exit 0; fi
out=""; sif=""; idx=""; prev=""
for a in "$@"; do
  if [[ "$prev" == "--bind" && "$a" == *":/mnt/data/output"* ]]; then out="${a%%:*}"; fi
  [[ "$a" == *.sif ]] && sif="$(basename "$a" .sif)"
  [[ "$prev" == "-i" || "$prev" == "--index" ]] && idx="$a"
  prev="$a"
done
if [[ -n "$FAKE_FAIL_SIF" && "$sif" == "$FAKE_FAIL_SIF" ]]; then
  echo "FATAL:   could not open image $sif.sif: failed to retrieve path: no such file or directory"; exit 255
fi
if [[ -n "$out" && "$idx" =~ ^[0-9]+$ ]]; then
  rid=$(jq -r ".[$idx].reach_id" "$FAKE_REACHES")
  cp "$FAKE_NC" "$out/${rid}_${sif}.nc"
fi
echo "ran $sif $idx"
"""


@pytest.fixture
def fake_slurm(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "sbatch": FAKE_SBATCH,
        "squeue": FAKE_SQUEUE,
        "scancel": '#!/bin/bash\necho "$@" >> "$FAKE_STATE/scancel.log"\n',
        "scontrol": '#!/bin/bash\necho "$@" >> "$FAKE_STATE/scontrol.log"\n',
        "apptainer": FAKE_APPTAINER,
    }.items():
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)
    nc = tmp_path / "q.nc"
    write_nc(nc, {"Q": np.arange(5) + 1.0, "Qa": np.arange(5) + 1.0})
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("FAKE_NC", str(nc))
    return tmp_path


def _run_driver(cfg: Config, extra_env=None):
    env = {**os.environ, "SLURM_JOB_ID": "777", **(extra_env or {})}
    env["FAKE_REACHES"] = str(cfg.dirs["input"] / "reaches.json")
    r = subprocess.run(["bash", str(cfg.dirs["sh_scripts"] / "slurm_driver.sh")], capture_output=True, text=True,
                       env=env, timeout=600)
    return r


E2E_MODULES = ["prediagnostics", "sad", "unconstrained_momma"]
E2E_MONITOR = {
    "canary_tasks": 1,
    "gate_wait_seconds": 0,
    "watcher": True,
    "modules": {"prediagnostics": {"abort_coverage": 0.0}},
}


def _swot_all(cfg):
    for e in json.loads((cfg.dirs["input"] / "reaches.json").read_text()):
        write_nc(cfg.dirs["input"] / "swot" / f"{e['reach_id']}_SWOT.nc", {"reach/time": np.arange(5) * 1e6})


def test_driver_healthy_run_completes(fake_slurm):
    cfg = make_cfg(fake_slurm, modules=E2E_MODULES, monitor=E2E_MONITOR)
    _swot_all(cfg)
    write_scripts(cfg)
    r = _run_driver(cfg)
    assert r.returncode == 0, r.stdout + r.stderr
    mon = cfg.dirs["monitor"]
    assert (mon / "driver.exit").read_text().strip() == "done"
    assert not (mon / "ABORT").exists()
    subs = [ln.split("\t") for ln in (mon / "submissions.tsv").read_text().splitlines()]
    phases = [(s[2], s[3], s[8], s[9]) for s in subs]
    # 100 reaches / 20 per task = 5 tasks: 1-task canary, then batches of 2 tasks (batch_size)
    assert phases[:4] == [("prediagnostics", "canary", "0", "19"), ("prediagnostics", "batch", "20", "59"),
                          ("prediagnostics", "batch", "60", "99"), ("sad", "canary", "0", "19")]
    assert len(list((cfg.dirs["mnt"] / "flpe" / "sad").glob("*_sad.nc"))) == 100
    sbatch_log = (fake_slurm / "state" / "sbatch.log").read_text()
    assert "audit.sh" in sbatch_log  # queued, not run: the fake sbatch only records it
    assert not (mon / "audit.json").exists()
    # the background monitor ran inside the driver and was stopped when the driver exited
    assert "watching driver 777" in (mon / "events.log").read_text()
    pid = int(r.stdout.split("Started monitor (pid ")[1].split(")")[0])
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_driver_audits_itself_when_audit_job_cannot_be_queued(fake_slurm):
    cfg = make_cfg(fake_slurm, modules=E2E_MODULES, monitor=E2E_MONITOR)
    _swot_all(cfg)
    write_scripts(cfg)
    r = _run_driver(cfg, {"FAKE_FAIL_AUDIT": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "could not queue the audit job" in r.stdout
    assert (cfg.dirs["monitor"] / "audit.html").exists()


def test_driver_aborts_after_canary_on_broken_module(fake_slurm):
    cfg = make_cfg(fake_slurm, modules=E2E_MODULES, monitor=E2E_MONITOR)
    _swot_all(cfg)
    write_scripts(cfg)
    r = _run_driver(cfg, {"FAKE_FAIL_SIF": "sad"})
    assert r.returncode == 3, r.stdout + r.stderr
    mon = cfg.dirs["monitor"]
    ab = json.loads((mon / "ABORT").read_text())
    assert ab["source"] == "gate" and "sad (canary)" in ab["reason"]
    assert "R3_fatal_signature" in ab["reason"]
    subs = [ln.split("\t") for ln in (mon / "submissions.tsv").read_text().splitlines()]
    sad = [s for s in subs if s[2] == "sad"]
    assert [s[3] for s in sad] == ["canary"], "only the canary array may run for the broken module"
    assert not any(s[2] == "unconstrained_momma" for s in subs)
    assert (mon / "driver.exit").read_text().strip() == "aborted"
    # the sad task log has the per-task exit summary and markers
    log = next(cfg.dirs["report"].glob("sad.*_0.out")).read_text()
    assert "CFL_TASK_SUMMARY module=sad units=20 failed=20" in log


def test_driver_honours_external_abort_and_clears_stale_one(fake_slurm):
    cfg = make_cfg(fake_slurm, modules=E2E_MODULES, monitor=E2E_MONITOR)
    _swot_all(cfg)
    write_scripts(cfg)
    (cfg.dirs["monitor"] / "ABORT").write_text('{"reason": "old run"}')
    r = _run_driver(cfg)
    assert r.returncode == 0, r.stdout
    assert list(cfg.dirs["monitor"].glob("ABORT.*.prev"))


def test_driver_follows_throttle_changes(fake_slurm):
    cfg = make_cfg(fake_slurm, modules=E2E_MODULES, monitor=E2E_MONITOR)
    _swot_all(cfg)
    write_scripts(cfg)
    mon = cfg.dirs["monitor"]
    (mon / "concurrent_jobs").write_text("99\n")  # left by an earlier driver: must not carry over
    r = _run_driver(cfg, {"FAKE_THROTTLE_AT": "sad.sh", "FAKE_THROTTLE_TO": "7", "FAKE_PY": sys.executable,
                          "FAKE_RUN_DIR": str(cfg.dirs["run"])})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Ignoring concurrent_jobs=99" in r.stdout

    state = fake_slurm / "state"
    arrays = [ln.split()[:2] for ln in (state / "sbatch.log").read_text().splitlines()
              if not ln.startswith("audit.sh")]
    sad_first = next(i for i, (script, _) in enumerate(arrays) if script == "sad.sh")
    assert all(a.endswith("%4") for _, a in arrays[: sad_first + 1])  # config value, up to the change
    assert all(a.endswith("%7") for _, a in arrays[sad_first + 1:])  # every later array
    sad_id = next(ln.split("\t")[4] for ln in (mon / "submissions.tsv").read_text().splitlines()
                  if ln.split("\t")[2] == "sad")
    assert (state / "scontrol.log").read_text().split() == ["update", f"JobId={sad_id}", "ArrayTaskThrottle=7"]
    assert f"Array {sad_id} now runs at most 7 tasks at once" in r.stdout
    assert "concurrent_jobs 4 -> 7" in (mon / "events.log").read_text()
    assert "concurrent_jobs set to 7 (was 4; config: 4)" in (state / "throttle.out").read_text()


def test_throttle_cli(tmp_path, capsys):
    from confluence.monitor.__main__ import main

    cfg = make_cfg(tmp_path)
    run, mon = str(cfg.dirs["run"]), cfg.dirs["monitor"]
    assert main(["throttle", run]) == 0
    assert "concurrent_jobs: 4 (hpc.concurrent_jobs" in capsys.readouterr().out
    assert main(["throttle", run, "10"]) == 1  # no driver has started: the next one would ignore it
    assert not (mon / "concurrent_jobs").exists()

    (mon / "driver.heartbeat").touch()
    assert main(["throttle", run, "0"]) == 2
    assert main(["throttle", run, "10"]) == 0
    assert "at most batch_size = 2 tasks" in capsys.readouterr().out
    assert (mon / "concurrent_jobs").read_text() == "10\n"
    assert main(["throttle", run, "3"]) == 0
    assert "does not stop tasks that are already running" in capsys.readouterr().out
    assert main(["throttle", run]) == 0
    assert "concurrent_jobs: 3 (set with throttle" in capsys.readouterr().out
    assert main(["throttle", run, "--reset"]) == 0
    assert not (mon / "concurrent_jobs").exists()

    (mon / "driver.exit").write_text("done\n")
    assert main(["throttle", run, "10"]) == 1


def test_driver_aborts_on_empty_index(fake_slurm):
    cfg = make_cfg(fake_slurm, modules=E2E_MODULES, monitor=E2E_MONITOR)
    write_scripts(cfg)
    (cfg.dirs["input"] / "reaches.json").write_text("")
    r = _run_driver(cfg)
    assert r.returncode == 3
    assert "missing or empty" in json.loads((cfg.dirs["monitor"] / "ABORT").read_text())["reason"]


def test_canary_is_off_by_default(tmp_path):
    cfg = make_cfg(tmp_path, modules=E2E_MODULES)
    driver = write_scripts(cfg).read_text()
    reach_calls = [ln for ln in driver.splitlines() if ln.startswith("run_reach ")]
    assert reach_calls and all(ln.endswith(" 0") for ln in reach_calls)

    (tmp_path / "on").mkdir()
    cfg = make_cfg(tmp_path / "on", modules=E2E_MODULES, monitor={"canary_tasks": 2})
    driver = write_scripts(cfg).read_text()
    assert any(ln.startswith("run_reach ") and ln.endswith(" 2") for ln in driver.splitlines())
