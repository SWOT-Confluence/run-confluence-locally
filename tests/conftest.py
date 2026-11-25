"""Synthetic Confluence run directories for monitor tests."""

from __future__ import annotations

import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]


def write_nc(path: Path, variables: dict | None = None, time_str: list[str] | None = None):
    """Write a small netCDF. variables: {"grp/var" or "var": array}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(path, "w") as ds:
        n = 5
        ds.createDimension("nt", n)
        for name, values in (variables or {}).items():
            grp = ds
            *groups, var = name.split("/")
            for g in groups:
                grp = grp.groups[g] if g in grp.groups else grp.createGroup(g)
            v = grp.createVariable(var, "f8", ("nt",), fill_value=-999999999999.0)
            v[:] = values
        if time_str is not None:
            v = ds.createVariable("time_str", str, ("nt",))
            for i, s in enumerate(time_str):
                v[i] = s


class SyntheticRun:
    def __init__(self, root: Path, name: str = "t", n_reaches: int = 40, modules=None, monitor=None):
        self.name = name
        self.run_dir = root / f"confluence_{name}"
        self.mnt = self.run_dir / f"{name}_mnt"
        self.input = self.mnt / "input"
        self.report = self.run_dir / "report"
        self.monitor = self.run_dir / "monitor"
        for d in (self.input / "swot", self.report, self.monitor, self.run_dir / "log", self.run_dir / "sh_scripts"):
            d.mkdir(parents=True, exist_ok=True)
        self.reach_ids = [f"7315060{i:03d}1" for i in range(n_reaches)]
        entries = [{"reach_id": int(r), "sword": "na_sword_v17.nc", "swot": f"{r}_SWOT.nc"} for r in self.reach_ids]
        for f in ("reaches.json", "expanded_reaches_of_interest.json"):
            (self.input / f).write_text(json.dumps(entries))
        (self.input / "reaches_of_interest.json").write_text(json.dumps(self.reach_ids))
        (self.input / "continent.json").write_text(json.dumps([{"na": [7, 8, 9]}]))
        modules = modules or ["input", "prediagnostics", "unconstrained_momma", "consensus", "validation", "output"]
        cfg = {
            "run_name": name,
            "root_dir": str(root),
            "sword_version": "17",
            "modules_to_run": modules,
            "max_reaches": 0,
            "hpc": {"partition": "", "account": "acct", "time": "1:00:00", "batch_size": 1000,
                    "concurrent_jobs": 10, "reach_chunks": 20},
            "module_templates": {m: {"time": "1:00:00", "mem": "4G", "j2_file": f"modules/{m}.sh.j2",
                                     "module_args": {}} for m in modules},
            "dirs": {"run": str(self.run_dir), "mnt": str(self.mnt), "input": str(self.input),
                     "report": str(self.report), "log": str(self.run_dir / "log")},
        }
        if monitor is not None:
            cfg["monitor"] = monitor
        (self.run_dir / "config.yml").write_text(yaml.safe_dump(cfg))

    # ---------------------------------------------------------------- inputs / outputs
    def swot(self, rids=None, empty=False):
        for r in rids if rids is not None else self.reach_ids:
            p = self.input / "swot" / f"{r}_SWOT.nc"
            if empty:
                p.write_bytes(b"")
            else:
                write_nc(p, {"reach/time": np.arange(5) * 86400.0 * 400 + 7.5e8})

    def output(self, module_dir: str, rids, var: str = "Q", values=None, stub=False, time_str=None):
        for r in rids:
            p = self.mnt / module_dir.format(unit=r)
            if stub:
                write_nc(p, {})
            else:
                write_nc(p, {var: values if values is not None else np.arange(5) + 10.0}, time_str=time_str)

    # ---------------------------------------------------------------- ledger
    def submit(self, module, array_id, phase, first, last, n_tasks=None, offset=None, index_range=20,
               driver="999", max_limit=None):
        n_tasks = n_tasks if n_tasks is not None else (last - first) // index_range + 1
        offset = first if offset is None else offset
        max_limit = max_limit if max_limit is not None else len(self.reach_ids)
        with open(self.monitor / "submissions.tsv", "a") as f:
            f.write("\t".join(map(str, [1700000000, driver, module, phase, array_id, offset, index_range,
                                        n_tasks, first, last, max_limit])) + "\n")

    def units(self, module, array_id, task, items, log_lines=None, node="node1"):
        """items: list of (idx, rc) - rc None means BEGIN without END. Writes TSV + a task log."""
        d = self.monitor / "units" / module
        d.mkdir(parents=True, exist_ok=True)
        rows, log = [], []
        for idx, rc in items:
            unit = self.reach_ids[idx] if isinstance(idx, int) and idx < len(self.reach_ids) else str(idx)
            rows.append(f"BEGIN\t{module}\t{array_id}\t{task}\t{idx}\t{unit}\t\t1700000000\t\t{node}")
            log.append(f"CFL_UNIT_BEGIN module={module} idx={idx} unit={unit}")
            log.extend((log_lines or {}).get(idx, []))
            if rc is not None:
                rows.append(f"END\t{module}\t{array_id}\t{task}\t{idx}\t{unit}\t{rc}\t1700000010\t10\t{node}")
                log.append(f"CFL_UNIT_END module={module} idx={idx} unit={unit} rc={rc} secs=10")
        (d / f"{array_id}_{task}.tsv").write_text("\n".join(rows) + "\n")
        (self.report / f"{module}.{array_id}_{task}.out").write_text("\n".join(log) + "\n")


@pytest.fixture
def run(tmp_path):
    return SyntheticRun(tmp_path)


@pytest.fixture
def make_run(tmp_path):
    def _make(**kw):
        return SyntheticRun(tmp_path, **kw)

    return _make
