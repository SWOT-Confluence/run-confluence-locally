import argparse
import subprocess as sp

from confluence.utils.config import Config
from confluence.utils.dir_structure import setup_dirs
from confluence.utils.local_runner import run_local
from confluence.utils.module_images import setup_modules
from confluence.utils.scripts import write_scripts
from confluence.utils.title import print_title

"""
TODO
- update readme
    - check for anything referencing notebook based code
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Number of apptainer builds to run concurrently. Default auto-detects: full "
        "parallelism inside a Slurm allocation, throttled to 2 outside of Slurm",
    )
    args = parser.parse_args()

    print_title()

    cfg = Config.from_file(args.config_path)
    cfg = setup_dirs(cfg)
    setup_modules(cfg, build_max_workers=args.max_workers)
    driver_path = write_scripts(cfg)

    if cfg.scheduler == "local":
        if cfg.submit_driver:
            run_local(cfg)
        else:
            print(f"module scripts written to {cfg.dirs['sh_scripts']}")
    elif cfg.submit_driver:
        sp.run(["sbatch", driver_path])
    else:
        print(f"slurm driver written to {driver_path}")


if __name__ == "__main__":
    main()
