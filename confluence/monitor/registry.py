"""What each Confluence module consumes and produces: the single source of truth for the gate rules,
the audit and RUN_GUIDE.md.

Paths are relative to the run's mount dir (``<run>/<run_name>_mnt``) and may contain ``{unit}``: the
unit id from the module's index file (reach id, ``rid-rid-...`` for metrosets, ``basin_<id>``, or a
continent code). Variable paths for probes use ``group/var``. They were checked against real module outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from confluence.utils.module_names import strip_modifiers

# Index files live in <mnt>/input.
REACHES = "reaches.json"
EXPANDED = "expanded_reaches_of_interest.json"
METROSETS = "metrosets.json"
BASINS = "basin.json"
CONTINENTS = "continent.json"

SWOT = "input/swot/{unit}_SWOT.nc"
PREDIAG = "diagnostics/prediagnostics/{unit}_prediagnostics.nc"


@dataclass(frozen=True)
class Probe:
    """How to tell whether an output file holds data.

    kind: "q" (variable has >= 1 finite, non-fill value), "time" (same for a time axis),
    "consensus" ("q" + time_str must not be all no_data), "opens" (netCDF opens and has
    variables), "json" (parses and is a non-empty list).
    """

    kind: str
    var: str | None = None


@dataclass(frozen=True)
class ModuleSpec:
    key: str
    scale: str  # reach | set | basin | continent | global | range
    index: str | None  # index json the unit index points into (None for global modules)
    description: str
    output: str | None = None  # per-unit output (pattern with {unit}); None = no per-unit file
    probe: Probe | None = None
    # Upstream files whose absence explains a failure (EXPLAINED), patterns with {unit}.
    requires: tuple[str, ...] = ()
    # Log lines meaning "this unit legitimately has no data". A named group (?P<unit>...) lets the
    # audit attribute them without CFL markers (old runs). When set, a unit with rc 0, no output and
    # no such line is SILENT (a silent failure, e.g. a rejected hydrocron collection).
    nodata_patterns: tuple[str, ...] = ()
    # If True, a unit with rc 0, no output and no nodata line is a SILENT failure (counted as bad).
    silent_is_failure: bool = False
    # Gate aborts when the has-data fraction of eligible finished units is below abort_coverage
    # (0 disables). The audit warns below warn_coverage. Calibrated on past full runs.
    abort_coverage: float = 0.10
    warn_coverage: float = 0.50
    # Modules whose units may legitimately produce no file at all (validation stats exist only for
    # gauged reaches): missing outputs are NODATA, never failures.
    sparse_output: bool = False
    # Whole-module products checked after the module ran (patterns relative to mnt, glob allowed).
    products: tuple[str, ...] = ()
    # Per-reach product tracked in the coverage funnel when it differs from the unit output
    # (metroman_consolidation writes per-reach files from per-continent tasks).
    funnel_index: str | None = None
    funnel_output: str | None = None
    funnel_probe: Probe | None = None
    quirks: tuple[str, ...] = field(default_factory=tuple)

    @property
    def per_unit_output(self) -> bool:
        return self.output is not None

    @property
    def funnel(self) -> tuple[str, str, Probe | None] | None:
        if self.funnel_output:
            return self.funnel_index or REACHES, self.funnel_output, self.funnel_probe
        if self.output and self.scale in ("reach", "range"):
            return self.index or REACHES, self.output, self.probe
        return None


_FLPE_REQUIRES = (SWOT,)

SPECS: dict[str, ModuleSpec] = {
    s.key: s
    for s in [
        ModuleSpec(
            key="expanded_setfinder",
            scale="continent",
            index=CONTINENTS,
            description="Expands the ROI to whole MetroMan sets, per continent.",
            output="input/expanded_reaches_of_interest_{unit}.json",
            probe=Probe("json"),
            sparse_output=True,
            abort_coverage=0.0,
            warn_coverage=0.0,
            quirks=("Continents without ROI reaches may legitimately produce no file.",),
        ),
        ModuleSpec(
            key="expanded_combine_data",
            scale="global",
            index=None,
            description="Merges the per-continent expanded lists into expanded_reaches_of_interest.json "
            "(the index for input).",
            products=("input/expanded_reaches_of_interest.json",),
        ),
        ModuleSpec(
            key="input",
            scale="reach",
            index=EXPANDED,
            description="Downloads SWOT RiverSP reach+node time series from Hydrocron into one netCDF per reach.",
            output=SWOT,
            probe=Probe("time", "reach/time"),
            nodata_patterns=(
                r"No data returned for reach_id = (?P<unit>\d+)",
                r"Feature ID (?P<unit>\d+) has no data in",
                r"All reaches already downloaded",
            ),
            silent_is_failure=True,
            abort_coverage=0.10,
            warn_coverage=0.90,
            quirks=(
                "Every reach logs `ERROR SSM Error: You must specify a region` (no AWS key lookup locally): benign.",
                "A 4xx client error (e.g. invalid collection name) is logged and the reach is skipped with exit 0; "
                "the monitor classifies such reaches as SILENT failures.",
                "With continue_downloads (-k) existing files are trusted, even 0-byte ones.",
            ),
        ),
        ModuleSpec(
            key="non_expanded_setfinder",
            scale="continent",
            index=CONTINENTS,
            description="Builds per-continent reach lists and MetroMan sets from the downloaded data.",
            output="input/reaches_{unit}.json",
            probe=Probe("json"),
            sparse_output=True,
            abort_coverage=0.0,
            warn_coverage=0.0,
        ),
        ModuleSpec(
            key="non_expanded_combine_data",
            scale="global",
            index=None,
            description="Merges per-continent lists into reaches.json, metrosets.json and basin.json "
            "(the indices for all downstream modules).",
            products=("input/reaches.json", "input/metrosets.json", "input/basin.json"),
        ),
        ModuleSpec(
            key="prediagnostics",
            scale="reach",
            index=REACHES,
            description="Flags/filters SWOT observations; rewrites the SWOT files in place and writes a "
            "diagnostics file per reach.",
            output=PREDIAG,
            probe=Probe("opens"),
            requires=(SWOT,),
            abort_coverage=0.30,
            warn_coverage=0.95,
        ),
        ModuleSpec(
            key="metroman",
            scale="set",
            index=METROSETS,
            description="MetroMan FLPE on each multi-reach set; writes one file per set.",
            output="flpe/metroman/sets/{unit}_metroman.nc",
            probe=Probe("opens"),
            nodata_patterns=(r"MetroMan will not run for this set",),
            abort_coverage=0.10,
            warn_coverage=0.85,
            quirks=("Set files are named by the set's reach ids joined with '-'.",),
        ),
        ModuleSpec(
            key="metroman_consolidation",
            scale="continent",
            index=CONTINENTS,
            description="Splits MetroMan set results into one file per reach.",
            funnel_index=REACHES,
            funnel_output="flpe/metroman/{unit}_metroman.nc",
            funnel_probe=Probe("q", "average/allq"),
            products=("flpe/metroman/*_metroman.nc",),
            warn_coverage=0.40,
        ),
        ModuleSpec(
            key="momma",
            scale="reach",
            index=REACHES,
            description="MOMMA FLPE (unconstrained_/constrained_ variants write the same directory).",
            output="flpe/momma/{unit}_momma.nc",
            probe=Probe("q", "Q"),
            requires=_FLPE_REQUIRES,
            nodata_patterns=(r"decided not to run",),
            warn_coverage=0.40,
            quirks=(
                "Prints 'decided not to run' and writes no file for reaches it skips.",
                "Healthy has-data fraction varies by pipeline version (0.43 to 0.88 of files).",
            ),
        ),
        ModuleSpec(
            key="busboi",
            scale="reach",
            index=REACHES,
            description="BUSBOI FLPE (R).",
            output="flpe/busboi/{unit}_busboi.nc",
            probe=Probe("q", "q/q"),
            requires=_FLPE_REQUIRES,
            warn_coverage=0.35,
            quirks=("~1-2% of reaches stop with `Error in optim(...)` / `Execution halted`: normal.",),
        ),
        ModuleSpec(
            key="hivdi",
            scale="reach",
            index=REACHES,
            description="H2iVDI FLPE.",
            output="flpe/hivdi/{unit}_hivdi.nc",
            probe=Probe("q", "reach/Q"),
            requires=_FLPE_REQUIRES,
            nodata_patterns=(r"Dataset with no valid observation",),
            warn_coverage=0.40,
            quirks=(
                "Writes a file for every reach; no-data reaches get a variable-less stub (counted as STUB).",
                "Older images write <rid>_h2ivdi.nc; the template renames it to <rid>_hivdi.nc.",
            ),
        ),
        ModuleSpec(
            key="sic4dvar",
            scale="reach",
            index=REACHES,
            description="SIC4DVar FLPE.",
            output="flpe/sic4dvar/{unit}_sic4dvar.nc",
            probe=Probe("q", "Q_da"),
            requires=_FLPE_REQUIRES,
            warn_coverage=0.50,
            quirks=(
                "Writes a file for every reach; catches per-reach exceptions itself "
                "(`[ERRO] Error computing reach_id ... skip`) and exits 0.",
            ),
        ),
        ModuleSpec(
            key="sad",
            scale="reach",
            index=REACHES,
            description="SAD FLPE (Julia).",
            output="flpe/sad/{unit}_sad.nc",
            probe=Probe("q", "Qa"),
            requires=_FLPE_REQUIRES,
            warn_coverage=0.45,
        ),
        ModuleSpec(
            key="consensus",
            scale="range",  # "reach" when module_args.per_reach is set
            index=REACHES,
            description="Consensus discharge (median/mean of the FLPEs that pass its filter).",
            output="flpe/consensus/{unit}_consensus.nc",
            probe=Probe("consensus", "consensus_q"),
            requires=_FLPE_REQUIRES,
            nodata_patterns=(r"No data for reach '(?P<unit>\d+)'",),
            warn_coverage=0.60,
            quirks=(
                "Range mode (-i START-END) loses the rest of a chunk when one reach crashes; per_reach avoids it.",
                "A crash while writing leaves consensus_q filled but time_str all no_data (PARTIAL).",
            ),
        ),
        ModuleSpec(
            key="moi",
            scale="basin",
            index=BASINS,
            description="Mean Optimization Integrator, per basin.",
            abort_coverage=0.0,
            warn_coverage=0.0,
        ),
        ModuleSpec(
            key="validation",
            scale="reach",
            index=REACHES,
            description="Scores FLPE/consensus discharge against SVS gauges; stats only for gauged reaches.",
            output="validation/stats/{unit}_validation.nc",
            probe=Probe("opens"),
            requires=_FLPE_REQUIRES,
            nodata_patterns=(r"no SVS gage match for reach (?P<unit>\d+)", r"No gauge found for reach"),
            sparse_output=True,
            abort_coverage=0.0,
            warn_coverage=0.0,
            quirks=(
                "Logs one `No such file` per algorithm file that is missing (benign; algorithms that did not run).",
                "Coverage is judged against a baseline run, not absolutely: only gauged reaches get stats.",
            ),
        ),
        ModuleSpec(
            key="output",
            scale="continent",
            index=CONTINENTS,
            description="Writes the per-continent SOS results file.",
            output="output/sos/{unit}_sword_v*_SOS_results_*.nc",
            probe=Probe("opens"),
            abort_coverage=0.99,
            warn_coverage=0.99,
        ),
    ]
}

# Canonical pipeline order (used to place modules found only through their logs).
PIPELINE_ORDER = [
    "expanded_setfinder",
    "expanded_combine_data",
    "input",
    "non_expanded_setfinder",
    "non_expanded_combine_data",
    "prediagnostics",
    "metroman",
    "metroman_consolidation",
    "unconstrained_momma",
    "constrained_momma",
    "busboi",
    "hivdi",
    "sic4dvar",
    "sad",
    "consensus",
    "moi",
    "validation",
    "output",
]

GENERIC = ModuleSpec(
    key="generic",
    scale="reach",
    index=REACHES,
    description="Module without a registry entry: only exit codes and logs are checked.",
    abort_coverage=0.0,
    warn_coverage=0.0,
)


def get_spec(module_name: str, module_args: dict | None = None) -> ModuleSpec:
    """Spec for a modules_to_run name (e.g. unconstrained_momma -> momma)."""
    module_args = module_args or {}
    spec = SPECS.get(module_name) or SPECS.get(strip_modifiers(module_name))
    if spec is None:
        return ModuleSpec(**{**GENERIC.__dict__, "key": module_name})
    if spec.key == "consensus" and module_args.get("per_reach"):
        return ModuleSpec(**{**spec.__dict__, "scale": "reach"})
    return spec


def is_fixed(spec: ModuleSpec) -> bool:
    return spec.scale in ("continent", "global")
