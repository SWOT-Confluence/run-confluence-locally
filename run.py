import os
import shutil
import argparse
import subprocess as sp
from pathlib import Path

import yaml

from confluence.utils.module_images import clone_repos, create_defs, create_sifs
from confluence.utils.scripts import create_slurm_scripts, create_slurm_driver


"""
TODO
- convert config to pydantic object for explicit rules and typing
    - move 'xxx_dir' keys into a cfg.dir dictionary for cleaner passing
- args / entry points for continuing runs or reusing images
- pull all input data from sources
    - SWORD (https://zenodo.org/records/10013982)
    - priors (https://zenodo.org/records/20541765)
    - SVS (ask MJ and Peyman)
- readme

"""


def setup_dirs(cfg: dict):
    run_dir = Path(cfg["root_dir"]).resolve() / f"confluence_{cfg['run_name']}"
    mnt_dir = run_dir / f"{cfg['run_name']}_mnt"

    if cfg["overwrite_run"] and run_dir.is_dir():
        print("Removing existing directory before running")
        shutil.rmtree(run_dir)

    if not run_dir.is_dir():
        print("Copying empty directory (may take a minute)")
        shutil.copytree(cfg["empty_dir"], run_dir, symlinks=True)
        os.rename(run_dir / "empty_mnt", mnt_dir)
        shutil.copy2(cfg["roi_file"], mnt_dir / "input" / "reaches_of_interest.json")
    else:
        print("Continuing with existing directory")

    cfg["run_dir"] = run_dir
    cfg["mnt_dir"] = mnt_dir

    # These should already exist from copying the empty dir, but defining their
    # paths in the config dict is useful later.
    dir_stems = ["modules", "sif", "report", "log", "sh_scripts"]
    for stem in dir_stems:
        full_dir = cfg["run_dir"] / stem
        full_dir.mkdir(exist_ok=True)
        cfg[f"{stem}_dir"] = full_dir

    out_path = run_dir / "config.yml"
    with open(out_path, "w") as outfile:
        yaml.dump(cfg, outfile)

    return cfg


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
    to_run = cfg["modules_to_run"]
    # set to removed duplicates (i.e. expanded and non-expanded setfinder)
    stripped_modules = set([strip_modifiers(module) for module in to_run])

    if cfg["clone_repos"]:
        clone_repos(
            stripped_modules,
            cfg["default_github_username"],
            cfg["run_dir"] / "modules",
            cfg.get("module_branches", {}),
            cfg.get("default_branch", "main"),
        )
    else:
        print("Using existing module files.")

    if not cfg["rebuild_modules"]:
        print("Skipping module rebuild.")
        return

    for mod in stripped_modules:
        dockerfile = Path(cfg["modules_dir"]) / mod / "Dockerfile"
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
        create_defs(module, cfg["modules_dir"], cfg["default_image_release"])

    create_sifs(stripped_modules, cfg["sif_dir"], cfg["modules_dir"])


def main(config_path):
    config_path = Path(config_path).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    cfg = setup_dirs(cfg)
    setup_modules(cfg)
    create_slurm_scripts(cfg)
    driver_path = create_slurm_driver(cfg)

    if cfg['submit_driver']:
        sp.run(driver_path)
    else:
        print(f"slurm driver written to {driver_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path")
    # parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    main(args.config_path)

    print("Done")
