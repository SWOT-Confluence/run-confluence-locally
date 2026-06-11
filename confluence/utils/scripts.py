import json
from importlib import resources

from jinja2 import Environment, PackageLoader

from confluence.utils.config import Config
from confluence.utils.module_names import get_repo_name

TEMPLATES_PATH = resources.files("confluence.templates")

# Define which modules have special job counts.
# These run 1 job per SWORD continent file.
CONTINENT_MODULES = [
    "expanded_setfinder",
    "non_expanded_setfinder",
    "unconstrained_priors",
    "constrained_priors",
    "metroman_consolidation",
    "output",
]
# These always run just once.
GLOBAL_MODULES = [
    "expanded_combine_data",
    "non_expanded_combine_data",
    "lakeflow_input",
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


def _get_continent_indices(cfg) -> str:
    # Identifies the indices of continents that need to be run.

    reaches_path = cfg.dirs["mnt"] / "input" / "reaches_of_interest.json"
    continents_path = cfg.dirs["mnt"] / "input" / "continent.json"

    with open(reaches_path) as f:
        reaches = json.load(f)
    with open(continents_path) as f:
        continents = json.load(f)

    active_prefixes = set(int(str(r)[0]) for r in reaches)

    active_indices = []
    conts_to_run = []
    for i, cont in enumerate(continents):
        prefixes = list(cont.values())[0]
        if any(p in active_prefixes for p in prefixes):
            active_indices.append(str(i))
            conts_to_run.append(list(cont.keys())[0])

    if active_indices:
        print(f"Per-continent modules will run on {conts_to_run}.")
        return ",".join(active_indices)
    else:
        raise RuntimeError("Could not resolve continents to run on from reaches_of_interest.json")


def _get_optional_binds(cfg: Config, bind_cmd: str):
    # Some modules look in different places for input data so we just set up two binds here instead
    # of making module-specific binds. The other way to do it would be to pass each source path
    # into each module template and define where they look for each data source data. This is maybe
    # lazy but easier than maintaining each module's binds. Lazy by definition actually- but I don't
    # see any major downsides or conflicts arising.
    binds = []
    if cfg.swot_input_bind_dir:
        binds.append(f"{bind_cmd} {cfg.swot_input_bind_dir}:/mnt/data/input/swot:ro")
    if cfg.priors_bind_dir:
        binds.append(f"{bind_cmd} {cfg.priors_bind_dir}:/data/sos:ro")
        binds.append(f"{bind_cmd} {cfg.priors_bind_dir}:/mnt/data/input/sos:ro")
    if cfg.sword_bind_dir:
        binds.append(f"{bind_cmd} {cfg.sword_bind_dir}:/data/sword:ro")
        binds.append(f"{bind_cmd} {cfg.sword_bind_dir}:/mnt/data/input/sword:ro")
    return binds


def create_module_scripts(cfg: Config):
    env = Environment(loader=PackageLoader("confluence", "templates"), trim_blocks=True)

    # SBATCH header commands
    module_template_file = env.get_template("sbatch.sh.j2")
    # TODO: truly local runs (not HPC) would want to remove this header? Would also need
    # to handle the indexing that relies on job arrays in the driver script...

    platform_dict = _get_platform_dict(cfg.container_platform)
    optional_binds = _get_optional_binds(cfg, platform_dict["bind"])

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
            module_dir=cfg.dirs["modules"] / get_repo_name(module_name),
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
            hpc=cfg.hpc,
            rendered_command=rendered_command,
        )

        script_path = cfg.dirs["sh_scripts"] / f"{module_name}.sh"
        with open(script_path, "w") as file:
            file.write(rendered_script)


def create_slurm_driver(cfg: Config):
    env = Environment(
        loader=PackageLoader("confluence", "templates"),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("slurm_driver.sh.j2")

    # build scripts list in order of modules_to_run list
    scripts = [f"{module}.sh" for module in cfg.modules_to_run]

    continent_indices_str = _get_continent_indices(cfg)

    # build script_jobs dict that includes counts for hardcoded modules.
    script_arrays = {}
    for module in cfg.modules_to_run:
        script_name = f"{module}.sh"

        if module in CONTINENT_MODULES:
            script_arrays[script_name] = continent_indices_str
        elif module in GLOBAL_MODULES:
            script_arrays[script_name] = "0"

    rendered_script = template.render(
        run_name=cfg.run_name,
        hpc=cfg.hpc,
        dirs=cfg.dirs,
        max_reaches=cfg.max_reaches,
        scripts=scripts,
        script_arrays=script_arrays,
    )

    out_path = cfg.dirs["sh_scripts"] / "slurm_driver.sh"
    with open(out_path, "w") as f:
        f.write(rendered_script)

    return out_path


def write_scripts(cfg: Config):
    create_module_scripts(cfg)
    driver_path = create_slurm_driver(cfg)

    return driver_path
