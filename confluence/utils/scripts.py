import json
import shutil
import sys
from importlib import resources
from pathlib import Path

from jinja2 import Environment, PackageLoader

from confluence.monitor.registry import get_spec
from confluence.utils.config import Config
from confluence.utils.module_names import get_repo_name

TEMPLATES_PATH = resources.files("confluence.templates")
LOAD_APPTAINER_SCRIPT = resources.files("confluence") / "scripts" / "load_apptainer.sh"
CFL_LIB_SCRIPT = resources.files("confluence") / "scripts" / "cfl_lib.sh"
REPO_ROOT = Path(__file__).resolve().parents[2]

# Most modules run per reach, but these modules run at continent or global scale.
# These always run just once.
GLOBAL_MODULES = [
    "expanded_combine_data",
    "non_expanded_combine_data",
    "lakeflow_input",
]
CONTINENT_MODULES = [
    "expanded_setfinder",
    "non_expanded_setfinder",
    "unconstrained_priors",
    "constrained_priors",
    "metroman_consolidation",
    "output",
]


def _get_platform_dict(platform: str):
    match platform:
        case "apptainer":
            bind_cmd = "--bind"
        case "docker":
            bind_cmd = "-v"
        case _:
            raise ValueError(f"{platform = } has not been implemented.")
    return {"run": platform, "bind": bind_cmd}


def _overwrite_continent_file(cfg) -> str:
    # After non_expanded_combine_data runs, the continent.json file is reduced
    # to only those that are needed. To simplify things we will just do it ourselves before running.
    # There are problems with intercontinental basins, but actually expanded_setfinder only
    # runs with a single continent of SWORD data anyway so it was already a problem.
    # Maybe SWORD groups all basins into single continents? Probably.

    reaches_path = cfg.dirs["input"] / "reaches_of_interest.json"
    continents_path = cfg.dirs["input"] / "continent.json"

    with open(reaches_path) as f:
        reaches = json.load(f)
    with open(continents_path) as f:
        continents = json.load(f)

    # set of first digits of the reach_ids. These indicate continents
    active_prefixes = set(int(str(r)[0]) for r in reaches)

    filtered_continents = []
    conts_to_run = []
    for cont in continents:
        prefixes = list(cont.values())[0]

        if any(p in active_prefixes for p in prefixes):
            filtered_continents.append(cont)
            conts_to_run.append(list(cont.keys())[0])

    # overwrite continent.json with only the needed continents
    with open(continents_path, "w") as f:
        json.dump(filtered_continents, f, indent=2)

    print(f"Per-continent modules will run on {conts_to_run}.")

    return len(filtered_continents)


def _get_optional_binds(cfg: Config, bind_cmd: str):
    binds = []
    if cfg.swot_input_bind_dir:
        binds.append(f"{bind_cmd} {cfg.swot_input_bind_dir}:/mnt/data/input/swot:ro")
    if cfg.priors_bind_dir:
        binds.append(f"{bind_cmd} {cfg.priors_bind_dir}:/mnt/data/input/sos:ro")
    if cfg.sword_bind_dir:
        binds.append(f"{bind_cmd} {cfg.sword_bind_dir}:/mnt/data/input/sword:ro")
    return binds


def _index_json(cfg: Config, module_name: str) -> str:
    """Index file a module's unit indices point into (used by cfl_unit to resolve unit ids)."""
    spec = get_spec(module_name, cfg.module_templates[module_name].module_args)
    return str(cfg.dirs["input"] / spec.index) if spec.index else ""


def _copy_cfl_lib(cfg: Config) -> Path:
    # Copied into the run so running jobs keep the version they were rendered with.
    dest = cfg.dirs["sh_scripts"] / "cfl_lib.sh"
    shutil.copyfile(CFL_LIB_SCRIPT, dest)
    return dest


