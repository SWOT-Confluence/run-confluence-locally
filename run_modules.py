import os
import shutil
import subprocess as sp
from pathlib import Path


def _validate_dir(dir: str | Path):
    if isinstance(dir, str):
        dir = Path(dir)
    elif not isinstance(dir, Path):
        raise TypeError(
            f"Argument repo_dir must be a Path or str object. {type(dir) = }."
        )

    return dir


def clone_repos(
    github_name: str,
    repo_dir: str | Path,
    repo_names: list[str],
    name_map: dict[str:str],
    branch: str | dict[str:str] = "main",
):
    """Clone repositories with specified branch.

    Parameters
    ----------
    github_name : str
        GitHub username or organization name
    repo_dir : Path
        Directory to clone repos into
    repo_names : list
        List of repository names to clone
    name_map: dict[str: str]
        Shorthand names for modules
    branch : str or dict, optional
        Branch name to clone. Can be:
        - A string: same branch for all repos (default: 'main')
        - A dict: mapping repo name to specific branch
    """
    repo_dir = _validate_dir(repo_dir)

    for name in repo_names:
        path = repo_dir / name
        repo_name = name_map.get(name, name)
        url = f"https://github.com/{github_name}/{repo_name}.git"

        # Determine which branch to use
        if isinstance(branch, dict):
            branch_name = branch.get(name, "main")
        else:
            branch_name = branch

        if path.is_dir():
            print(f"[Remove] Deleting existing {name} to overwrite...")
            try:
                shutil.rmtree(path)  # rm -rf
            except OSError as e:
                print(f"Error: {path} : {e.strerror}")

        print(f"[Clone] Cloning {name} from branch {branch_name}...")
        sp.run(["git", "clone", "--branch", branch_name, url, name], cwd=repo_dir)


def build_and_push_images(
    repo_dir: str | Path,
    modules_to_run: list,
    docker_username: str,
    push: bool = True,
    custom_tag_name: str = "latest",
    local_arch: bool = True,
):
    repo_dir = _validate_dir(repo_dir)

    for a_repo_name in modules_to_run:
        repo_path = repo_dir / a_repo_name
        docker_path = f"{docker_username}/{a_repo_name}:{custom_tag_name}"

        # If local_arch is True, omit --platform to use host arch
        platform = [] if local_arch else ["--platform", "linux/amd64"]

        # buildx requires --load to make the image visible to 'docker images'
        # if not pushing to a remote registry.
        output_flag = "--push" if push else "--load"

        print(f"Building {a_repo_name}")
        print(f"\t{repo_path = }")
        print(f"\t{docker_path = }")

        build_cmd = [
            "docker",
            "buildx",
            "build",
            *platform,
            # "--no-cache",
            "--quiet",
            "-t",
            docker_path,
            output_flag,
            repo_path,
        ]

        # Filter out empty strings if push is False
        build_cmd = [arg for arg in build_cmd if arg]

        try:
            # check=True is necessary to catch non-zero exit codes in the try block
            sp.run(build_cmd, check=True)
        except sp.CalledProcessError as e:
            raise RuntimeError(
                f"Buildx failed for {a_repo_name}.\n"
                f"Command: {' '.join(build_cmd)}\n"
                f"Exit Status: {e.returncode}"
            )


