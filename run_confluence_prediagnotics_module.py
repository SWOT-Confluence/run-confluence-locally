
import subprocess as sp
from multiprocessing import Pool
import os
from pathlib import Path
import sys
import json
from itertools import repeat


# Docker command for the selected module

def run_docker(args):
    index, json_path = args
    run_dir = json_path.parent
    print(f"Running command for index {index}")
    #command = f'docker run -v {run_dir}:/mnt/data/input -v {run_dir}/prediagnostics:/mnt/data/output -v /home/shared/Utils/SWORD/SWORD_v16_netcdf/netcdf/:/home/shared/Utils/SWORD/SWORD_v16_netcdf/netcdf/ ccazals/prediagnostics:latest -r {json_path.name} -i {index}'
    command = f'docker run -v {run_dir}:/mnt/data/input -v {run_dir}/prediagnostics:/mnt/data/output -v /Users/elisafriedmann/Documents/confluence/confluence_test/test_mnt/input/sword:/home/shared/Utils/SWORD/SWORD_v16_netcdf/netcdf/ ccazals/prediagnostics:latest -r {json_path.name} -i {index}'
    print(command)
    try:
        sp.run(command, shell=True, check=True)
        return (index, True)
    except sp.CalledProcessError as e:
        print(f"Index {index} failed with return code {e.returncode}")
        return (index, False)

if __name__ == '__main__':
    if len(sys.argv)>1:
        json_path = Path(sys.argv[1])
        with open(json_path) as jsonfile:
            print(f'Open file {json_path}')
            json_data = json.load(jsonfile)
            # Handle both list of dicts and simple list of strings
            if isinstance(json_data, list) and len(json_data) > 0:
                if isinstance(json_data[0], dict):
                    nb_reaches = len([r['reach_id'] for r in json_data])
                else:
                    nb_reaches = len(json_data)
            else:
                nb_reaches = 0
        #indices = list(range(0, 10))
        indices = list(range(0, nb_reaches))
        total_cores = os.cpu_count() or 2  # fallback in case os.cpu_count() returns None
        max_workers = max(1, total_cores // 2)  # use at least 1 worker
        if not json_path.parent.joinpath("prediagnostics").exists():
            os.mkdir(json_path.parent.joinpath("prediagnostics"))

        print(f"Using {max_workers} out of {total_cores} available cores for parallel processing.")
        print(f"{nb_reaches} reaches to run for json {json_path}")

        with Pool(processes=max_workers) as pool:
            args_list = list(zip(indices, repeat(json_path)))
            results = pool.map(run_docker, args_list)

        for idx, success in results:
            if success:
                print(f"Index {idx} succeeded.")
            else:
                print(f"Index {idx} failed.")
    else :
        print('No json provided ... skip')
