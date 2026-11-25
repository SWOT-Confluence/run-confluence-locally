"""Readers for the machine-readable run records.

- ``monitor/submissions.tsv``: one row per array the driver submitted.
- ``monitor/units/<module>/<array>_<task>.tsv``: BEGIN/END rows written by cfl_unit (cfl_lib.sh).
- ``report/<module>.<array>_<task>.out``: task logs, segmented per unit by CFL_UNIT_* markers.

Runs made before the monitor existed have none of these; ``driver_log_arrays`` reconstructs the
submitted arrays from the driver log so sacct can still be queried.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SUBMISSION_FIELDS = [
    "ts",
    "driver_job",
    "module",
    "phase",
    "array_id",
    "offset",
    "index_range",
    "n_tasks",
    "first_idx",
    "last_idx",
    "max_limit",
]


@dataclass(frozen=True)
class Submission:
    ts: int
    driver_job: str
    module: str
    phase: str  # canary | batch | fixed
    array_id: str
    offset: int
    index_range: int
    n_tasks: int
    first_idx: int
    last_idx: int
    max_limit: int

    def indices(self) -> range:
        """Unit indices this array covers (clamped to max_limit like the module scripts)."""
        if self.phase == "fixed":
            return range(self.n_tasks)
        last = min(self.last_idx, self.max_limit - 1) if self.max_limit else self.last_idx
        return range(self.first_idx, last + 1)


def read_submissions(monitor_dir: Path, driver_job: str | None = None, module: str | None = None) -> list[Submission]:
    path = Path(monitor_dir) / "submissions.tsv"
    rows: list[Submission] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) < len(SUBMISSION_FIELDS):
            continue
        try:
            s = Submission(
                ts=int(parts[0]),
                driver_job=parts[1],
                module=parts[2],
                phase=parts[3],
                array_id=parts[4],
                offset=int(parts[5]),
                index_range=int(parts[6]),
                n_tasks=int(parts[7]),
                first_idx=int(parts[8]),
                last_idx=int(parts[9]),
                max_limit=int(parts[10]),
            )
        except ValueError:
            continue
        if driver_job and s.driver_job != driver_job:
            continue
        if module and s.module != module:
            continue
        rows.append(s)
    return rows


def latest_driver_job(monitor_dir: Path) -> str | None:
    rows = read_submissions(monitor_dir)
    return rows[-1].driver_job if rows else None


# ------------------------------------------------------------------------------------- units
@dataclass
class UnitRecord:
    module: str
    array_id: str
    task_id: str
    idx: str  # "17", or "20-39" for range calls
    unit: str
    begin: int | None = None
    end: int | None = None
    rc: int | None = None  # None = BEGIN without END (running, or LOST if the task is over)
    secs: int | None = None
    node: str = ""

    @property
    def finished(self) -> bool:
        return self.rc is not None

    def indices(self) -> list[int]:
        if re.fullmatch(r"\d+", self.idx):
            return [int(self.idx)]
        m = re.fullmatch(r"(\d+)-(\d+)", self.idx)
        if m:
            return list(range(int(m.group(1)), int(m.group(2)) + 1))
        return []


@dataclass
class TaskUnits:
    """All unit records of one array task (one TSV file)."""

    module: str
    array_id: str
    task_id: str
    path: Path
    records: list[UnitRecord] = field(default_factory=list)


def _parse_unit_lines(lines, module: str, array_id: str, task_id: str) -> list[UnitRecord]:
    open_recs: dict[str, UnitRecord] = {}
    done: list[UnitRecord] = []
    for line in lines:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 10:
            continue
        event, _mod, _arr, _task, idx, unit, rc, epoch, secs, node = parts[:10]
        if event == "BEGIN":
            if idx in open_recs:  # a re-run of the same index inside one task: keep the last
                done.append(open_recs.pop(idx))
            open_recs[idx] = UnitRecord(module, array_id, task_id, idx, unit, begin=_int(epoch), node=node)
        elif event == "END":
            rec = open_recs.pop(idx, None) or UnitRecord(module, array_id, task_id, idx, unit, node=node)
            rec.rc = _int(rc)
            rec.end = _int(epoch)
            rec.secs = _int(secs)
            done.append(rec)
    return done + list(open_recs.values())


def _int(s: str) -> int | None:
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def units_dir(monitor_dir: Path, module: str) -> Path:
    return Path(monitor_dir) / "units" / module


def read_task_units(monitor_dir: Path, module: str, arrays: set[str] | None = None) -> list[TaskUnits]:
    d = units_dir(monitor_dir, module)
    out: list[TaskUnits] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.tsv")):
        array_id, _, task_id = p.stem.partition("_")
        if arrays is not None and array_id not in arrays:
            continue
        try:
            lines = p.read_text().splitlines()
        except OSError:
            continue
        out.append(TaskUnits(module, array_id, task_id, p, _parse_unit_lines(lines, module, array_id, task_id)))
    return out


def has_unit_ledger(monitor_dir: Path, module: str) -> bool:
    d = units_dir(monitor_dir, module)
    return d.is_dir() and any(d.glob("*.tsv"))


class UnitTail:
    """Incremental reader for the watcher: re-reads only files whose size changed."""

    def __init__(self, monitor_dir: Path):
        self.monitor_dir = Path(monitor_dir)
        self._sizes: dict[Path, int] = {}
        self._tasks: dict[Path, TaskUnits] = {}

    def refresh(self, module: str, arrays: set[str] | None = None) -> list[TaskUnits]:
        d = units_dir(self.monitor_dir, module)
        if d.is_dir():
            for p in d.glob("*.tsv"):
                array_id, _, task_id = p.stem.partition("_")
                if arrays is not None and array_id not in arrays:
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                if self._sizes.get(p) == size and p in self._tasks:
                    continue
                try:
                    lines = p.read_text().splitlines()
                except OSError:
                    continue
                self._sizes[p] = size
                self._tasks[p] = TaskUnits(
                    module, array_id, task_id, p, _parse_unit_lines(lines, module, array_id, task_id)
                )
        return [t for p, t in self._tasks.items() if t.module == module and (arrays is None or t.array_id in arrays)]


# ------------------------------------------------------------------------------------- logs
MARK_BEGIN = re.compile(r"^CFL_UNIT_BEGIN module=\S+ idx=(\S+) unit=(\S+)")
MARK_END = re.compile(r"^CFL_UNIT_END module=\S+ idx=(\S+) unit=(\S+) rc=(-?\d+)")


def task_log_path(report_dir: Path, module: str, array_id: str, task_id: str) -> Path:
    return Path(report_dir) / f"{module}.{array_id}_{task_id}.out"


def module_logs(report_dir: Path, module: str) -> list[Path]:
    """All task logs of a module (new %A_%a names and old %j_%a names alike)."""
    return sorted(Path(report_dir).glob(f"{module}.*.out"))


def read_log(path: Path, max_bytes: int = 20_000_000) -> list[str]:
    try:
        with open(path, errors="replace") as f:
            return f.read(max_bytes).splitlines()
    except OSError:
        return []


def segment_log(lines: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Split a task log into {idx: lines} using CFL markers; lines outside any unit go to 'other'."""
    segments: dict[str, list[str]] = {}
    other: list[str] = []
    current: str | None = None
    for line in lines:
        m = MARK_BEGIN.match(line)
        if m:
            current = m.group(1)
            segments.setdefault(current, [])
            continue
        if MARK_END.match(line):
            current = None
            continue
        if current is None:
            other.append(line)
        else:
            segments[current].append(line)
    return segments, other


DRIVER_START = re.compile(r"Starting submission for: (\S+)\.sh")
DRIVER_ARRAY = re.compile(r"Waiting for (?:job )?array (\d+) to finish")


def driver_logs(log_dir: Path) -> list[Path]:
    # The monitor and the audit job write monitor_<jobid>.out / audit_<jobid>.out next to the driver logs;
    # run names may start with "monitor_" or "audit_".
    logs = [p for p in Path(log_dir).glob("*.out") if not re.fullmatch(r"(monitor|audit)_\d+\.out", p.name)]
    return sorted(logs, key=lambda p: p.stat().st_mtime)


def driver_log_arrays(log_dir: Path) -> dict[str, list[str]]:
    """{module: [array ids]} reconstructed from old-style driver logs (pre-monitor runs)."""
    out: dict[str, list[str]] = {}
    for p in driver_logs(log_dir):
        module = None
        for line in read_log(p):
            m = DRIVER_START.search(line)
            if m:
                module = m.group(1)
                continue
            m = DRIVER_ARRAY.search(line)
            if m and module:
                out.setdefault(module, [])
                if m.group(1) not in out[module]:
                    out[module].append(m.group(1))
    return out
