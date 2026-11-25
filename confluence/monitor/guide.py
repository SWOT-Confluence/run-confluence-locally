"""RUN_GUIDE.md: a deterministic briefing that lets someone new to a run understand it - how it
was built, what it produced, whether it worked and where to dig deeper - without reading this repo.

Rendered at setup (static sections) and re-rendered by every audit (results sections).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jinja2 import Environment, PackageLoader

from confluence.monitor import report
from confluence.monitor.context import RunContext
from confluence.monitor.ledger import driver_logs, read_submissions
from confluence.monitor.provenance import REPO_ROOT, collect_provenance
from confluence.monitor.registry import is_fixed
from confluence.monitor.signatures import BENIGN_PATTERNS, FATAL_PATTERNS

_BIND = re.compile(r"--bind\s+(\S+?):(/\S+?)(:ro)?(?=\s|\\|$)")

HOST_DIRS = [
    ("config.yml", "Fully resolved config this run was set up with (defaults filled in)."),
    ("RUN_GUIDE.md", "This file."),
    ("sh_scripts/", "Rendered Slurm scripts: slurm_driver.sh, one <module>.sh per module, audit.sh, cfl_lib.sh."),
    (
        "log/",
        "Driver logs (<run>_<jobid>_*.out/.err), the monitor log (monitor_<driver jobid>.out) and the "
        "end-of-run audit job log (audit_<jobid>.out).",
    ),
    ("report/", "One log per array task: <module>.<array>_<task>.out (stdout+stderr of every container call)."),
    (
        "monitor/",
        "Gate/monitor/audit records: submissions.tsv, units/, events.log, status.*, audit.*, ABORT, provenance.json.",
    ),
    ("modules/", "Source of every module (git clones or copies of local_repo_dirs); modules/logs/ has clone logs."),
    ("sif/", "Built Apptainer images (<module>.sif); sif/logs/ has build logs."),
]

MNT_DIRS = [
    (
        "input/",
        "Index JSONs (reaches_of_interest, expanded_reaches_of_interest, reaches, metrosets, basin, continent) "
        "and inputs.",
    ),
    (
        "input/swot/",
        "<reach_id>_SWOT.nc: SWOT RiverSP time series downloaded by `input` (rewritten by prediagnostics).",
    ),
    ("input/sword/", "SWORD netCDFs per continent."),
    ("input/sos/", "SoS priors per continent."),
    ("input/svs/", "SVS gauge file used by validation."),
    ("diagnostics/prediagnostics/", "<reach_id>_prediagnostics.nc."),
    (
        "flpe/<algo>/",
        "<reach_id>_<algo>.nc per FLPE algorithm; flpe/metroman/sets/ holds per-set MetroMan files; "
        "flpe/consensus/ the consensus.",
    ),
    ("validation/stats/", "<reach_id>_validation.nc for gauged reaches; validation/figs/ hydrograph plots."),
    ("output/sos/", "<cont>_sword_v<V>_SOS_results_<first>_<last>_<created>.nc: the final per-continent products."),
    ("moi/, offline/", "MOI / offline products (often empty)."),
]


def _bind_map(ctx: RunContext) -> dict[str, list[tuple[str, str, bool]]]:
    out = {}
    for module in ctx.modules_to_run:
        p = ctx.sh_scripts_dir / f"{module}.sh"
        if not p.exists():
            continue
        binds = []
        for m in _BIND.finditer(p.read_text()):
            host, cont, ro = m.group(1), m.group(2), bool(m.group(3))
            if (host, cont, ro) not in binds:
                binds.append((host, cont, ro))
        out[module] = binds
    return out


def _split_binds(binds: dict) -> dict:
    """Binds every module uses (shown once) and the module-specific rest."""
    lists = [set(b) for b in binds.values() if b]
    common = set.intersection(*lists) if lists else set()
    first = next((b for b in binds.values() if b), [])
    return {
        "common_binds": [b for b in first if b in common],
        "binds": {m: [x for x in b if x not in common] for m, b in binds.items()},
    }


def _config_digest(ctx: RunContext) -> list[tuple[str, str]]:
    c = ctx.cfg
    inp = ctx.module_args("input")
    roi = ctx.index("reaches_of_interest.json")

    def src(kind):
        for k in (f"{kind}_bind_dir", f"{kind}_copy_dir", f"{kind}_zenodo_doi"):
            if c.get(k):
                return f"{k}: {c[k]}"
        return "-"

    rows = [
        ("ROI file", f"{c.get('roi_file')} ({len(roi)} reaches)"),
        ("SWORD version", str(c.get("sword_version"))),
        ("SWOT collection", str(inp.get("collection", "-"))),
        ("SWOT time_range", str(inp.get("time_range", "-"))),
        ("continue_downloads (-k)", str(inp.get("continue_downloads", "-"))),
        ("SoS priors", src("priors")),
        ("SWORD source", src("sword")),
        ("SVS", str(c.get("svs_repo_filename") or c.get("svs_copy_dir") or "-")),
        ("swot_input_bind_dir", str(c.get("swot_input_bind_dir") or "-")),
        ("flpe_output_bind_dir", str(c.get("flpe_output_bind_dir") or "-")),
        ("max_reaches", str(c.get("max_reaches", 0))),
        (
            "GitHub user / branch / image tag",
            " / ".join(
                str(c.get(k))
                for k in ("default_github_username", "default_repository_branch", "default_image_release_tag")
            ),
        ),
        ("scheduler", ctx.scheduler),
        (
            "local" if ctx.scheduler == "local" else "hpc",
            ", ".join(f"{k}={v}" for k, v in ctx.scheduler_settings.items()),
        ),
        ("monitor", json.dumps(c.get("monitor") or "defaults (monitor block absent: pre-monitor run)")),
    ]
    return rows


def _example(ctx: RunContext) -> dict | None:
    """A worked reach -> index -> array task -> log example from this run."""
    reaches = ctx.index("reaches.json")
    if not reaches:
        return None
    idx = min(len(reaches) - 1, 25)
    rid = str(reaches[idx].get("reach_id")) if isinstance(reaches[idx], dict) else str(reaches[idx])
    module = next(
        (m for m in ctx.modules_to_run if not is_fixed(ctx.spec(m)) and ctx.spec(m).index == "reaches.json"), None
    )
    if module is None:
        return None
    chunk = ctx.reach_chunks(module)
    subs = [s for s in read_submissions(ctx.monitor_dir, module=module) if idx in s.indices()]
    if subs:
        s = subs[-1]
        task = (idx - s.offset) // s.index_range
        log = ctx.report_dir / f"{module}.{s.array_id}_{task}.out"
        how = f"submissions.tsv row: array {s.array_id}, OFFSET={s.offset}, INDEX_RANGE={s.index_range}"
    else:
        task = idx // chunk
        log = ctx.report_dir / f"{module}.*_{task}.out"
        how = (
            f"no submissions.tsv (pre-monitor run): task = idx // reach_chunks = {task} when the module ran "
            "in one batch; old logs are named <module>.<task job id>_<task>.out, so glob on the task suffix"
        )
    spec = ctx.spec(module)
    out = ctx.path(spec.output, rid) if spec.output else None
    return {
        "module": module,
        "idx": idx,
        "reach_id": rid,
        "task": task,
        "log": str(log),
        "how": how,
        "output": str(out) if out else None,
        "n": len(reaches),
    }


def build_context(ctx: RunContext, audit: dict | None) -> dict:
    prov = (audit or {}).get("provenance") or collect_provenance(ctx)
    modules = []
    for i, m in enumerate(ctx.audited_modules, 1):
        spec = ctx.spec(m)
        p = prov.get("modules", {}).get(m, {})
        t = ctx.module_template(m)
        modules.append(
            {
                "n": i,
                "name": m,
                "spec": spec,
                "tmpl": t,
                "args": t.get("module_args") or {},
                "prov": p,
                "commit": (p.get("git_commit") or "")[:10],
                "fixed": is_fixed(spec),
            }
        )
    drv = driver_logs(ctx.log_dir) if ctx.log_dir.exists() else []
    abort = report.read_abort(ctx.monitor_dir)
    exit_file = ctx.monitor_dir / "driver.exit"
    return {
        "ctx": ctx,
        "run_name": ctx.run_name,
        "run_dir": str(ctx.run_dir),
        "mnt": str(ctx.mnt),
        "generated": report.now_iso(),
        "audit": audit,
        "abort": abort,
        "driver_exit": exit_file.read_text().strip() if exit_file.exists() else None,
        "driver_logs": [str(p) for p in drv],
        "prov": prov,
        "runner": prov.get("runner") or {},
        "repo_root": str(REPO_ROOT),
        "digest": _config_digest(ctx),
        "modules": modules,
        "host_dirs": HOST_DIRS,
        "mnt_dirs": MNT_DIRS,
        **_split_binds(_bind_map(ctx)),
        "example": _example(ctx),
        "benign": BENIGN_PATTERNS,
        "fatal": FATAL_PATTERNS,
        "py": f"{REPO_ROOT}/.venv/bin/python",
        "has_ledger": (ctx.monitor_dir / "submissions.tsv").exists(),
        "scheduler": ctx.scheduler,
    }


def render_guide(ctx: RunContext, audit: dict | None = None) -> str:
    env = Environment(loader=PackageLoader("confluence", "templates"), trim_blocks=True, lstrip_blocks=True)
    return env.get_template("RUN_GUIDE.md.j2").render(**build_context(ctx, audit))


def write_guide(ctx: RunContext, audit: dict | None = None, out_dir: str | Path | None = None) -> Path:
    if audit is None:
        p = ctx.out_dir / "audit.json"
        if p.exists():
            try:
                audit = json.loads(p.read_text())
            except ValueError:
                audit = None
    target = (Path(out_dir) if out_dir else ctx.run_dir) / "RUN_GUIDE.md"
    report.write_atomic(target, render_guide(ctx, audit))
    return target
