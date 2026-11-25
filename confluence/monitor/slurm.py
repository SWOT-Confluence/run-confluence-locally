"""Thin wrappers around squeue / sacct / scancel. The runner is injectable so tests can fake Slurm."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

Runner = Callable[[list[str]], tuple[int, str, str]]


def default_runner(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


class Slurm:
    def __init__(self, runner: Runner | None = None, user: str | None = None):
        self.run = runner or default_runner
        self.user = user or os.environ.get("USER", "")

    # ------------------------------------------------------------------ squeue
    def queue(self) -> list[QueueRow] | None:
        """This user's jobs, or None if squeue failed (callers must not read that as 'empty')."""
        rc, out, _ = self.run(["squeue", "-h", "-u", self.user, "-r", "-o", "%i|%j|%T|%r|%V|%N"])
        if rc != 0:
            return None
        rows = []
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) >= 6:
                rows.append(QueueRow(*parts[:6]))
        return rows

    def job_present(self, job_id: str) -> bool | None:
        rc, out, err = self.run(["squeue", "-h", "-j", str(job_id), "-o", "%i"])
        if rc != 0:
            if "invalid job id" in (out + err).lower():
                return False
            return None
        return bool(out.strip())

    # ------------------------------------------------------------------ sacct
    def sacct(self, job_ids: list[str]) -> list[dict]:
        """Allocation rows (one per array task) merged with their .batch step's MaxRSS."""
        if not job_ids:
            return []
        fields = (
            "JobID,JobName,State,ExitCode,Elapsed,ElapsedRaw,Timelimit,TimelimitRaw,"
            "ReqMem,MaxRSS,NodeList,CPUTimeRAW,Start,End"
        )
        rows: dict[str, dict] = {}
        for chunk in _chunks(job_ids, 50):
            rc, out, _ = self.run(["sacct", "-n", "-P", "-j", ",".join(chunk), f"--format={fields}"])
            if rc != 0:
                continue
            names = fields.split(",")
            for line in out.splitlines():
                parts = line.split("|")
                if len(parts) != len(names):
                    continue
                d = dict(zip(names, parts, strict=True))
                jid = d["JobID"]
                if "." in jid:
                    base, step = jid.split(".", 1)
                    if step == "batch" and base in rows:
                        rows[base]["MaxRSS"] = d["MaxRSS"]
                    continue
                rows[jid] = d
        return list(rows.values())

    # ------------------------------------------------------------------ scancel
    def scancel(self, job_ids: list[str]) -> bool:
        if not job_ids:
            return True
        rc, _, _ = self.run(["scancel", *[str(j) for j in job_ids]])
        return rc == 0


@dataclass
class QueueRow:
    job_id: str
    name: str
    state: str
    reason: str
    submit: str
    nodes: str

    @property
    def array_id(self) -> str:
        return self.job_id.split("_")[0]


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


_MEM = re.compile(r"^([\d.]+)([KMGT]?)")


def mem_bytes(s: str | None) -> float | None:
    """Parse sacct memory strings (MaxRSS '1234K', ReqMem '16G' / '4000Mn' / '4Gc')."""
    if not s:
        return None
    m = _MEM.match(s.strip())
    if not m:
        return None
    mult = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}[m.group(2)]
    return float(m.group(1)) * mult


def task_id_of(job_id: str) -> str | None:
    """'123_45' -> '45'; '123_[0-5]' (pending range) -> None."""
    _, _, t = job_id.partition("_")
    return t if t.isdigit() else None


FATAL_PENDING_REASONS = {
    "InvalidAccount",
    "InvalidQOS",
    "PartitionConfig",
    "BadConstraints",
    "AccountNotAllowed",
    "QOSNotAllowed",
    "PartitionNodeLimit",
    "PartitionTimeLimit",
    "JobHeldAdmin",
}
