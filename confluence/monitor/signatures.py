"""Error-line extraction and normalisation, so the same failure on 300 reaches counts as one signature."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

ERROR_LINE = re.compile(
    r"(?i)(\berror\b|\w+(error|exception)\b:|exception|traceback|\bfatal\b|halted|\bfailed\b|"
    r"segmentation fault|core dumped|"
    r"\bkilled\b|no such file|permission denied|out of memory|oom-kill|cancelled|time limit)"
)

# Messages that look like errors but are expected in healthy runs (see RUN_GUIDE "benign noise").
BENIGN_PATTERNS = [
    r"SSM Error: You must specify a region",  # input: no AWS key lookup outside AWS
    r"has no data in SWOT_",  # input: reach without observations
    r"No data returned for reach_id",
    r"decided not to run",  # momma
    r"No data for reach '",  # consensus
    r"Could not read flpe file",  # validation: algorithm that did not run
    r"No such file or directory: '/mnt/data/flpe/",  # validation: same
    r"MetroMan will not run for this set",
    r"Unable to unlink domain socket",  # slurmd noise
    r"delta_grad == 0\.0",  # scipy warning
    r"DeprecationWarning",
    r"UserWarning",
    r"RuntimeWarning",
    r"^\s*warnings\.warn\(",
    r"error_call = error_call",  # R traceback frame lines, not the error itself
    r"with_subscript_errors",
    r"^CFL_",  # our own markers
    r"\bfailed=0\b",
    r"failed test \d+ with invalid values in h_break",  # input: routine HWS fitting messages
    r"Optimization failed, using set breakpoint fit",
    r"(Piecewise|Simple) linear regression failed",  # hivdi: routine preprocessing warnings
    r"Dataset with no valid observation",  # hivdi: no-data reach
    r"Preprocessing failed: error_code=",  # hivdi: follows the no-data message
]

# Lines that mark an error but don't say which one; they are not counted as signatures (the
# specific line next to them is).
GENERIC_PATTERNS = [
    r"^\s*Traceback \(most recent call last\):",
    r"^\s*The above exception was the direct cause",
    r"^\s*During handling of the above exception",
    r"^\s*Execution halted\s*$",
    r"^\s*Warning messages?:\s*$",
]

# Messages that mean the module cannot work at all (not a data problem with one reach).
FATAL_PATTERNS = [
    r"^FATAL:",  # apptainer: image/exec failures
    r"FATAL:\s+(container creation failed|could not open image|stat .*no such file)",
    r"ModuleNotFoundError",
    r"ImportError",
    r"\.sif.*(no such file|not found|does not exist)",
    r"exec format error",
    r"executable file not found",
    r"NetCDF: Unknown file format",
    r"(?i)permission denied.*(/app|/mnt/data|\.sif)",
    r"cannot open shared object file",
]

_PROGRESS = re.compile(r"^.*?\d+%\|[^|]*\|[^\]]*\]")  # tqdm progress bars glued to log lines
_TS = re.compile(r"^\s*(\[?\d{4}-\d\d-\d\d[T ][\d:,.]+\]?|\d{2}:\d{2}:\d{2}[,.]\d+)\s*")
_ANSI = re.compile(r"\x1b\[[0-9;]*m|\[\d+(;\d+)*m")
_PATH = re.compile(r"(?<![\w.])/[^\s:'\"(),\]]+")
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b")
_NUM = re.compile(r"\d+(\.\d+)?([eE][-+]?\d+)?")
_WS = re.compile(r"\s+")


def normalize(line: str, max_len: int = 160) -> str:
    s = _ANSI.sub("", line)
    s = _PROGRESS.sub("", s)
    s = _TS.sub("", s)
    s = _PATH.sub("<path>", s)
    s = _HEX.sub("0xN", s)
    s = _NUM.sub("N", s)
    s = _WS.sub(" ", s).strip()
    return s[:max_len]


class SignatureMatcher:
    def __init__(self, extra_benign: list[str] | None = None, extra_fatal: list[str] | None = None):
        self.benign = [re.compile(p) for p in BENIGN_PATTERNS + list(extra_benign or [])]
        self.fatal = [re.compile(p) for p in FATAL_PATTERNS + list(extra_fatal or [])]
        self.generic = [re.compile(p) for p in GENERIC_PATTERNS]

    def is_benign(self, line: str) -> bool:
        return any(p.search(line) for p in self.benign)

    def is_fatal(self, line: str) -> bool:
        return any(p.search(line) for p in self.fatal)

    def error_lines(self, lines: list[str]) -> list[str]:
        return [ln for ln in lines if ERROR_LINE.search(ln) and not self.is_benign(ln)]

    def signatures(self, lines: list[str]) -> set[str]:
        """Distinct normalised error signatures in a block of log lines."""
        return {normalize(ln) for ln in self.error_lines(lines) if not any(g.search(ln) for g in self.generic)}

    def fatal_signatures(self, lines: list[str]) -> set[str]:
        return {normalize(ln) for ln in lines if self.is_fatal(ln)}


@dataclass
class SignatureTable:
    """Counts each signature once per unit (or per task log when units can't be separated)."""

    counts: Counter = field(default_factory=Counter)
    examples: dict[str, str] = field(default_factory=dict)
    fatal: set[str] = field(default_factory=set)

    def add(self, sigs: set[str], example: str, fatal: set[str] | None = None):
        for s in sigs:
            self.counts[s] += 1
            self.examples.setdefault(s, example)
        if fatal:
            self.fatal |= fatal
            for s in fatal:
                if s not in sigs:
                    self.counts[s] += 1
                    self.examples.setdefault(s, example)

    def top(self, n: int = 5) -> list[dict]:
        return [
            {"signature": s, "count": c, "example": self.examples.get(s, ""), "fatal": s in self.fatal}
            for s, c in self.counts.most_common(n)
        ]


# ------------------------------------------------------------------ full-message excerpts for reports
_STOP_FWD = re.compile(r"^(CFL_|Backtrace:|Traceback \(most recent call last\)|\s*▆\s*$)")
_LOGLINE = re.compile(r"^\s*(\[?\d{4}-\d\d-\d\d[T ][\d:,.]+)")
_PY_EXC = re.compile(r"^(\w+\.)*\w*(Error|Exception|Interrupt)\b|^\s+raise\b")
_FRAME = re.compile(r'^\s+File "')


def error_excerpt(lines: list[str], signature: str, max_fwd: int = 8, frames: int = 2, width: int = 200) -> str:
    """The whole error message around the first line that normalises to `signature`.

    A signature is one log line, but many errors span several lines: R's `Error in f():` /
    `Caused by error in g():` / `! the actual reason`, or a Python traceback. Reports show this
    excerpt instead. For Python exceptions it adds the innermost `frames` traceback frames; forwards
    it takes lines until a CFL marker, an R backtrace, a new traceback, a timestamped log line or a
    blank line.
    """
    clean = [_ANSI.sub("", ln.rstrip("\n")) for ln in lines]
    hit = next((i for i, ln in enumerate(clean) if normalize(ln) == signature), None)
    if hit is None:
        return ""
    out: list[str] = []
    if _PY_EXC.match(clean[hit]):
        tb = next(
            (i for i in range(hit - 1, max(hit - 200, -1), -1) if clean[i].startswith("Traceback (most recent")), None
        )
        if tb is not None:
            frame_idx = [i for i in range(tb + 1, hit) if _FRAME.match(clean[i])][-frames:]
            out.append(clean[tb])
            if frame_idx:
                if frame_idx[0] > tb + 1:
                    out.append("  ...")
                out += clean[frame_idx[0] : hit]
    out.append(clean[hit])
    end = hit + 1
    while end < len(clean) and end - hit <= max_fwd:
        ln = clean[end]
        if not ln.strip() or _STOP_FWD.match(ln) or _LOGLINE.match(ln):
            break
        out.append(ln)
        end += 1
    return "\n".join(ln if len(ln) <= width else ln[: width - 1] + "…" for ln in out)
