import argparse
import subprocess as sp

from confluence.utils.config import Config
from confluence.utils.dir_structure import setup_dirs
from confluence.utils.module_images import clone_repos, create_defs, create_sifs
from confluence.utils.scripts import create_slurm_scripts, create_slurm_driver


"""
TODO
- args / entry points for continuing runs or reusing images
    - symlinks to data or images? 
    - should all data be located within the root (even if symlinked), or can 
    we bind it into the right place on the mnt? That would be the most slick,
    but maybe not leave as much of a record. 
- update readme
    - example usage of new code (end to end and entry points)
    - check for anything referencing notebook based code
    - 
"""

def strip_modifiers(name: str):
    modifiers = [
        "non_expanded_",
        "expanded_",
        "unconstrained_",
        "constrained_",
        "unconstrained_",
        "constrained_",
    ]
    stripped = name
    for mod in modifiers:
        stripped = stripped.replace(mod, "")

    return stripped


def setup_modules(cfg: dict):
    # set to removed duplicates after removing modifiers (e.g. expanded and non_expanded setfinder) 
    stripped_modules = set([strip_modifiers(module) for module in cfg.modules_to_run])

    if cfg.clone_repos:
        clone_repos(
            stripped_modules,
            cfg.default_github_username,
            cfg.default_repository_branch,
            cfg.module_branches,
            cfg.dirs["run"] / "modules",
        )
    else:
        print("Using existing repository files.")

    if not cfg.build_modules:
        print("Skipping module rebuild.")
        return

    create_defs(stripped_modules, cfg.dirs["modules"], cfg.default_image_release_tag)

    create_sifs(
        stripped_modules, cfg.container_platform, cfg.dirs["sif"], cfg.dirs["modules"]
    )


def main(config_path):
    cfg = Config.from_file(config_path)
    cfg = setup_dirs(cfg)
    setup_modules(cfg)
    create_slurm_scripts(cfg)
    driver_path = create_slurm_driver(cfg)

    if cfg.submit_driver:
        sp.run(["sbatch", driver_path])
    else:
        print(f"slurm driver written to {driver_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path")
    # parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    main(args.config_path)

    print("Done")
