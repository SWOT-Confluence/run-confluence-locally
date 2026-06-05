from importlib import resources

from jinja2 import Environment, PackageLoader


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


def create_slurm_scripts(cfg: dict):
    env = Environment(loader=PackageLoader("confluence", "templates"), trim_blocks=True)

    module_template = env.get_template("sbatch.sh.j2")

    for module_name in cfg["modules_to_run"]:
        module_args = cfg["module_template_args"][module_name]
        # time and memory are now optional
        time_limit = module_args.get("time", "6:00:00")
        mem_limit = module_args.get("mem", "4G")

        # Explicit template path resolution from cfg
        command_template = env.get_template(module_args["template"])
        rendered_command = command_template.render(
            mnt_dir=cfg["dirs"]["mnt"],
            sif_dir=cfg["dirs"]["sif"],
            sword_version=cfg["sword_version"],
            module=module_args,
            run=cfg["run_name"],
        )

        rendered_script = module_template.render(
            job_name=f"{module_name}_{cfg['run_name']}_cfl",
            report_dir=cfg["dirs"]["report"],
            module_name=module_name,
            time_limit=time_limit,
            mem_limit=mem_limit,
            hpc=cfg["hpc"],
            rendered_command=rendered_command,
        )

        script_path = cfg["dirs"]["sh_scripts"] / f"{module_name}.sh"
        with open(script_path, "w") as file:
            file.write(rendered_script)
        print(f"{module_name} script written.")


def create_slurm_driver(cfg: dict):
    env = Environment(
        loader=PackageLoader("confluence", "templates"),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("driver_template.sh.j2")

    # build scripts list (same order as INCLUDED_MODULES)
    scripts = [f"{module}.sh" for module in cfg["modules_to_run"]]

    # build script_jobs dict that includes counts for hardcoded modules.
    script_jobs = {}
    for module in cfg["modules_to_run"]:
        script_name = f"{module}.sh"

        if module in HARDCODED_JOBS.keys():
            # Use hardcoded job count
            script_jobs[script_name] = HARDCODED_JOBS[module]

    rendered_script = template.render(
        run_name=cfg["run_name"],
        hpc=cfg["hpc"],
        log_dir=cfg["dirs"]["log"],
        run_dir=cfg["dirs"]["run"],
        input_dir=cfg["dirs"]["mnt"] / "input",
        sh_directory=cfg["dirs"]["sh_scripts"],
        scripts=scripts,
        script_jobs=script_jobs,
        max_reaches=cfg.get("max_reaches", 0),
    )

    out_path = cfg["dirs"]["sh_scripts"] / "slurm_driver.sh"
    with open(out_path, "w") as f:
        f.write(rendered_script)

    return out_path
