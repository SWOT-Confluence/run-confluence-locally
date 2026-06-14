import argparse
import subprocess as sp

from confluence.utils.config import Config
from confluence.utils.title import print_title
from confluence.utils.dir_structure import setup_dirs
from confluence.utils.module_images import setup_modules
from confluence.utils.scripts import write_scripts

"""
TODO
- args / entry points for continuing runs or reusing images
- update readme
    - check for anything referencing notebook based code
"""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path")
    args = parser.parse_args()

    print_title()

    cfg = Config.from_file(args.config_path)
    cfg = setup_dirs(cfg)
    setup_modules(cfg)
    driver_path = write_scripts(cfg)

    if cfg.submit_driver:
        sp.run(["sbatch", driver_path])
    else:
        print(f"slurm driver written to {driver_path}")


if __name__ == "__main__":
    main()
