"""What code built and ran the run: run-confluence-locally commit, each module's source commit,
the base image tag and the .sif files. Recorded at setup (monitor/provenance.json) and re-collected
by the audit/guide for runs made before that existed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

from confluence.monitor import report
from confluence.utils.module_names import get_image_name, get_repo_name

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(path: Path, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def git_info(path: Path) -> dict:
    if not (Path(path) / ".git").exists():
        return {}
    status = _git(path, "status", "--porcelain", "--untracked-files=no")
    return {
        "commit": _git(path, "rev-parse", "HEAD"),
        "branch": _git(path, "rev-parse", "--abbrev-ref", "HEAD"),
        "remote": _git(path, "remote", "get-url", "origin"),
        "dirty": bool(status) if status is not None else None,
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def module_provenance(cfg: dict, modules_dir: Path, sif_dir: Path, hash_sifs: bool = False) -> dict:
    out = {}
    local_dirs = cfg.get("local_repo_dirs") or {}
    branches = cfg.get("repo_branches") or {}
    tags = cfg.get("image_release_tags") or {}
    for module in cfg.get("modules_to_run") or []:
        repo = get_repo_name(module).lower()
        image = get_image_name(module)
        src = Path(modules_dir) / repo
        sif = Path(sif_dir) / f"{image}.sif"
        entry = {
            "repo_dir": str(src) if src.exists() else None,
            "configured_branch": branches.get(repo, cfg.get("default_repository_branch")),
            "github_user": cfg.get("default_github_username"),
            "local_source": str(local_dirs[repo]) if repo in local_dirs else None,
            "image_tag": tags.get(repo, cfg.get("default_image_release_tag")),
            "def_file": str(src / "Singularity.def") if (src / "Singularity.def").exists() else None,
            "sif": str(sif) if sif.exists() else None,
        }
        entry.update({f"git_{k}": v for k, v in git_info(src).items()})
        if sif.exists():
            st = sif.stat()
            entry["sif_size"] = st.st_size
            entry["sif_mtime"] = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
            if hash_sifs:
                entry["sif_sha256"] = _sha256(sif)
        out[module] = entry
    return out


def collect_provenance(ctx, hash_sifs: bool = False) -> dict:
    """Merge the provenance recorded at setup (if any) with what can be read now."""
    recorded = {}
    p = ctx.monitor_dir / "provenance.json"
    if p.exists():
        try:
            recorded = json.loads(p.read_text())
        except ValueError:
            recorded = {}
    now = module_provenance(ctx.cfg, ctx.modules_dir, ctx.sif_dir, hash_sifs=hash_sifs)
    return {
        "recorded_at_setup": bool(recorded),
        "runner": recorded.get("runner") or {"note": "not recorded (run predates provenance capture)"},
        "modules": {m: {**now.get(m, {}), **(recorded.get("modules", {}).get(m, {}))} for m in now},
    }


def write_setup_provenance(cfg) -> Path:
    """Called by write_scripts() at setup: records the runner commit and each module's source."""
    cfg_dict = cfg.model_dump(mode="json")
    data = {
        "recorded": report.now_iso(),
        "runner": {"repo": str(REPO_ROOT), **git_info(REPO_ROOT)},
        "modules": module_provenance(cfg_dict, cfg.dirs["modules"], cfg.dirs["sif"]),
    }
    out = Path(cfg.dirs["run"]) / "monitor" / "provenance.json"
    report.write_json(out, data)
    return out
