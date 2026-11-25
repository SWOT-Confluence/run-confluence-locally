"""Read-only view of a run directory: its resolved config, index files and directory layout.

The run's config.yml is loaded leniently (plain YAML, not the strict pydantic Config) so that old or
moved runs can still be audited.
"""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path

import yaml

from confluence.monitor.config import MonitorConfig
from confluence.monitor.registry import ModuleSpec, get_spec


class RunContext:
    def __init__(self, run_dir: str | Path, out_dir: str | Path | None = None):
        self.run_dir = Path(run_dir).resolve()
        cfg_path = self.run_dir / "config.yml"
        if not cfg_path.exists():
            raise FileNotFoundError(f"{cfg_path} not found: is {self.run_dir} a Confluence run directory?")
        self.config_path = cfg_path
        with open(cfg_path) as f:
            self.cfg: dict = yaml.safe_load(f) or {}
        # Where monitor files are written (defaults to <run>/monitor). Reading always uses monitor_dir.
        self.monitor_dir = self.run_dir / "monitor"
        self.out_dir = Path(out_dir).resolve() if out_dir else self.monitor_dir

    # ----------------------------------------------------------------------------- layout
    @property
    def run_name(self) -> str:
        return self.cfg.get("run_name") or self.run_dir.name.removeprefix("confluence_")

    def _dir(self, key: str, default: Path) -> Path:
        # config.yml stores absolute dirs; prefer the actual run location if the run was moved.
        stored = (self.cfg.get("dirs") or {}).get(key)
        if stored:
            p = Path(stored)
            if p.exists():
                return p
        return default

    @cached_property
    def mnt(self) -> Path:
        return self._dir("mnt", self.run_dir / f"{self.run_name}_mnt")

    @property
    def input_dir(self) -> Path:
        return self.mnt / "input"

    @property
    def swot_dir(self) -> Path:
        """Where this run's SWOT files are (a source run's dir for derived runs with swot_input_bind_dir)."""
        bind = self.cfg.get("swot_input_bind_dir")
        local = self.input_dir / "swot"
        if bind and not (local.is_dir() and any(local.iterdir())):
            return Path(bind)
        return local

    @property
    def report_dir(self) -> Path:
        return self._dir("report", self.run_dir / "report")

    @property
    def log_dir(self) -> Path:
        return self._dir("log", self.run_dir / "log")

    @property
    def sh_scripts_dir(self) -> Path:
        return self._dir("sh_scripts", self.run_dir / "sh_scripts")

    @property
    def sif_dir(self) -> Path:
        return self._dir("sif", self.run_dir / "sif")

    @property
    def modules_dir(self) -> Path:
        return self._dir("modules", self.run_dir / "modules")

    @property
    def flpe_dir(self) -> Path:
        bind = self.cfg.get("flpe_output_bind_dir")
        return Path(bind) if bind else self.mnt / "flpe"

    def path(self, pattern: str, unit: str | None = None) -> Path:
        """Resolve a registry pattern (relative to mnt) for one unit."""
        rel = pattern.format(unit=unit) if unit is not None else pattern
        local = self.mnt / rel
        swot_bind = self.cfg.get("swot_input_bind_dir")
        if rel.startswith("input/swot/") and swot_bind and not local.exists():
            # Derived runs bind the SWOT files of a source run read-only.
            return Path(swot_bind) / rel.removeprefix("input/swot/")
        if rel.startswith("flpe/") and self.cfg.get("flpe_output_bind_dir") and not local.exists():
            # FLPE outputs of a derived run live in the bound source run (except consensus, which always writes to the run's own flpe/).
            return self.flpe_dir / rel.removeprefix("flpe/")
        return local

    # ----------------------------------------------------------------------------- config
    @property
    def modules_to_run(self) -> list[str]:
        return list(self.cfg.get("modules_to_run") or [])

    @cached_property
    def audited_modules(self) -> list[str]:
        """modules_to_run plus any module that left task logs or a unit ledger (config.yml only describes
        the last setup of a run dir; partial reruns overwrite it)."""
        from confluence.monitor.registry import PIPELINE_ORDER

        seen = set(self.modules_to_run)
        found = set()
        if self.report_dir.is_dir():
            for p in self.report_dir.glob("*.out"):
                name = p.name.split(".", 1)[0]
                if name in PIPELINE_ORDER:
                    found.add(name)
        units = self.monitor_dir / "units"
        if units.is_dir():
            found |= {d.name for d in units.iterdir() if d.is_dir()}
        extra = found - seen
        if not extra:
            return self.modules_to_run
        order = {m: i for i, m in enumerate(PIPELINE_ORDER)}
        merged = self.modules_to_run + sorted(extra, key=lambda m: order.get(m, len(order)))
        return sorted(merged, key=lambda m: order.get(m, len(order)))

    def module_args(self, module: str) -> dict:
        tmpl = (self.cfg.get("module_templates") or {}).get(module) or {}
        return tmpl.get("module_args") or {}

    def module_template(self, module: str) -> dict:
        return (self.cfg.get("module_templates") or {}).get(module) or {}

    def spec(self, module: str) -> ModuleSpec:
        return get_spec(module, self.module_args(module))

    @property
    def max_reaches(self) -> int:
        return int(self.cfg.get("max_reaches") or 0)

    @cached_property
    def monitor(self) -> MonitorConfig:
        try:
            return MonitorConfig(**(self.cfg.get("monitor") or {}))
        except Exception as e:  # never let a bad monitor block break auditing
            print(f"WARNING: invalid monitor config in {self.config_path}, using defaults: {e}")
            return MonitorConfig()

    @property
    def scheduler(self) -> str:
        """'slurm' or 'local' (runs made before the local scheduler existed are slurm runs)."""
        return self.cfg.get("scheduler") or "slurm"

    @property
    def hpc(self) -> dict:
        return self.cfg.get("hpc") or {}

    @property
    def local(self) -> dict:
        return self.cfg.get("local") or {}

    @property
    def scheduler_settings(self) -> dict:
        """The config block of the scheduler this run used (`hpc` or `local`)."""
        return self.local if self.scheduler == "local" else self.hpc

    def reach_chunks(self, module: str) -> int:
        """Index entries per array task: the module's reach_chunks, else the scheduler's reach_chunks."""
        tmpl = (self.cfg.get("module_templates") or {}).get(module) or {}
        return int(tmpl.get("reach_chunks") or self.scheduler_settings.get("reach_chunks") or 20)

    # ----------------------------------------------------------------------------- indices
    @cached_property
    def _index_cache(self) -> dict:
        return {}

    def index(self, name: str | None) -> list:
        """Contents of an index json in <mnt>/input ([] if missing or unreadable)."""
        if name is None:
            return []
        if name not in self._index_cache:
            p = self.input_dir / name
            try:
                with open(p) as f:
                    data = json.load(f)
                self._index_cache[name] = data if isinstance(data, list) else []
            except (OSError, ValueError):
                self._index_cache[name] = []
        return self._index_cache[name]

    def units(self, index_name: str | None, capped: bool = True) -> list[tuple[int, str]]:
        """(idx, unit_id) for every entry of an index file, capped by max_reaches like the driver."""
        if index_name is None:
            return [(0, "global")]
        entries = self.index(index_name)
        n = len(entries)
        if capped and self.max_reaches and index_name != "continent.json":
            n = min(n, self.max_reaches)
        return [(i, unit_id(entries[i], i)) for i in range(n)]


def unit_id(entry, idx: int) -> str:
    """Same mapping as _cfl_unit_id in cfl_lib.sh."""
    if isinstance(entry, dict):
        if "basin_id" in entry:
            return f"basin_{entry['basin_id']}"
        if "reach_id" in entry:
            return str(entry["reach_id"])
        return str(next(iter(entry.keys()), f"idx_{idx}"))
    if isinstance(entry, list):
        return "-".join(str(e["reach_id"]) if isinstance(e, dict) else str(e) for e in entry)
    if entry is None:
        return f"idx_{idx}"
    return str(entry)
