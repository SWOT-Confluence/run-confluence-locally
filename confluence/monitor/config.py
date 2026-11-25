"""Settings for the run gate / monitor / audit (the optional `monitor:` block of a run config)."""

from pydantic import BaseModel, ConfigDict, Field


class ModuleMonitorSettings(BaseModel):
    """Per-module overrides. Unset fields fall back to the run-wide value or the registry default."""

    model_config = ConfigDict(extra="forbid")

    gate: bool | None = None  # False disables gating (and the canary) for this module
    canary_tasks: int | None = Field(None, ge=0)
    abort_fail_frac: float | None = Field(None, ge=0, le=1)
    abort_coverage: float | None = Field(None, ge=0, le=1)
    warn_coverage: float | None = Field(None, ge=0, le=1)


class MonitorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True  # gates + canary in the driver
    watcher: bool = True  # also run the monitor inside the driver job and queue the end-of-run audit job
    canary_tasks: int = Field(0, ge=0)  # off by default; e.g. 2 tests each per-reach module on a small batch first
    poll_seconds: int = Field(60, ge=10)
    # Rules only fire once this many eligible units (inputs present) have finished, or all of them
    # if the scope is smaller, but never on fewer than min_units_floor.
    min_units: int = Field(30, ge=1)
    min_units_floor: int = Field(5, ge=1)
    abort_fail_frac: float = Field(0.5, ge=0, le=1)
    warn_fail_frac: float = Field(0.1, ge=0, le=1)
    # A fatal signature (apptainer FATAL, ImportError, ...) in at least this many units and this
    # fraction of finished units aborts the run.
    fatal_min_units: int = Field(3, ge=1)
    fatal_frac: float = Field(0.2, ge=0, le=1)
    # Outputs probed (opened with netCDF4) per gate call; the rest of the scope is only stat'ed.
    gate_probe_sample: int = Field(60, ge=0)
    # How long the gate waits for unit files of finished tasks to become visible over NFS.
    gate_wait_seconds: int = Field(60, ge=0)
    # Fatal pending reasons (InvalidAccount, ...) older than this abort the run.
    pending_fatal_minutes: int = Field(10, ge=1)
    benign_patterns: list[str] = Field(default_factory=list)
    fatal_patterns: list[str] = Field(default_factory=list)
    modules: dict[str, ModuleMonitorSettings] = Field(default_factory=dict)

    def for_module(self, name: str, field: str, registry_default=None):
        """Resolve a setting: per-module override > registry default (if given) > run-wide value."""
        override = self.modules.get(name)
        if override is not None and getattr(override, field, None) is not None:
            return getattr(override, field)
        if registry_default is not None:
            return registry_default
        return getattr(self, field, None)