def build_sifs_and_create_slurm_scripts(
    run_list: list[str],
    included_modules: list[str],
    base_dir: str | Path,
    docker_username: str,
    build: bool,
    custom_tag_name: str,
    continue_downloads: bool,
):
    base_dir = _validate_dir(base_dir)

    for run in run_list:
        # Fail safe directory creation
        # Has to exist with 'mnt' structure (Do it exister avec la structure 'mnt')
        run_dir = base_dir / f"confluence_{run}"
        mnt_dir = run_dir / f"{run}_mnt"

        # Create the sh_scripts directory (Cree le repertoire sh_scripts)
        sh_dir = run_dir / "sh_scripts"
        sh_dir.mkdir(exist_ok=True)

        # Create the sif directory (Cree la repertoire sif)
        sif_dir = run_dir / "sif"
        sif_dir.mkdir(exist_ok=True)

        # Create the report directory (Cree la repertoire report)
        report_dir = run_dir / "report"
        report_dir.mkdir(exist_ok=True)

        # Create batchs script details
        submission_prefix = "#SBATCH"

        job_details = {
            "partition": "ceewater_cjgleason-cpu",
            "nodes": "1",
            "cpus-per-task": "1",
            "job-name": f"{run}_cfl",
            "account": "pi_cjgleason_umass_edu"
        }

        run_data = f"singularity run --bind {mnt_dir}/input:/data"
        run_input = f"singularity run --bind {mnt_dir}/input:/mnt/data/input"
        skip_flag = " -k" if continue_downloads else ""

        # fmt: off
        command_dict = {
            "expanded_setfinder": f"{run_data} {sif_dir/'setfinder.simg'} -r reaches_of_interest.json -c continent.json -e -s 17b -o /data -n /data -a MetroMan HiVDI SIC -i ${{SLURM_ARRAY_TASK_ID}}",
            "expanded_combine_data": f"{run_data} {sif_dir/'combine_data.simg'} -d /data -e -s 17b",
            "input": f"GLOBAL_INDEX=$(( ${{OFFSET:-0}} + SLURM_ARRAY_TASK_ID * ${{INDEX_RANGE:-1}}))\n\nsingularity run --bind {mnt_dir}/input:/mnt/data {sif_dir/'input.simg'} -v 17b -r /mnt/data/expanded_reaches_of_interest.json -c SWOT_L2_HR_RiverSP_D -i ${{GLOBAL_INDEX}} -a ${{INDEX_RANGE:-1}}{skip_flag}",
            "non_expanded_setfinder": f"{run_data} {sif_dir/'setfinder.simg'} -c continent.json -s 17b -o /data -n /data -a MetroMan HiVDI SIC -i ${{SLURM_ARRAY_TASK_ID}}",
            "non_expanded_combine_data": f"{run_data} {sif_dir/'combine_data.simg'} -d /data -s 17b",
            "prediagnostics": f"GLOBAL_INDEX=$(( ${{OFFSET:-0}} + SLURM_ARRAY_TASK_ID ))\n\n{run_input},{mnt_dir}/diagnostics/prediagnostics:/mnt/data/output {sif_dir/'prediagnostics.sim'} -i ${{GLOBAL_INDEX}} -r reaches.json",
            "constrained_priors": f"singularity run -c --writable-tmpfs --bind {mnt_dir}/input:/mnt/data {sif_dir / 'priors.simg'} -i ${{SLURM_ARRAY_TASK_ID}} -r constrained -p usgs riggs -g -s local",
            "unconstrained_priors": f"singularity run -c --writable-tmpfs --bind {mnt_dir}/input:/mnt/data {sif_dir / 'priors.simg'} -i ${{SLURM_ARRAY_TASK_ID}} -r unconstrained -p usgs riggs -g -s local",
            "hivdi": f"{run_input},{mnt_dir}/flpe/hivdi:/mnt/data/flpe/hivdi {sif_dir/'hivdi.simg'} /mnt/data/input/reaches.json --input-dir /mnt/data/input -i ${{SLURM_ARRAY_TASK_ID}}",
            "sic4dvar": f"GLOBAL_INDEX=$(( ${{OFFSET:-0}} + SLURM_ARRAY_TASK_ID ))\n\n{run_input},{mnt_dir}/flpe/sic4dvar:/mnt/data/output,{mnt_dir}/logs:/mnt/data/logs {sif_dir/'sic4dvar.simg'} -r reaches.json --index ${{GLOBAL_INDEX}}",
            "metroman": f"GLOBAL_INDEX=$(( ${{OFFSET:-0}} + SLURM_ARRAY_TASK_ID ))\n\nsingularity run --env AWS_BATCH_JOB_ID='foo' --bind {mnt_dir}/input:/mnt/data/input,{mnt_dir}/flpe/metroman:/mnt/data/output {sif_dir/'metroman.simg'} -i ${{GLOBAL_INDEX}} -r metrosets.json -s local -v",
            "metroman_consolidation": f"GLOBAL_INDEX=$(( ${{OFFSET:-0}} + SLURM_ARRAY_TASK_ID ))\n\n{run_input},{mnt_dir}/flpe/metroman:/mnt/data/flpe {sif_dir/'metroman_consolidation.simg'} -i ${{GLOBAL_INDEX}}",
            "unconstrained_momma": f"GLOBAL_INDEX=$(( ${{OFFSET:-0}} + SLURM_ARRAY_TASK_ID ))\n\n{run_input},{mnt_dir}/flpe/momma:/mnt/data/output {sif_dir/'momma.simg'} -r reaches.json -m 3 -i ${{GLOBAL_INDEX}}",
            "constrained_momma": f"GLOBAL_INDEX=$(( ${{OFFSET:-0}} + SLURM_ARRAY_TASK_ID ))\n\n{run_input},{mnt_dir}/flpe/momma:/mnt/data/output {sif_dir/'momma.simg'} -r reaches.json -m 3 -c -i ${{GLOBAL_INDEX}}",
            "sad": f"GLOBAL_INDEX=$(( ${{OFFSET:-0}} + SLURM_ARRAY_TASK_ID ))\n\n{run_input},{mnt_dir}/flpe/sad:/mnt/data/output {sif_dir/'sad.simg'} --reachfile reaches.json --index ${{GLOBAL_INDEX}}",
            "moi": f"GLOBAL_INDEX=$(( ${{OFFSET:-0}} + SLURM_ARRAY_TASK_ID ))\n\nsingularity run --env AWS_BATCH_JOB_ID='foo' --bind {mnt_dir}/input:/mnt/data/input,{mnt_dir}/flpe:/mnt/data/flpe,{mnt_dir}/moi:/mnt/data/output {sif_dir/'moi.simg'} -j basin.json -v -b unconstrained -i ${{GLOBAL_INDEX}}",  
            "consensus": f"GLOBAL_INDEX=$(( ${{OFFSET:-0}} + SLURM_ARRAY_TASK_ID ))\n\n{run_input},{mnt_dir}/flpe:/mnt/data/flpe {sif_dir/'consensus.simg'} --mntdir /mnt/data -r /mnt/data/input/reaches.json -i ${{GLOBAL_INDEX}}",
            "unconstrained_offline": f"GLOBAL_INDEX=$(( ${{OFFSET:-0}} + SLURM_ARRAY_TASK_ID ))\n\n{run_input},{mnt_dir}/flpe:/mnt/data/flpe,{mnt_dir}/moi:/mnt/data/moi,{mnt_dir}/offline:/mnt/data/output {sif_dir/'offline.simg'} unconstrained timeseries integrator reaches.json ${{GLOBAL_INDEX}}",
            "validation": f"GLOBAL_INDEX=$(( ${{OFFSET:-0}} + SLURM_ARRAY_TASK_ID ))\n\n{run_input},{mnt_dir}/flpe:/mnt/data/flpe,{mnt_dir}/moi:/mnt/data/moi,{mnt_dir}/offline:/mnt/data/offline,{mnt_dir}/validation:/mnt/data/output {sif_dir/'validation.simg'} -r reaches.json -t unconstrained -i ${{GLOBAL_INDEX}}",
            "output": f"{run_input},{mnt_dir}/flpe:/mnt/data/flpe,{mnt_dir}/diagnostics:/mnt/data/diagnostics,{mnt_dir}/moi:/mnt/data/moi,{mnt_dir}/offline:/mnt/data/offline,{mnt_dir}/validation:/mnt/data/validation,{mnt_dir}/output:/mnt/data/output {sif_dir/'output.simg'} -s local -j /app/metadata/metadata.json -m input prediagnostics momma metroman sic4dvar consensus swot -v 17b -i ${{SLURM_ARRAY_TASK_ID}}",
        }
        # fmt: on

        def create_slurm_script(
            job_details: dict[str: str], build_image: bool, sif_dir: Path
        ):
            submission_prefix = job_details["submission_prefix"]
            if build_image:
                module_name = str(job_details["module_name"])
                image_name = (
                    module_name.replace("expanded_", "")
                    .replace("non_", "")
                    .replace("unconstrained_", "")
                    .replace("constrained_", "")
                )
                sp.run(
                    [
                        "singularity",
                        "build",
                        "-F",
                        sif_dir / f"{image_name}.simg",
                        f"docker://{job_details['docker_username']}/{image_name}:{custom_tag_name}",
                    ]
                )

            file = open(sh_dir / f"{module_to_run}.sh", "w")
            file.write("#!/bin/bash \n")
            file.write(f"{submission_prefix} -o {report_dir/module_to_run}.%j_%a.out\n")

            for item in job_details:
                if item not in [
                    "run_command",
                    "module_name",
                    "docker_username",
                    "submission_prefix",
                ]:
                    file.write(f"{submission_prefix} --{item}={job_details[item]} \n")
            file.write(job_details["run_command"])
            file.close()

        for module_to_run, run_command in command_dict.items():
            if module_to_run == "moi":
                time_to_use = "00:30:00"
                mem_to_use = "2G"
            elif module_to_run == "output":
                time_to_use = "05:00:00"
                mem_to_use = "4G"
            elif module_to_run == "input":
                time_to_use = "24:00:00"
                mem_to_use = "4G"
            else:
                time_to_use = "00:20:00"
                mem_to_use = "4G"

            if included_modules and (module_to_run not in included_modules):
                continue

            print("DIRECTORY NAME: ", run, "\nMODULE: ", module_to_run)

            job_details.update(
                {
                    "run_command": run_command,
                    "module_name": module_to_run,
                    "mem": mem_to_use,
                    "time": time_to_use,
                    "docker_username": docker_username,
                    "submission_prefix": submission_prefix,
                    "job-name": f"{module_to_run}_{run}_cfl",
                }
            )

            create_slurm_script(
                job_details=job_details, build_image=build, sif_dir=sif_dir
            )