def create_module_scripts(cfg: Config):
    env = Environment(loader=PackageLoader("confluence", "templates"), trim_blocks=True)
    cfl_lib = _copy_cfl_lib(cfg)
    monitor_dir = cfg.dirs["run"] / "monitor"

    # SBATCH header (slurm only) + apptainer + cfl_lib bookkeeping around the module command
    module_template_file = env.get_template("sbatch.sh.j2")

    platform_dict = _get_platform_dict(cfg.container_platform)
    optional_binds = _get_optional_binds(cfg, platform_dict["bind"])

    flpe_bind_dir = cfg.flpe_output_bind_dir if cfg.flpe_output_bind_dir else cfg.dirs["mnt"] / "flpe"

    print("\n\nWriting module scripts.")
    for module_name in cfg.modules_to_run:
        template_args = cfg.module_templates[module_name]

        # Module specific command
        command_template = env.get_template(template_args.j2_file)
        rendered_command = command_template.render(
            container_cmd=platform_dict,
            optional_binds=optional_binds,
            mnt_dir=cfg.dirs["mnt"],
            sif_dir=cfg.dirs["sif"],
            flpe_bind_dir=flpe_bind_dir,
            module_dir=cfg.dirs["modules"] / get_repo_name(module_name).lower(),
            sword_version=cfg.sword_version,
            module=template_args.module_args,
            run=cfg.run_name,
        )

        rendered_script = module_template_file.render(
            job_name=f"{module_name}_{cfg.run_name}_cfl",
            report_dir=cfg.dirs["report"],
            module_name=module_name,
            time_limit=template_args.time,
            mem_limit=template_args.mem,
            scheduler=cfg.scheduler,
            cpus=template_args.cpus,
            hpc=cfg.hpc,
            rendered_command=rendered_command,
            load_apptainer_script=LOAD_APPTAINER_SCRIPT,
            monitor_dir=monitor_dir,
            index_json=_index_json(cfg, module_name),
            cfl_lib_script=cfl_lib,
        )

        script_path = cfg.dirs["sh_scripts"] / f"{module_name}.sh"
        with open(script_path, "w") as file:
            file.write(rendered_script)


def module_plan(cfg: Config, n_continents: int) -> list[dict]:
    """How each module in modules_to_run is fanned out; shared by the slurm driver and the local runner.

    Continent / global modules are fixed-size arrays; every other module runs one unit per entry of
    its index file (counted at runtime, after upstream modules have written it).
    """
    default_chunks = cfg.local.reach_chunks if cfg.scheduler == "local" else cfg.hpc.reach_chunks
    modules = []
    for module in cfg.modules_to_run:
        entry = {"name": module, "script": f"{module}.sh"}
        if module in CONTINENT_MODULES or module in GLOBAL_MODULES:
            entry.update(kind="fixed", count=n_continents if module in CONTINENT_MODULES else 1)
        else:
            index_json = _index_json(cfg, module) or str(cfg.dirs["input"] / "reaches.json")
            gate_on = cfg.monitor.for_module(module, "gate") is not False
            canary = cfg.monitor.for_module(module, "canary_tasks") if gate_on else 0
            index_range = cfg.module_templates[module].reach_chunks or default_chunks
            entry.update(kind="reach", index_json=index_json, canary_tasks=canary, index_range=index_range)
        modules.append(entry)
    return modules


def create_slurm_driver(cfg: Config, n_continents: int):
    env = Environment(
        loader=PackageLoader("confluence", "templates"),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("slurm_driver.sh.j2")
    modules = module_plan(cfg, n_continents)

    rendered_script = template.render(
        run_name=cfg.run_name,
        hpc=cfg.hpc,
        dirs=cfg.dirs,
        max_reaches=cfg.max_reaches,
        modules=modules,
        monitor=cfg.monitor,
        monitor_dir=cfg.dirs["run"] / "monitor",
        python_exe=sys.executable,
        repo_root=REPO_ROOT,
    )

    out_path = cfg.dirs["sh_scripts"] / "slurm_driver.sh"
    with open(out_path, "w") as f:
        f.write(rendered_script)

    return out_path


def create_audit_script(cfg: Config) -> Path:
    env = Environment(loader=PackageLoader("confluence", "templates"), trim_blocks=True)
    rendered = env.get_template("audit.sh.j2").render(
        run_name=cfg.run_name,
        hpc=cfg.hpc,
        dirs=cfg.dirs,
        monitor_dir=cfg.dirs["run"] / "monitor",
        python_exe=sys.executable,
        repo_root=REPO_ROOT,
    )
    out_path = cfg.dirs["sh_scripts"] / "audit.sh"
    out_path.write_text(rendered)
    return out_path


def write_run_records(cfg: Config):
    """Provenance + RUN_GUIDE.md. Never fatal: the run must not depend on the monitor."""
    from confluence.monitor.context import RunContext
    from confluence.monitor.guide import write_guide
    from confluence.monitor.provenance import write_setup_provenance

    try:
        write_setup_provenance(cfg)
        print(f"Wrote {write_guide(RunContext(cfg.dirs['run']))}")
    except Exception as e:
        print(f"WARNING: could not write provenance / RUN_GUIDE.md: {type(e).__name__}: {e}")


def write_scripts(cfg: Config):
    (cfg.dirs["run"] / "monitor").mkdir(parents=True, exist_ok=True)
    create_module_scripts(cfg)

    # Both schedulers need continent.json reduced to the active continents
    # before any per-continent module is counted.
    n_continents = _overwrite_continent_file(cfg)

    # The local runner walks cfg.modules_to_run itself, so there is no driver.
    driver_path = None
    if cfg.scheduler == "slurm":
        create_audit_script(cfg)
        driver_path = create_slurm_driver(cfg, n_continents)
    write_run_records(cfg)

    return driver_path
