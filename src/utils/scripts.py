import os
import shutil
import subprocess as sp
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# from config import Config



# def build_sifs(cfg: dict, run_dir: Path, included_modules: list[str]):
#     built_images = set()

#     for module_name in included_modules:
#         if module_name not in cfg.get("modules", {}):
#             raise KeyError(f"Module '{module_name}' is not defined in the configuration file.")
            
#         module_cfg = cfg["modules"][module_name]

#         image_name = (
#             module_name.replace("expanded_", "")
#             .replace("non_", "")
#             .replace("unconstrained_", "")
#             .replace("constrained_", "")
#         )
        
#         if image_name not in built_images:
#             sp.run(
#                 [
#                     "singularity",
#                     "build",
#                     "-F",
#                     dirs["sif"] / f"{image_name}.simg",
#                     f"docker://{cfg['docker']['username']}/{image_name}:{cfg['docker']['tag']}",
#                 ],
#                 check=True
#             )
#             built_images.add(image_name)


def create_slurm_scripts(
    cfg: dict,
):
    env = Environment(loader=FileSystemLoader(str(template_dir)), trim_blocks=True)

    module_template = env.get_template("sbatch.sh.j2")
    skip_flag = "-k" if cfg['continue_downloads'] else ""

    for module_name in included_modules:
        if module_name not in cfg.modules:
            raise KeyError(f"Module '{module_name}' is not defined in the cfguration file.")
            
        module_cfg = cfg.modules[module_name]
        time_limit = module_cfg.get("time", "00:20:00")
        mem_limit = module_cfg.get("mem", "4G")

        # Explicit template path resolution from cfg
        command_template = env.get_template(module_cfg["template"])
        rendered_command = command_template.render(
            mnt_dir=mnt_dir,
            sif_dir=dirs["sif"],
            exp=cfg["experiment"],
            files=cfg["files"],
            module=module_cfg,
            skip_flag=skip_flag,
            run=run
        )

        rendered_script = module_template.render(
            job_name=f"{module_name}_{run}_cfl",
            report_dir=dirs["report"],
            module_name=module_name,
            time_limit=time_limit,
            mem_limit=mem_limit,
            hpc=cfg["hpc"],
            rendered_command=rendered_command
        )

        script_path = dirs["sh"] / f"{module_name}.sh"
        with open(script_path, "w") as file:
            file.write(rendered_script)


def generate_slurm_driver(
    cfg: Config
        
    # template_dir: str | Path,
    # output_filepath: str | Path,
    # job_name: str,
    # output_log_dir: str,
    # partition: str,
    # time_limit: str,
    # nodes: int,
    # ntasks: int,
    # cpus_per_task: int,
    # mem: str,
    # run: str,
    # directory: str,
    # reach_chunks: int,
    # json_file: str,
    # expanded_json_file: str,
    # reach_json_file: str,
    # basin_json_file: str,
    # metroman_json_file: str,
    # batch_size: int,
    # concurrent_jobs: int,
    # script_jobs: dict[str, int],
    # scripts: list[str],
    # max_reaches: int | None = None,
):
    env = Environment(
        loader=FileSystemLoader(str(template_dir)), 
        trim_blocks=True, 
        lstrip_blocks=True
    )
    template = env.get_template('templates/driver_template.sh.j2')

    rendered_script = template.render(
        job_name=job_name,
        output_log_dir=output_log_dir,
        partition=partition,
        time_limit=time_limit,
        nodes=nodes,
        ntasks=ntasks,
        cpus_per_task=cpus_per_task,
        mem=mem,
        run=run,
        directory=directory,
        reach_chunks=reach_chunks,
        json_file=json_file,
        expanded_json_file=expanded_json_file,
        reach_json_file=reach_json_file,
        basin_json_file=basin_json_file,
        metroman_json_file=metroman_json_file,
        batch_size=batch_size,
        concurrent_jobs=concurrent_jobs,
        script_jobs=script_jobs,
        scripts=scripts,
        max_reaches=max_reaches,
        dry_run=dry_run
    )

    output_path = Path(output_filepath)
    output_path.parent.mkdir(exist_ok=True, parents=True)
    
    with open(output_path, 'w') as f:
        f.write(rendered_script)