def generate_slurm_driver(
    job_name: str,
    output_log_dir: str,
    partition: str,
    time_limit: str,
    nodes: int,
    ntasks: int,
    cpus_per_task: int,
    mem: str,
    run: str,
    directory: str,
    reach_chunks: int,
    json_file: str,
    expanded_json_file: str,
    reach_json_file: str,
    basin_json_file: str,
    metroman_json_file: str,
    batch_size: int,
    concurrent_jobs: int,
    script_jobs: dict[str, str],
    scripts: list[str],
    dry_run: bool = False,
) -> str:

    slurm_header = f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --output={output_log_dir}/{job_name}_%j_%a.out
#SBATCH --error={output_log_dir}/{job_name}_%j_%a.err
#SBATCH --partition={partition}
#SBATCH --time={time_limit}
#SBATCH --nodes={nodes}
#SBATCH --ntasks={ntasks}
#SBATCH --cpus-per-task={cpus_per_task}
#SBATCH --mem={mem}
#SBATCH --account=pi_cjgleason_umass_edu

run='{run}'
echo "Run: $run"
"""

    if dry_run:
        slurm_header += """
echo ""
echo "*** DRY RUN MODE — no jobs will be submitted ***"
echo ""
"""

    slurm_header += f"""
directory="{directory}"

