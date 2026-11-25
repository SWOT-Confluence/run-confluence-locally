"""`locate`: where one reach is in every module - its index, unit class, output file and task log."""

from __future__ import annotations

import re

from confluence.monitor import outputs as O
from confluence.monitor.assess import _upstream_ok, classify
from confluence.monitor.context import RunContext
from confluence.monitor.ledger import (
    has_unit_ledger,
    read_log,
    read_submissions,
    read_task_units,
    segment_log,
    task_log_path,
)
from confluence.monitor.registry import BASINS, CONTINENTS
from confluence.monitor.signatures import SignatureMatcher


def _find_idx(ctx: RunContext, spec, reach_id: str) -> tuple[int, str] | None:
    if spec.scale == "global":
        return 0, "global"
    units = ctx.units(spec.index)
    if spec.scale == "set":
        for i, u in units:
            if reach_id in u.split("-"):
                return i, u
        return None
    if spec.scale == "basin":
        for i, entry in enumerate(ctx.index(BASINS)):
            if isinstance(entry, dict) and int(reach_id) in [int(r) for r in entry.get("reach_id", [])]:
                return i, f"basin_{entry.get('basin_id')}"
        return None
    if spec.scale == "continent":
        digit = int(reach_id[0])
        for i, entry in enumerate(ctx.index(CONTINENTS)):
            if isinstance(entry, dict):
                code, digits = next(iter(entry.items()))
                if digit in digits:
                    return i, code
        return None
    for i, u in units:
        if u == reach_id:
            return i, u
    return None


def locate(run_dir: str, reach_id: str) -> list[dict]:
    ctx = RunContext(run_dir)
    rows = []
    for module in ctx.modules_to_run:
        spec = ctx.spec(module)
        found = _find_idx(ctx, spec, reach_id)
        row: dict = {"module": module, "scale": spec.scale}
        if found is None:
            row["status"] = f"not in {spec.index}"
            rows.append(row)
            continue
        idx, unit = found
        row.update(idx=idx, unit=unit)

        ledger = has_unit_ledger(ctx.monitor_dir, module)
        rec = None
        log = None
        subs = [s for s in read_submissions(ctx.monitor_dir, module=module) if idx in s.indices()]
        if ledger and subs:
            for s in reversed(subs):
                task = str(idx if s.phase == "fixed" else (idx - s.offset) // s.index_range)
                for t in read_task_units(ctx.monitor_dir, module, {s.array_id}):
                    if t.task_id == task:
                        for r in t.records:
                            if idx in r.indices():
                                rec = r
                if rec is not None or not log:
                    log = task_log_path(ctx.report_dir, module, s.array_id, task)
                if rec is not None:
                    break
        else:
            task = idx if spec.scale in ("continent", "global") else idx // ctx.reach_chunks(module)
            matches = sorted(ctx.report_dir.glob(f"{module}.*_{task}.out"))
            log = matches[-1] if matches else None
            row["log_note"] = "pre-monitor run: log guessed from the task index (assumes one batch)"

        nodata_hit = False
        if log is not None and log.exists():
            lines = read_log(log)
            segments, other = segment_log(lines)
            block = segments.get(rec.idx if rec else str(idx)) if segments else None
            if block is None:
                block = [ln for ln in lines if reach_id in ln] if not segments else other
            nodata_hit = any(re.search(p, ln) for p in spec.nodata_patterns for ln in block)
            row["log_lines_for_unit"] = len(block)
            # Why: the lines that explain the class (no-data messages and error signatures), deduplicated.
            matcher = SignatureMatcher(ctx.monitor.benign_patterns, ctx.monitor.fatal_patterns)
            why = [ln.strip() for ln in block if any(re.search(p, ln) for p in spec.nodata_patterns)]
            why += [ln.strip() for ln in matcher.error_lines(block) if not any(g.search(ln) for g in matcher.generic)]
            row["evidence"] = list(dict.fromkeys(why))[:4]
        out_path = O.resolve(ctx.path(spec.output, unit)) if spec.output else None
        state = O.deep_state(out_path, spec.probe) if spec.output else None
        up = _upstream_ok(ctx, spec, unit) if spec.requires else True
        if spec.funnel_output and spec.funnel_output != spec.output:
            fp = ctx.path(spec.funnel_output, reach_id)
            row["reach_product"] = str(fp)
            row["reach_product_state"] = O.deep_state(fp if fp.exists() else None, spec.funnel_probe)
        row.update(
            output=str(out_path or (ctx.path(spec.output, unit) if spec.output else "")) or None,
            output_state=state,
            upstream_ok=up,
            rc=rec.rc if rec else None,
            node=rec.node if rec else None,
            log=str(log) if log else None,
            cls=classify(spec, state, up, rec, nodata_hit, ledger, in_progress=False),
        )
        rows.append(row)
    return rows


def render_locate(rows: list[dict], reach_id: str) -> str:
    out = [f"Reach {reach_id}"]
    for r in rows:
        if "status" in r:
            out.append(f"\n{r['module']}: {r['status']}")
            continue
        out.append(
            f"\n{r['module']} [{r['scale']}] idx={r['idx']} unit={r['unit']} -> {r['cls']}"
            f" (rc={r['rc']}, output={r['output_state']}, inputs {'ok' if r['upstream_ok'] else 'MISSING'})"
        )
        if r.get("output"):
            out.append(f"  output: {r['output']}")
        if r.get("reach_product"):
            out.append(f"  per-reach product: {r['reach_product']} ({r['reach_product_state']})")
        if r.get("log"):
            out.append(f"  log:    {r['log']}" + (f"  ({r['log_note']})" if r.get("log_note") else ""))
        for ln in r.get("evidence", []):
            out.append(f"  why:    {ln[:160]}")
    return "\n".join(out) + "\n"
