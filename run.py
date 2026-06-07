import argparse
import subprocess as sp
from pathlib import Path

import yaml

from confluence.utils.config import Config
from confluence.utils.dir_structure import setup_dirs
from confluence.utils.module_images import clone_repos, create_defs, create_sifs
from confluence.utils.scripts import create_slurm_scripts, create_slurm_driver


"""
TODO
- convert config to pydantic object for explicit rules and typing
    - move 'xxx_dir' keys into a cfg.dir dictionary for cleaner passing
- args / entry points for continuing runs or reusing images
    - symlinks to data or images? 
    - should all data be located within the root (even if symlinked), or can 
    we bind it into the right place on the mnt? That would be the most slick,
    but maybe not leave as much of a record. 
- Check downloaded/copied sword version matches the sword_version key.
    - pass 'sword_version' down into the validation module for reach_id_vXX
    - check 17 vs 17b/c etc.
- update readme
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
    to_run = cfg.modules_to_run
    # set to removed duplicates (i.e. expanded and non-expanded setfinder)
    stripped_modules = set([strip_modifiers(module) for module in to_run])

    if cfg.clone_repos:
        clone_repos(
            stripped_modules,
            cfg.default_github_username,
            cfg.default_repository_branch,
            cfg.module_branches,
            cfg.dirs["run"] / "modules",    
        )
    else:
        print("Using existing module files.")

    if not cfg.build_modules:
        print("Skipping module rebuild.")
        return

    for mod in stripped_modules:
        dockerfile = cfg.dirs["modules"] / mod / "Dockerfile"
        if dockerfile.exists():
            print(f"[{mod}]")
            with open(dockerfile) as f:
                lines = f.readlines()

                for line in lines:
                    if line.strip().startswith(("COPY", "ENTRYPOINT", "FROM")):
                        print(line.strip())
            print()
        else:
            print(f"{mod} -> (Dockerfile x)")
            print()

    for module in stripped_modules:
        create_defs(module, cfg.dirs["modules"], cfg.default_image_release_tag)

    create_sifs(stripped_modules, cfg.dirs["sif"], cfg.dirs["modules"])


def main(config_path):
    cfg = Config.from_file(config_path)
    cfg = setup_dirs(cfg)
    setup_modules(cfg)
    create_slurm_scripts(cfg)
    driver_path = create_slurm_driver(cfg)

    if cfg.submit_driver:
        sp.run(f"sbatch {driver_path}")
    else:
        print(f"slurm driver written to {driver_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path")
    # parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    main(args.config_path)

    print("Done")