# Parameters
index_range={reach_chunks}
json_file="{json_file}"
expanded_json_file="{expanded_json_file}"
reach_json_file="{reach_json_file}"
basin_json_file="{basin_json_file}"
metroman_json_file="{metroman_json_file}"
default_jobs=$(jq length "$json_file")

# Adjust to HPC requirements
batch_size={batch_size}
concurrent_jobs={concurrent_jobs}

# Map specific script names to their job counts
declare -A script_jobs=(
"""

    # Inject job counts into script_jobs associative array
    for script, jobs in script_jobs.items():
        slurm_header += f"    [{script}]={jobs}\n"
    slurm_header += ")\n\n"

    # Build scripts array
    script_array = "    " + "\n    ".join(scripts)
    scripts_block = f"""scripts=(
{script_array}
)
"""

    # The submission block differs based on dry_run mode
    if dry_run:
        submission_block = r"""
        echo "[DRY RUN] Script:      $slurm_script"
        echo "[DRY RUN] Reaches:     $start to $logical_end"
        echo "[DRY RUN] Tasks:       $num_tasks (index_range: $current_range, batch: $current_batch_count)"
        echo "[DRY RUN] Would run:   sbatch --export=ALL,OFFSET=${start},INDEX_RANGE=${current_range} --array=0-${last_task_index}%${concurrent_jobs} ${directory}/${slurm_script}"
        echo "---"
"""
    else:
        submission_block = r"""
        job_id=$(sbatch --export=ALL,OFFSET=${start},INDEX_RANGE=${current_range} \
               --array=0-${last_task_index}%${concurrent_jobs} \
               "${directory}/${slurm_script}")
        
        job_id_number=$(echo $job_id | awk '{print $4}')

        echo "Waiting for job array $job_id_number to finish..."

        while squeue -j "$job_id_number" 2>/dev/null | grep -q "$job_id_number"; do
            job_info=$(squeue -j "${job_id_number}[]" --noheader -o "%i %T %R")
            held_tasks=$(echo "$job_info" | grep -i "requeued held" | awk '{print $1}')

            if [[ -n "$held_tasks" ]]; then
                echo "Detected held tasks in array $job_id_number:"
                echo "$held_tasks"
                for task in $held_tasks; do
                    echo "Cancelling task $task..."
                    scancel "$task"
                done
            fi

            sleep 10
        done

        echo "Batch $job_id_number has finished. Submitting next batch."
        date
"""

    finish_msg = (
        'echo "DRY RUN complete. No jobs were submitted."'
        if dry_run
        else 'echo "Run $run has finished successfully."'
    )

    body = rf"""{scripts_block}




for slurm_script in "${{scripts[@]}}"; do
    echo "Starting submission for: $slurm_script"
    date

    # Initialize num_jobs from script_jobs array FIRST
    num_jobs="${{script_jobs[$slurm_script]}}"

    # Dynamic job count updates (files created during workflow)
    if [[ -s "$expanded_json_file" ]]; then
      expanded_jobs=$(jq length "$expanded_json_file")
      script_jobs["input.sh"]=$expanded_jobs
      # Update num_jobs if this is the input script
      if [[ "$slurm_script" == "input.sh" ]]; then
        num_jobs=$expanded_jobs
      fi
    fi

    if [[ -s "$basin_json_file" ]]; then
      basin_jobs=$(jq length "$basin_json_file")
      script_jobs["moi.sh"]=$basin_jobs
      if [[ "$slurm_script" == "moi.sh" ]]; then
        num_jobs=$basin_jobs
      fi
    fi

    if [[ -s "$metroman_json_file" ]]; then
      metroman_jobs=$(jq length "$metroman_json_file")
      script_jobs["metroman.sh"]=$metroman_jobs
      if [[ "$slurm_script" == "metroman.sh"  ]]; then
        num_jobs=$metroman_jobs
      fi
    fi

    # Fallback: all remaining $default_jobs modules use reaches.json once available,
    # otherwise fall back to reaches_of_interest.json
    if [[ -z "$num_jobs" || "$num_jobs" == "\$default_jobs" ]]; then
        if [[ -s "$reach_json_file" ]]; then
            num_jobs=$(jq length "$reach_json_file")
            echo "Using reach_json_file job count ($num_jobs) for $slurm_script"
        else
            num_jobs=$default_jobs
            echo "Using reaches_of_interest.json job count ($num_jobs) for $slurm_script"
        fi
    fi

    # Safety check
    if [[ -z "$num_jobs" ]]; then
        echo "Warning: No job count found for $slurm_script. Skipping."
        continue
    fi

    start=0
    while [ $start -lt $num_jobs ]; do
        # 1. Determine Step and Target
        if [[ "$slurm_script" == "input.sh" ]]; then
            current_range=$index_range
            target_reaches_per_batch=$(( batch_size * current_range ))
        else
            current_range=1
            target_reaches_per_batch=$batch_size
        fi

        # 2. Calculate actual reaches for this batch
        reaches_remaining=$(( num_jobs - start ))
        if [ $reaches_remaining -gt $target_reaches_per_batch ]; then
            current_batch_reach_count=$target_reaches_per_batch
        else
            current_batch_reach_count=$reaches_remaining
        fi
        
        # 3. Calculate SLURM tasks
        num_tasks=$(( (current_batch_reach_count + current_range - 1) / current_range ))
        last_task_index=$(( num_tasks - 1 ))
        logical_end=$(( start + current_batch_reach_count - 1 ))

        echo "Submitting $num_tasks tasks (Step: $current_range) for reaches $start to $logical_end"
        {submission_block}

        # 5. Increment start by the total number of reaches processed in this batch
        start=$((start + current_batch_reach_count))

        # Prevent infinite loop if calculation fails
        if [[ "$current_batch_reach_count" -le 0 ]]; then
            echo "Error: Batch reach count is 0. Terminating to prevent loop."
            break
        fi


        sleep 1
    
    done      
    
done

{finish_msg}
"""
    return slurm_header + body




def generate_local_run_scripts(
    run: str,
    modules_to_run: list,
    target_modules: list,
    script_jobs: dict,
    base_dir: str,
    repo_directory: str,
    rebuild_docker: bool,
    docker_username: str,
    push: bool,
    custom_tag_name: str,
    reach_chunks: int,
    continue_downloads: bool,
):
    """
    Generate Python scripts to run Docker containers locally for each module.
    Handles dynamic JSON file detection similar to SLURM version.
    """

    def to_docker_path(path):
        p = str(path).replace("\\", "/")
        if len(p) >= 2 and p[1] == ":":
            p = "/" + p[0].lower() + p[2:]
        return p

    # Directory structure
    mnt_dir_native = os.path.join(base_dir, f"confluence_{run}", f"{run}_mnt")
    mnt_dir = to_docker_path(mnt_dir_native)
    input_dir = os.path.join(mnt_dir_native, "input")
    sh_dir = os.path.join(base_dir, f"confluence_{run}", "sh_scripts")
    logs_dir = os.path.join(mnt_dir_native, "logs")

    os.makedirs(sh_dir, exist_ok=True)
    os.makedirs(os.path.join(mnt_dir_native, "logs"), exist_ok=True)  # use native path

    # JSON file paths (similar to HPC version)
    json_files = {
        "reaches_of_interest": os.path.join(input_dir, "reaches_of_interest.json"),
        "expanded": os.path.join(input_dir, "expanded_reaches_of_interest.json"),
        "reaches": os.path.join(input_dir, "reaches.json"),
        "basin": os.path.join(input_dir, "basin.json"),
        "metrosets": os.path.join(input_dir, "metrosets.json"),
    }

    # Build Docker images if requested
    if rebuild_docker:
        print("Building Docker images...")
        build_and_push_images(
            repo_directory=repo_directory,
            modules_to_run=target_modules,
            docker_username=docker_username,
            push=push,
            custom_tag_name=custom_tag_name,
        )

    run_data = f"docker run -v {mnt_dir}/input:/data"
    run_input = f"docker run -v {mnt_dir}/input:/mnt/data/input"
    module = lambda m: f"{docker_username}/{m}:{custom_tag_name}"
    skip_flag = " -k" if continue_downloads else ""

    # Command dictionary
    command_dict = {
        "expanded_setfinder": f"{run_data} {module('setfinder')} -r reaches_of_interest.json -c continent.json -e -s 17b -o /data -n /data -a MetroMan HiVDI SIC -i {{index}}",
        "expanded_combine_data": f"{run_data} {module('combine_data')} -d /data -e -s 17b",
        "input": f"docker run -v {mnt_dir}/input:/mnt/data {module('input')} -v 17b -r /mnt/data/expanded_reaches_of_interest.json -c SWOT_L2_HR_RiverSP_D -i {{index}} -a {reach_chunks}{skip_flag}",
        "non_expanded_setfinder": f"{run_data} {module('setfinder')} -c continent.json -s 17b -o /data -n /data -a MetroMan HiVDI SIC -i {{index}}",
        "non_expanded_combine_data": f"{run_data} {module('combine_data')} -d /data -s 17b",
        "prediagnostics": f"{run_input} -v {mnt_dir}/diagnostics/prediagnostics:/mnt/data/output {module('prediagnostics')} -r reaches.json -i {{index}}",
        "metroman": f"docker run --env AWS_BATCH_JOB_ID='foo' -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe/metroman:/mnt/data/output {module('metroman')} -r metrosets.json -s local -v -i {{index}}",
        "metroman_consolidation": f"{run_input} -v {mnt_dir}/flpe/metroman:/mnt/data/flpe {module('metroman_consolidation')} -i {{index}}",
        "unconstrained_momma": f"{run_input} -v {mnt_dir}/flpe/momma:/mnt/data/output {module('momma')} -r reaches.json -m 3 -i {{index}}",
        "constrained_momma": f"{run_input} -v {mnt_dir}/flpe/momma:/mnt/data/output {module('momma')} -r reaches.json -m 3 -c -i {{index}}",
        "sad": f"{run_input} -v {mnt_dir}/flpe/sad:/mnt/data/output {module('sad')} --reachfile reaches.json --index {{index}}",
        "hivdi": f"{run_input} -v {mnt_dir}/flpe/hivdi:/mnt/data/flpe/hivdi {module('hivdi')} /mnt/data/input/reaches.json --input-dir /mnt/data/input -i ${{index}}",
        "sic4dvar": f"{run_input} -v {mnt_dir}/flpe/sic4dvar:/mnt/data/output -v {mnt_dir}/logs:/mnt/data/logs {module('sic4dvar')} -r reaches.json --index {{index}}",
        "moi": f"docker run --env AWS_BATCH_JOB_ID='foo' -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe:/mnt/data/flpe -v {mnt_dir}/moi:/mnt/data/output {module('moi')} -j basin.json -v -b unconstrained -i {{index}}",
        "consensus": f"{run_input} -v {mnt_dir}/flpe:/mnt/data/flpe {module('consensus')} --mntdir /mnt/data -r /mnt/data/input/reaches.json -i {{index}}",
        "unconstrained_offline": f"{run_input} -v {mnt_dir}/flpe:/mnt/data/flpe -v {mnt_dir}/moi:/mnt/data/moi -v {mnt_dir}/offline:/mnt/data/output {module('offline')} unconstrained timeseries integrator reaches.json {{index}}",
        "validation": f"{run_input} -v {mnt_dir}/flpe:/mnt/data/flpe -v {mnt_dir}/moi:/mnt/data/moi -v {mnt_dir}/offline:/mnt/data/offline -v {mnt_dir}/validation:/mnt/data/output {module('validation')} reaches.json unconstrained {{index}}",
        "output": f"{run_input} -v {mnt_dir}/flpe:/mnt/data/flpe -v {mnt_dir}/moi:/mnt/data/moi -v {mnt_dir}/diagnostics:/mnt/data/diagnostics -v {mnt_dir}/offline:/mnt/data/offline -v {mnt_dir}/validation:/mnt/data/validation -v {mnt_dir}/output:/mnt/data/output {module('output')} -s local -j /app/metadata/metadata.json -m input momma metroman sic4dvar consensus swot -v 17b -i {{index}}",
    }

    output_paths = []

    for module in modules_to_run:
        if module not in command_dict:
            print(f"Warning: No command defined for module '{module}', skipping")
            continue

        job_count = script_jobs.get(module, "1")

        # Generate Python script with dynamic job count detection and logging support
        script_content = f'''#!/usr/bin/env python3
import subprocess as sp
import sys
import os
import json
from math import ceil

# Module: {module}

# Check for --log flag
use_logging = '--log' in sys.argv
logs_dir = r'{logs_dir}'

# JSON file paths
json_files = {{
    'reaches_of_interest': r'{json_files["reaches_of_interest"]}',
    'expanded': r'{json_files["expanded"]}',
    'reaches': r'{json_files["reaches"]}',
    'basin': r'{json_files["basin"]}',
    'metrosets': r'{json_files["metrosets"]}',
}}

def get_json_length(filepath):
    """Get length of JSON array file"""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
            if isinstance(data, list):
                return len(data)
    except Exception as e:
        print(f"Error reading {{filepath}}: {{e}}")
    return None

# Determine job count for this module
job_count = "{job_count}"

if job_count == "$default_jobs":
    # Dynamic job count based on module-specific logic
    num_jobs = None
    
    # Module-specific JSON file selection (matching HPC logic)
    if "{module}" == "input":
        # Use expanded_reaches_of_interest.json if it exists
        num_jobs = get_json_length(json_files['expanded'])
        num_jobs = ceil(num_jobs / {int(reach_chunks)})
        if num_jobs is None:
            print("Error: expanded_reaches_of_interest.json not found for input module")
            print("Make sure expanded_combine_data has been run first")
            sys.exit(1)
    
    elif "{module}" == "metroman":
        # Use metrosets.json if it exists, otherwise reaches.json, otherwise reaches_of_interest.json
        num_jobs = get_json_length(json_files['metrosets'])
        if num_jobs is None:
            num_jobs = get_json_length(json_files['reaches'])
        if num_jobs is None:
            num_jobs = get_json_length(json_files['reaches_of_interest'])
    
    elif "{module}" == "moi":
        # Use basin.json if it exists, otherwise reaches.json, otherwise reaches_of_interest.json
        num_jobs = get_json_length(json_files['basin'])
        if num_jobs is None:
            num_jobs = get_json_length(json_files['reaches'])
        if num_jobs is None:
            num_jobs = get_json_length(json_files['reaches_of_interest'])
    
    else:
        # For most modules: use reaches.json if exists, otherwise reaches_of_interest.json
        num_jobs = get_json_length(json_files['reaches'])
        if num_jobs is None:
            num_jobs = get_json_length(json_files['reaches_of_interest'])
    
    if num_jobs is None:
        print("Error: Could not determine job count for module '{module}'")
        sys.exit(1)
    
    print(f"Determined {{num_jobs}} job(s) dynamically for module '{module}'")
else:
    num_jobs = int(job_count)

# Docker command template
command_template = r"""{command_dict[module]}"""

print(f"\\nStarting module: {module}")
print(f"Running {{num_jobs}} job(s)")
if use_logging:
    print(f"Logs will be written to: {{logs_dir}}")
print()

for index in range(num_jobs):
    print(f"--- Running job {{index + 1}}/{{num_jobs}} for module '{module}' ---")

    if '{module}' == 'input':
        # offset the index because of the chunking.
        index *= {int(reach_chunks)}
    
    # Replace {{index}} with actual index
    run_command = command_template.replace('{{index}}', str(index))
    
    if use_logging:
        # Write output to log file
        log_file = os.path.join(logs_dir, f"{module}_{{index}}.log")
        print(f"Logging to: {{log_file}}")
        
        try:
            with open(log_file, 'w') as f:
                result = sp.run(run_command, shell=True, stdout=f, stderr=sp.STDOUT)
            
            if result.returncode == 0:
                print(f"Job {{index}} completed successfully\\n")
            else:
                print(f"Job {{index}} failed with exit code {{result.returncode}}")
                print(f"Check log: {{log_file}}\\n")
        except Exception as e:
            print(f"Error running job {{index}}: {{e}}\\n")
    else:
        # Direct output to terminal
        print(f"Command: {{run_command}}")
        
        try:
            result = sp.run(run_command, shell=True, check=True)
            print(f"Job {{index}} completed successfully\\n")
        except sp.CalledProcessError as e:
            print(f"Job {{index}} failed with exit code {{e.returncode}}\\n")

print(f"All jobs completed for module '{module}'")
if use_logging:
    print(f"Logs saved in: {{logs_dir}}")
'''

        output_script_path = os.path.join(sh_dir, f"run_{module}.py")
        with open(output_script_path, "w") as f:
            f.write(script_content)

        os.chmod(output_script_path, 0o755)
        output_paths.append(output_script_path)
        print(f"Created: {output_script_path}")

    return output_paths


def generate_run_all_modules_script(
    run: str,
    modules_to_run: list,
    script_jobs: dict,
    base_dir: str,
    script_name: str = "run_all_modules.sh",
):
    """
    Generate a bash script that runs all module scripts in series.

    Parameters
    ----------
    run : str
        Run name
    modules_to_run : list
        List of modules
    script_jobs : dict
        Module -> job count mapping
    base_dir : str
        Base directory
    script_name : str
        Name of the generated script
    """
    sh_dir = os.path.join(base_dir, f"confluence_{run}", "sh_scripts")
    script_path = os.path.join(sh_dir, script_name)

    # Filter modules with non-zero job counts
    filtered_modules = [m for m in modules_to_run if script_jobs.get(m, "0") != "0"]

    # Generate modules array
    modules_array = "modules_to_run=(\n"
    for module in filtered_modules:
        modules_array += f'    "{module}"\n'
    modules_array += ")\n"

    script_content = f"""#!/bin/bash
# {script_name}
# Runs all module scripts in series for run: {run}

SCRIPT_DIR="{sh_dir}"

{modules_array}

echo "Starting Confluence Run: {run}"

for module in "${{modules_to_run[@]}}"; do
    script_path="${{SCRIPT_DIR}}/run_${{module}}.py"
    
    if [[ -f "$script_path" ]]; then
        echo "Running module: $module"
        echo "Script: $script_path"
        python3 "$script_path" "$@"
        
        if [[ $? -ne 0 ]]; then
            echo "Error occurred while running $module. Exiting."
        fi
        
        echo "Finished module: $module"
        echo ""
    else
        echo "Script not found for module: $module. Skipping."
    fi
done

echo "All modules finished!"
"""

    with open(script_path, "w") as f:
        f.write(script_content)

    os.chmod(script_path, 0o755)
    print(f"Created: {script_path}")
    return script_path