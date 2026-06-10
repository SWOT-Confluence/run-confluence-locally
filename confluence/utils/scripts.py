from importlib import resources

from jinja2 import Environment, PackageLoader

from confluence.utils.config import Config

TEMPLATES_PATH = resources.files("confluence.templates")

# Define which modules have special (hardcoded) job counts
HARDCODED_JOBS = {
    "expanded_setfinder": "6",
    "expanded_combine_data": "1",
    "non_expanded_setfinder": "6",
    "non_expanded_combine_data": "1",
    "unconstrained_priors": "6",
    "constrained_priors": "6",
    "metroman_consolidation": "6",
    "lakeflow_input": "1",
    "output": "6",
}


def _get_platform_dict(platform: str):
    match platform:
        case "apptainer":
            bind_cmd = "--bind"
        case "docker":
            bind_cmd = "-v"
        case _:
            raise ValueError(f"{platform = } has not been implemented.")
    return {"run": platform, "bind": bind_cmd}


def create_slurm_scripts(cfg: Config):
    env = Environment(loader=PackageLoader("confluence", "templates"), trim_blocks=True)

    # SBATCH header commands
    module_template_file = env.get_template("sbatch.sh.j2")

    platform_dict = _get_platform_dict(cfg.container_platform)

    for module_name in cfg.modules_to_run:
        template_args = cfg.module_templates[module_name]

        # Module specific command
        command_template = env.get_template(template_args.j2_file)
        rendered_command = command_template.render(
            container_cmd=platform_dict,
            mnt_dir=cfg.dirs["mnt"],
            sif_dir=cfg.dirs["sif"],
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
        print(f"{module_name} script written.")


def create_slurm_driver(cfg: Config):
    env = Environment(
        loader=PackageLoader("confluence", "templates"),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("slurm_driver.sh.j2")

    # build scripts list in order of modules_to_run list
    scripts = [f"{module}.sh" for module in cfg.modules_to_run]

    # build script_jobs dict that includes counts for hardcoded modules.
    script_jobs = {}
    for module in cfg.modules_to_run:
        script_name = f"{module}.sh"

        if module in HARDCODED_JOBS.keys():
            # Use hardcoded job count
            script_jobs[script_name] = HARDCODED_JOBS[module]

    rendered_script = template.render(
        run_name=cfg.run_name,
        hpc=cfg.hpc,
        dirs=cfg.dirs,
        max_reaches=cfg.max_reaches,
        scripts=scripts,
        script_jobs=script_jobs,
    )

    out_path = cfg.dirs["sh_scripts"] / "slurm_driver.sh"
    with open(out_path, "w") as f:
        f.write(rendered_script)

    return out_path
