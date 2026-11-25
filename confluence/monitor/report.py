"""Files the monitor writes under <run>/monitor/: ABORT, events.log, status/audit JSON + text."""

from __future__ import annotations

import json
import os
import re
import socket
import time
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_atomic(path: Path, text: str):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{socket.gethostname()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def write_json(path: Path, obj):
    write_atomic(path, json.dumps(obj, indent=2, default=str) + "\n")


def event(monitor_dir: Path, source: str, message: str):
    monitor_dir = Path(monitor_dir)
    monitor_dir.mkdir(parents=True, exist_ok=True)
    with open(monitor_dir / "events.log", "a") as f:
        f.write(f"{now_iso()}\t{source}\t{message}\n")


def abort(monitor_dir: Path, source: str, reason: str, findings: list[dict] | None = None) -> bool:
    """Create <monitor>/ABORT exclusively. Returns True if this call created it (first reason wins)."""
    monitor_dir = Path(monitor_dir)
    monitor_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "time": now_iso(),
        "epoch": int(time.time()),
        "source": source,
        "reason": reason,
        "host": socket.gethostname(),
        "slurm_job": os.environ.get("SLURM_JOB_ID"),
        "findings": findings or [],
    }
    try:
        fd = os.open(monitor_dir / "ABORT", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o664)
    except FileExistsError:
        event(monitor_dir, source, f"abort requested but ABORT already exists: {reason}")
        return False
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(payload, indent=2, default=str) + "\n")
    event(monitor_dir, source, f"ABORT: {reason}")
    return True


