"""Cheap (stat + magic bytes) and deep (open with netCDF4) checks of module output files."""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import numpy as np

from confluence.monitor.registry import Probe

# Output states, from worst to best.
MISSING = "missing"
EMPTY = "empty"  # 0 bytes
BAD = "bad"  # wrong magic / unopenable
STUB = "stub"  # opens but holds no data (no variables, or the probe variable is all fill)
PARTIAL = "partial"  # consensus: Q present but time_str all no_data
PRESENT = "present"  # passed the cheap check, not opened
DATA = "data"  # opened and holds data

GOOD_STATES = {PRESENT, DATA, PARTIAL}

_NC_MAGIC = (b"CDF\x01", b"CDF\x02", b"CDF\x05", b"\x89HDF")
_FILL_ABS = 1e11  # Confluence fill values are -999999999999 and similar


def resolve(path: Path) -> Path | None:
    """Return the file for a path that may contain glob characters (output SOS names)."""
    s = str(path)
    if any(c in s for c in "*?["):
        matches = sorted(glob.glob(s))
        return Path(matches[-1]) if matches else None
    if path.exists():
        return path
    # NFS can cache a negative lookup on this node for a while after a compute node creates (or renames) the
    # file; listing the parent directory revalidates it (see assess._exists_nfs): output files written
    # seconds before a gate have been reported missing.
    try:
        return path if path.name in os.listdir(path.parent) else None
    except OSError:
        return None


def cheap_state(path: Path | None, probe: Probe | None = None) -> str:
    if path is None:
        return MISSING
    try:
        size = path.stat().st_size
    except OSError:
        return MISSING
    if size == 0:
        return EMPTY
    if probe is not None and probe.kind == "json":
        return PRESENT
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return BAD
    if path.suffix == ".nc" and not head.startswith(_NC_MAGIC):
        return BAD
    return PRESENT


def _get_var(ds, var_path: str):
    grp = ds
    *groups, name = var_path.split("/")
    for g in groups:
        grp = grp.groups[g]
    return grp.variables[name]


def _has_values(arr) -> bool:
    a = np.ma.filled(np.ma.asarray(arr, dtype=float), np.nan)
    good = a[np.isfinite(a) & (np.abs(a) < _FILL_ABS) & (a > -999)]
    return bool(good.size)


def _time_str_all_missing(ds) -> bool:
    if "time_str" not in ds.variables:
        return False
    v = ds.variables["time_str"]
    try:
        vals = v[:]
    except Exception:
        return False
    if getattr(vals, "dtype", None) is not None and vals.dtype.kind in "SU" and vals.ndim == 2:
        import netCDF4

        vals = netCDF4.chartostring(vals)
    strs = [str(x).strip() for x in np.ravel(np.ma.filled(vals, ""))]
    return all(s in ("", "no_data", "--", "None", "nan") for s in strs)


def deep_state(path: Path | None, probe: Probe | None) -> str:
    """Open the file and decide DATA / STUB / PARTIAL / BAD. Falls back to the cheap state."""
    state = cheap_state(path, probe)
    if state != PRESENT or probe is None:
        return state
    if probe.kind == "json":
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return BAD
        return DATA if data else STUB
    try:
        import netCDF4

        with netCDF4.Dataset(path) as ds:
            n_vars = len(ds.variables) + sum(len(g.variables) for g in ds.groups.values())
            if n_vars == 0:
                return STUB
            if probe.kind == "opens":
                return DATA
            try:
                var = _get_var(ds, probe.var)
            except (KeyError, IndexError):
                return STUB
            if not _has_values(var[:]):
                return STUB
            if probe.kind == "consensus" and _time_str_all_missing(ds):
                return PARTIAL
            return DATA
    except Exception:
        return BAD


def time_span(path: Path, var: str = "reach/time") -> tuple[float, float] | None:
    """(min, max) of a SWOT time variable in seconds since 2000-01-01, or None."""
    try:
        import netCDF4

        with netCDF4.Dataset(path) as ds:
            t = np.ma.filled(np.ma.asarray(_get_var(ds, var)[:], dtype=float), np.nan)
    except Exception:
        return None
    t = t[np.isfinite(t) & (t > 0) & (t < 2e9)]
    return (float(t.min()), float(t.max())) if t.size else None


def sample_evenly(items: list, n: int) -> list:
    """Deterministic, evenly spread sample (keeps results reproducible between calls)."""
    if n <= 0:
        return []
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]
