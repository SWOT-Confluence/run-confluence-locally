from confluence.monitor.signatures import SignatureMatcher, SignatureTable, normalize

# Real lines from module report logs.
HIVDI = (
    "2026-10-26T14:01:02,123 [1;31m[ERROR   ][0m "
    "Run failed: IndexError('index 12 is out of bounds for axis 0 with size 12')"
)
SIC = (
    "2026-10-26 14:01:02,123 [ERRO]  Error computing reach_id 12345600011: "
    "too many values to unpack (expected 2) ... skip"
)
SSM = "2026-10-27T12:18:23,199 root ERROR SSM Error: You must specify a region."
VALID = "[Errno 2] No such file or directory: '/mnt/data/flpe/momma/63450800091_momma.nc'"


def test_normalize_collapses_ids_numbers_paths():
    a = normalize("Error computing reach_id 12345600011: float division by zero")
    b = normalize("Error computing reach_id 99999900021: float division by zero")
    assert a == b == "Error computing reach_id N: float division by zero"
    assert "<path>" in normalize("open.nc(/mnt/data/input/swot/1_SWOT.nc) failed")


def test_normalize_strips_timestamps_and_ansi():
    s = normalize(HIVDI)
    assert s.startswith("[ERROR ] Run failed: IndexError")
    assert "2026" not in s


def test_benign_and_fatal():
    m = SignatureMatcher()
    assert m.is_benign(SSM)
    assert m.is_benign(VALID)
    assert m.error_lines([SSM, VALID]) == []
    assert m.error_lines([SIC]) == [SIC]
    assert m.is_fatal("FATAL:   could not open image /x/sad.sif: failed to retrieve path")
    assert m.is_fatal("ModuleNotFoundError: No module named 'swotq'")
    assert m.is_fatal("Error in RNetCDF: NetCDF: Unknown file format")
    assert not m.is_fatal(SIC)


def test_extra_patterns_from_config():
    m = SignatureMatcher(extra_benign=["Preprocessing failed"], extra_fatal=["boom!"])
    assert m.is_benign("[ERROR] Preprocessing failed: error_code=3")
    assert m.is_fatal("it went boom!")


def test_table_counts_once_per_unit():
    t = SignatureTable()
    m = SignatureMatcher()
    for i in range(3):
        t.add(m.signatures([SIC, SIC]), f"log{i}")
    top = t.top()
    assert top[0]["count"] == 3
    assert top[0]["example"] == "log0"


def test_progress_bars_and_generic_lines():
    m = SignatureMatcher()
    tqdm = " 40%|████      | 2/5 [00:01<00:02, 1.5it/s]2026-10-27 12:00:01,123 [ERROR   ] Run failed: KeyError('x')"
    assert normalize(tqdm).startswith("[ERROR ] Run failed: KeyError")
    lines = ["Traceback (most recent call last):", "  File x", "KeyError: 'reach'", "Execution halted"]
    assert m.signatures(lines) == {"KeyError: 'reach'"}
    assert m.signatures(["2026-10-27 [ERROR ] Dataset with no valid observation"]) == set()


# Real multi-line errors from module report logs.
MOMMA_LOG = """[1] "decided not to run"
Error in `summarise()`:
ℹ In argument: `across(...)`.
Caused by error in `across()`:
! Can't select columns that don't exist.
✖ Column `nb` doesn't exist.
Backtrace:
     ▆
  1. ├─global run_momma()
CFL_UNIT_END module=unconstrained_momma idx=46 unit=63440400011 rc=1 secs=5""".splitlines()

HIVDI_LOG = """2026-11-10 22:32:27,804 [INFO    ] - Run passed: 0/6652
Traceback (most recent call last):
  File "/app/H2iVDI/bin/h2ivdi_cli.py", line 497, in <module>
    process_runs(args.runs_file, index=args.run_index)
  File "/app/H2iVDI/bin/h2ivdi_cli.py", line 417, in process_runs
    raise RuntimeError("Critical error(s) detected")
RuntimeError: Critical error(s) detected
CFL_UNIT_END module=hivdi idx=66 unit=12950500061 rc=1 secs=7""".splitlines()


def test_error_excerpt_keeps_the_whole_r_message():
    from confluence.monitor.signatures import error_excerpt

    ex = error_excerpt(MOMMA_LOG, normalize("Error in `summarise()`:"))
    assert ex.splitlines()[0] == "Error in `summarise()`:"
    assert "Column `nb` doesn't exist." in ex
    assert "Backtrace" not in ex and "decided not to run" not in ex


def test_error_excerpt_adds_python_traceback_frames():
    from confluence.monitor.signatures import error_excerpt

    ex = error_excerpt(HIVDI_LOG, normalize("RuntimeError: Critical error(s) detected"))
    lines = ex.splitlines()
    assert lines[0].startswith("Traceback") and lines[-1] == "RuntimeError: Critical error(s) detected"
    assert "Run passed" not in ex and "CFL_" not in ex
    assert error_excerpt(HIVDI_LOG, "not in the log") == ""


def test_signatures_of_one_message_are_merged(tmp_path):
    from confluence.monitor.audit import _with_excerpts

    log = tmp_path / "momma.out"
    log.write_text("\n".join(MOMMA_LOG))
    sigs = [
        {"signature": normalize(s), "count": 104, "example": str(log), "fatal": False}
        for s in ("Error in `summarise()`:", "Caused by error in `across()`:")
    ]
    merged = _with_excerpts(sigs, {})
    assert len(merged) == 1 and merged[0]["count"] == 104
    assert merged[0]["excerpt"].startswith("Error in `summarise()`:")
