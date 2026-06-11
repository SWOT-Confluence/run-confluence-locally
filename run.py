import argparse
import subprocess as sp

from confluence.utils.config import Config
from confluence.utils.dir_structure import setup_dirs
from confluence.utils.module_images import setup_modules
from confluence.utils.scripts import write_scripts


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


def main(config_path):
    cfg = Config.from_file(config_path)

    cfg = setup_dirs(cfg)
    setup_modules(cfg)
    driver_path = write_scripts(cfg)

    if cfg.submit_driver:
        sp.run(["sbatch", driver_path])
    else:
        print(f"slurm driver written to {driver_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path")
    args = parser.parse_args()

    main(args.config_path)