def read_concurrent_jobs(monitor_dir: Path) -> int | None:
    """The live override of hpc.concurrent_jobs in <monitor>/concurrent_jobs, if any."""
    try:
        return int((Path(monitor_dir) / "concurrent_jobs").read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def set_concurrent_jobs(monitor_dir: Path, n: int | None):
    """Write <monitor>/concurrent_jobs (n=None removes it, so the driver goes back to hpc.concurrent_jobs).
    The driver applies it to the array in flight (scontrol ArrayTaskThrottle) and submits later arrays with it."""
    p = Path(monitor_dir) / "concurrent_jobs"
    if n is None:
        p.unlink(missing_ok=True)
    else:
        write_atomic(p, f"{int(n)}\n")


def read_abort(monitor_dir: Path) -> dict | None:
    p = Path(monitor_dir) / "ABORT"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except ValueError:
        return {"reason": p.read_text().strip()}


def fmt_table(rows: list[list], headers: list[str]) -> str:
    """Plain-text table (also valid-looking in Markdown code blocks)."""
    cols = [headers] + [[("" if c is None else str(c)) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cols) for i in range(len(headers))]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))]
    lines.append("  ".join("-" * w for w in widths))
    for r in cols[1:]:
        lines.append("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)))
    return "\n".join(lines)


def md_table(rows: list[list], headers: list[str]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


# ------------------------------------------------------------------------------------ HTML
_CSS = """
:root { --fg:#1f2328; --muted:#59636e; --bg:#ffffff; --line:#d1d9e0; --stripe:#f6f8fa; --code:#f6f8fa;
  --pass:#1a7f37; --warn:#9a6700; --fail:#cf222e; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --fg:#e6edf3; --muted:#9198a1;
  --bg:#0d1117; --line:#3d444d; --stripe:#151b23; --code:#151b23; --pass:#3fb950; --warn:#d29922; --fail:#f85149; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 14px/1.5 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 24px; margin: 0 0 8px; }
h2 { font-size: 19px; margin: 32px 0 8px; padding-bottom: 4px; border-bottom: 1px solid var(--line); }
h3 { font-size: 15px; margin: 0; display: inline; }
p, li { margin: 4px 0; }
code { font: 12.5px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: var(--code);
  padding: 1px 4px; border-radius: 4px; }
pre { background: var(--code); border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px;
  overflow-x: auto; margin: 6px 0 14px; }
pre code { padding: 0; background: none; font-size: 12px; }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; margin: 8px 0; font-size: 13px; }
th, td { border: 1px solid var(--line); padding: 4px 8px; text-align: left; white-space: nowrap; }
th { background: var(--stripe); white-space: normal; vertical-align: bottom; }
tbody tr:nth-child(even) { background: var(--stripe); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.verdict { display: inline-block; font-weight: 700; padding: 1px 10px; border-radius: 12px; color: #fff; }
.verdict.PASS { background: var(--pass); } .verdict.WARN { background: var(--warn); }
.verdict.FAIL { background: var(--fail); }
details { border: 1px solid var(--line); border-radius: 6px; padding: 6px 12px; margin: 8px 0; }
details > summary { cursor: pointer; }
details[open] > summary { margin-bottom: 6px; }
@page { size: letter portrait; margin: 12mm; }
@media print {
  body { font-size: 11px; background: #fff; color: #000; }
  main { max-width: none; padding: 0; }
  h2 { break-after: avoid; }
  pre, tr, details > summary { break-inside: avoid; }
  pre { white-space: pre-wrap; overflow: visible; }
  th, td { white-space: normal; }
  .tablewrap { overflow: visible; }
}
"""

_NUM_CELL = re.compile(r"<td>([\d,.%() /–-]+)</td>")


def _errors_collapsible(html: str) -> str:
    """Turn each `<h3>module</h3> ...` block of the Errors section into a collapsible <details>."""
    start = html.find("<h2>Errors</h2>")
    if start < 0:
        return html
    end = html.find("<h2>", start + 1)
    end = len(html) if end < 0 else end
    sec = html[start:end]
    parts = re.split(r"(<h3>.*?</h3>)", sec)
    out = [parts[0]]
    for i in range(1, len(parts), 2):
        body = parts[i + 1] if i + 1 < len(parts) else ""
        n = len(re.findall(r"<pre>", body))
        label = f"{n} error message{'s' if n != 1 else ''}"
        out.append(f"<details open><summary>{parts[i]} <small>({label})</small></summary>{body}</details>")
    return html[:start] + "".join(out) + html[end:]


def _escape_angles(md_text: str) -> str:
    """Escape `<` outside code so placeholders like `<path>` in error text aren't read as HTML tags."""
    out = []
    for i, block in enumerate(re.split(r"(^```.*?^```)", md_text, flags=re.S | re.M)):
        if i % 2:  # fenced code block: markdown escapes it itself
            out.append(block)
            continue
        parts = re.split(r"(`[^`\n]*`)", block)
        out.append("".join(p if j % 2 else p.replace("<", "&lt;") for j, p in enumerate(parts)))
    return "".join(out)


def write_html(path: Path, md_text: str, title: str = "") -> None:
    """Render a markdown report as one self-contained HTML file (inline CSS, no external assets).

    Tables size their own columns, errors per module are collapsible, and the page prints to a
    portrait PDF from any browser (all sections are expanded before printing).
    """
    import html as _html

    import markdown

    body = markdown.markdown(_escape_angles(md_text), extensions=["tables", "fenced_code", "sane_lists"])
    body = re.sub(
        r"<strong>Verdict: (PASS|WARN|FAIL)</strong>",
        r'<span class="verdict \1">\1</span>',
        body,
    )
    body = _NUM_CELL.sub(r'<td class="num">\1</td>', body)
    body = body.replace("<table>", '<div class="tablewrap"><table>').replace("</table>", "</table></div>")
    body = _errors_collapsible(body)
    doc = (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_html.escape(title or 'Run audit')}</title><style>{_CSS}</style></head>"
        f"<body><main>{body}</main>"
        "<script>window.addEventListener('beforeprint',()=>document.querySelectorAll('details')"
        ".forEach(d=>d.open=true));</script></body></html>\n"
    )
    write_atomic(Path(path), doc)
