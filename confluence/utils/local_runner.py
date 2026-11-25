"""Run the generated module scripts on a workstation instead of through slurm.

The per-module scripts are already scheduler-agnostic: the `#SBATCH` lines are
bash comments, and the reach fan-out is driven entirely by the OFFSET,
INDEX_RANGE, MAX_LIMIT and SLURM_ARRAY_TASK_ID environment variables. This
runner supplies those directly and bounds concurrency with a thread pool,
preserving the sequential module barrier that the slurm driver gets by waiting
on each array before submitting the next one.

Job counts are resolved immediately before each module runs, not up front,
because setfinder creates reaches.json / basin.json / metrosets.json partway
through the pipeline. The slurm driver gets this right by evaluating inside its
loop; we have to do the same.
"""

import json
import math
import os
import signal
import subprocess as sp
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from confluence.utils.config import Config
from confluence.utils.scripts import CONTINENT_MODULES, GLOBAL_MODULES

# Modules that count their tasks from a file other than reaches.json. Mirrors
# BRANCH 2 of templates/slurm_driver.sh.j2.
REACH_FILE_OVERRIDES = {
    "input": "expanded_reaches_of_interest.json",
    "moi": "basin.json",
    "metroman": "metrosets.json",
}


def _json_length(path: Path) -> int | None:
    """Length of a json array, or None if the file is absent or empty."""
    if not path.is_file() or path.stat().st_size == 0:
        return None
    with open(path) as f:
        return len(json.load(f))


def _n_continents(cfg: Config) -> int:
    # continent.json has already been filtered down to the active continents by
    # _overwrite_continent_file before any of this runs.
    return _json_length(cfg.dirs["input"] / "continent.json") or 1


def _count_tasks(cfg: Config, module: str) -> int | None:
    """How many reaches/basins/continents this module covers."""
    input_dir = cfg.dirs["input"]

    if module in GLOBAL_MODULES:
        return 1
    if module in CONTINENT_MODULES:
        return _n_continents(cfg)

    override = REACH_FILE_OVERRIDES.get(module)
    if override:
        count = _json_length(input_dir / override)
        if count is not None:
            return count

    count = _json_length(input_dir / "reaches.json")
    if count is not None:
        return count

    return _json_length(input_dir / "reaches_of_interest.json")


def _chunk_size(cfg: Config, module: str) -> int:
    # Continent and global modules index one unit per task; only the
    # reach-ranged modules honour reach_chunks.
    if module in GLOBAL_MODULES or module in CONTINENT_MODULES:
        return 1
    return cfg.local.reach_chunks


def _build_tasks(cfg: Config, module: str, n_units: int) -> list[dict]:
    """One dict of env overrides per task, equivalent to a slurm array index."""
    chunk = _chunk_size(cfg, module)
    n_tasks = math.ceil(n_units / chunk)

    return [
        {
            "SLURM_ARRAY_TASK_ID": str(i),
            "SLURM_ARRAY_JOB_ID": "local",
            "SLURM_JOB_ID": f"local{os.getpid()}",
            "OFFSET": "0",
            "INDEX_RANGE": str(chunk),
            "MAX_LIMIT": str(n_units),
        }
        for i in range(n_tasks)
    ]


def _run_task(script_path: Path, task_env: dict, report_dir: Path, module: str) -> tuple[int, Path]:
    env = os.environ.copy()
    env.update(task_env)

    log_path = report_dir / f"{module}.local_{task_env['SLURM_ARRAY_TASK_ID']}.out"

    with open(log_path, "w") as log_file:
        # start_new_session so a ctrl-c in the parent can take out the whole
        # container process tree rather than orphaning apptainer children.
        proc = sp.run(
            ["bash", str(script_path)],
            stdout=log_file,
            stderr=sp.STDOUT,
            env=env,
            start_new_session=True,
            check=False,
        )

    return proc.returncode, log_path


def _module_concurrency(cfg: Config, module: str) -> int:
    default = cfg.local.concurrent_jobs or (os.cpu_count() or 1)
    return cfg.local.module_concurrency.get(module, default)


def _run_module(cfg: Config, module: str) -> int:
    script_path = cfg.dirs["sh_scripts"] / f"{module}.sh"
    report_dir = cfg.dirs["report"]

    n_units = _count_tasks(cfg, module)
    if not n_units:
        print(f"  no job count found for {module}, skipping.")
        return 0

    if cfg.max_reaches and module not in GLOBAL_MODULES and module not in CONTINENT_MODULES:
        if n_units > cfg.max_reaches:
            print(f"  limiting {module} from {n_units} to {cfg.max_reaches} units (test mode)")
            n_units = cfg.max_reaches

    tasks = _build_tasks(cfg, module, n_units)
    workers = min(_module_concurrency(cfg, module), len(tasks))

    print(f"\n{module}: {len(tasks)} tasks over {n_units} units, {workers} at a time")
    started = time.monotonic()

    failures = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_run_task, script_path, t, report_dir, module): t for t in tasks}

        for future in as_completed(futures):
            task = futures[future]
            idx = task["SLURM_ARRAY_TASK_ID"]
            try:
                returncode, log_path = future.result()
            except Exception as e:
                failures += 1
                print(f"  [{module} {idx}] runner error: {e}")
                continue

            if returncode != 0:
                failures += 1
                print(f"  [{module} {idx}] exit {returncode}. log: {log_path}")

    elapsed = time.monotonic() - started
    # Confluence tolerates individual task failures by design, so we report and
    # carry on rather than aborting the pipeline.
    print(f"{module}: finished in {elapsed:.0f}s with {failures}/{len(tasks)} failed tasks")

    return failures


def run_local(cfg: Config):
    interrupted = False

    def _on_sigint(signum, frame):
        nonlocal interrupted
        interrupted = True
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGINT, _on_sigint)

    print(f"\n\nRunning {len(cfg.modules_to_run)} modules locally.")
    print(f"Per-task logs in: {cfg.dirs['report']}")

    totals = {}
    try:
        for module in cfg.modules_to_run:
            totals[module] = _run_module(cfg, module)
    except KeyboardInterrupt:
        print("\nInterrupted. Waiting on in-flight tasks to exit.")
    finally:
        signal.signal(signal.SIGINT, previous)

    print("\n" + "=" * 60)
    for module, failures in totals.items():
        status = "ok" if failures == 0 else f"{failures} failed tasks"
        print(f"  {module:<30} {status}")
    print("=" * 60)

    if interrupted:
        print(f"Run {cfg.run_name} was interrupted before finishing.")
    else:
        print(f"Run {cfg.run_name} has finished.")
