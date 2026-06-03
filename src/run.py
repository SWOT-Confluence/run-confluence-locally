import os
import shutil
import argparse
from pathlib import Path

import yaml

from .utils.module_images import clone_repos, create_singularity_defs, create_sifs

def setup_dirs(cfg: dict):
    run_dir = Path(cfg['root_dir']).resolve() / f"confluence_{cfg['run_name']}"

    if cfg['overwrite_run'] and run_dir.is_dir():
        shutil.rmtree(run_dir)
        
    mnt_dir = run_dir / f"{cfg['run_name']}_mnt"
    
    shutil.copytree(cfg['empty_dir'], run_dir, symlinks=True)
    os.rename(run_dir / "empty_mnt", mnt_dir)

    shutil.copy2(cfg['roi_file'], mnt_dir / 'input' / 'reaches_of_interest.json')

    cfg['run_dir'] = run_dir
    cfg['mnt_dir'] = mnt_dir

    module_dir = cfg['run_dir'] / 'modules'
    module_dir.mkdir(exist_ok=True)
    cfg['module_dir'] = module_dir

    sif_dir = cfg['run_dir'] / 'sif'
    sif_dir.mkdir(exist_ok=True)
    cfg['sif_dir'] = sif_dir
    
    return cfg


def setup_modules(cfg: dict):
    modules = cfg['modules_to_run']

    clone_repos(
        cfg['github_username'], 
        cfg['run_dir'] / 'modules', 
        modules,
    )


    for mod in modules.keys():
        dockerfile = Path(cfg['module_dir']) /  mod / 'Dockerfile'
        if dockerfile.exists():
            print(f"[{mod}]")
            with open(dockerfile) as f:
                lines = f.readlines()

                for line in lines:
                    if line.strip().startswith(('COPY', 'ENTRYPOINT', 'FROM')):
                        print(line.strip())
            print()
        else:
            print(f'{mod} -> (Dockerfile x)')
            print()

    for module in modules.keys():
        create_singularity_defs(module, cfg['module_dir'], cfg['run_dir'] / 'sif')

    create_sifs(modules.keys(), cfg['sif_dir'], cfg['module_dir'])



def main(config_path):
    config_path = Path(config_path).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    # print(cfg)

    cfg = setup_dirs(cfg)
    setup_modules(cfg)

    


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path")
    args = parser.parse_args()

    main(args.config_path)

    print("Done")
