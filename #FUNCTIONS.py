#FUNCTIONS
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
    custom_tag_name: str
):
    """
    Generate Python scripts to run Docker containers locally for each module.
    Handles dynamic JSON file detection similar to SLURM version.
    """
    # Directory structure
    mnt_dir = os.path.join(base_dir, f'confluence_{run}', f'{run}_mnt')
    input_dir = os.path.join(mnt_dir, 'input')
    sh_dir = os.path.join(base_dir, f'confluence_{run}', 'sh_scripts')
    logs_dir = os.path.join(mnt_dir, 'logs')
    os.makedirs(sh_dir, exist_ok=True)
    os.makedirs(logs_dir, exist_ok=True)
    
    # JSON file paths (similar to HPC version)
    json_files = {
        'reaches_of_interest': os.path.join(input_dir, 'reaches_of_interest.json'),
        'expanded': os.path.join(input_dir, 'expanded_reaches_of_interest.json'),
        'reaches': os.path.join(input_dir, 'reaches.json'),
        'basin': os.path.join(input_dir, 'basin.json'),
        'metrosets': os.path.join(input_dir, 'metrosets.json'),
    }
    
    # Build Docker images if requested
    if rebuild_docker:
        print("Building Docker images...")
        build_and_push_images(
            repo_directory=repo_directory,
            modules_to_run=target_modules,
            docker_username=docker_username,
            push=push,
            custom_tag_name=custom_tag_name
        )
    
    # Command dictionary
    command_dict = {
        'expanded_setfinder': f'docker run -v {mnt_dir}/input:/data {docker_username}/setfinder:{custom_tag_name} -r reaches_of_interest.json -c continent.json -e -s 17 -o /data -n /data -a MetroMan HiVDI SIC -i {{index}}',
        'expanded_combine_data': f'docker run -v {mnt_dir}/input:/data {docker_username}/combine_data:{custom_tag_name} -d /data -e -s 17',
        'input': f'docker run -v {mnt_dir}/input:/mnt/data {docker_username}/input:{custom_tag_name} -v 17 -r /mnt/data/expanded_reaches_of_interest.json -c SWOT_L2_HR_RiverSP_D -i {{index}}',
        'non_expanded_setfinder': f'docker run -v {mnt_dir}/input:/data {docker_username}/setfinder:{custom_tag_name} -c continent.json -s 17 -o /data -n /data -a MetroMan HiVDI SIC -i {{index}}',
        'non_expanded_combine_data': f'docker run -v {mnt_dir}/input:/data {docker_username}/combine_data:{custom_tag_name} -d /data -s 17',
        'prediagnostics': f'docker run -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/diagnostics/prediagnostics:/mnt/data/output {docker_username}/prediagnostics:{custom_tag_name} -r reaches.json -i {{index}}',
        'unconstrained_priors': f'docker run -v {mnt_dir}/input:/mnt/data {docker_username}/priors:{custom_tag_name} -r unconstrained -p usgs riggs -g -s local -i {{index}}',
        'constrained_priors': f'docker run -v {mnt_dir}/input:/mnt/data {docker_username}/priors:{custom_tag_name} -r constrained -p usgs riggs -g -s local -i {{index}}',
        'metroman': f'docker run --env AWS_BATCH_JOB_ID="foo" -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe/metroman:/mnt/data/output {docker_username}/metroman:{custom_tag_name} -r metrosets.json -s local -v -i {{index}}',
        'metroman_consolidation': f'docker run -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe/metroman:/mnt/data/flpe {docker_username}/metroman_consolidation:{custom_tag_name} -i {{index}}',
        'unconstrained_momma': f'docker run -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe/momma:/mnt/data/output {docker_username}/momma:{custom_tag_name} -r reaches.json -m 3 -i {{index}}',
        'constrained_momma': f'docker run -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe/momma:/mnt/data/output {docker_username}/momma:{custom_tag_name} -r reaches.json -m 3 -c -i {{index}}',
        'sad': f'docker run -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe/sad:/mnt/data/output {docker_username}/sad:{custom_tag_name} --reachfile reaches.json --index {{index}}',
        'sic4dvar': f'docker run -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe/sic4dvar:/mnt/data/output -v {mnt_dir}/logs:/mnt/data/logs {docker_username}/sic4dvar:{custom_tag_name} -r reaches.json --index {{index}}',
        'moi': f'docker run --env AWS_BATCH_JOB_ID="foo" -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe:/mnt/data/flpe -v {mnt_dir}/moi:/mnt/data/output {docker_username}/moi:{custom_tag_name} -j basin.json -v -b unconstrained -i {{index}}',
        'consensus': f'docker run -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe:/mnt/data/flpe {docker_username}/consensus:{custom_tag_name} --mntdir /mnt/data -r /mnt/data/input/reaches.json -i {{index}}',
        'unconstrained_offline': f'docker run -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe:/mnt/data/flpe -v {mnt_dir}/moi:/mnt/data/moi -v {mnt_dir}/offline:/mnt/data/output {docker_username}/offline:{custom_tag_name} unconstrained timeseries integrator reaches.json {{index}}',
        'validation': f'docker run -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe:/mnt/data/flpe -v {mnt_dir}/moi:/mnt/data/moi -v {mnt_dir}/offline:/mnt/data/offline -v {mnt_dir}/validation:/mnt/data/output {docker_username}/validation:{custom_tag_name} reaches.json unconstrained {{index}}',
        'output': f'docker run -v {mnt_dir}/input:/mnt/data/input -v {mnt_dir}/flpe:/mnt/data/flpe -v {mnt_dir}/moi:/mnt/data/moi -v {mnt_dir}/diagnostics:/mnt/data/diagnostics -v {mnt_dir}/offline:/mnt/data/offline -v {mnt_dir}/validation:/mnt/data/validation -v {mnt_dir}/output:/mnt/data/output {docker_username}/output:{custom_tag_name} -s local -j /app/metadata/metadata.json -m input prediagnostics momma metroman sic4dvar consensus swot priors -v 17 -i {{index}}'
    }
    
    output_paths = []
    
    for module in modules_to_run:
        if module not in command_dict:
            print(f"Warning: No command defined for module '{module}', skipping")
            continue
        
        job_count = script_jobs.get(module, "1")
        
        # Generate Python script with dynamic job count detection, logging, and multiprocessing support
        script_content = f'''#!/usr/bin/env python3
import subprocess as sp
import sys
import os
import json
from multiprocessing import Pool
from itertools import repeat

# Module: {module}

# Check for --log flag
use_logging = '--log' in sys.argv
logs_dir = r'{logs_dir}'

# JSON file paths
json_files = {{
    'reaches_of_interest': r'{json_files['reaches_of_interest']}',
    'expanded': r'{json_files['expanded']}',
    'reaches': r'{json_files['reaches']}',
    'basin': r'{json_files['basin']}',
    'metrosets': r'{json_files['metrosets']}',
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

def run_docker(args):
    """Run a single docker command with given index"""
    index, command_template = args
    
    # Replace {{index}} with actual index
    run_command = command_template.replace('{{index}}', str(index))
    
    if use_logging:
        # Write output to log file
        log_file = os.path.join(logs_dir, f"{module}_{{index}}.log")
        
        try:
            with open(log_file, 'w') as f:
                result = sp.run(run_command, shell=True, stdout=f, stderr=sp.STDOUT)
            
            if result.returncode == 0:
                return (index, True, None)
            else:
                return (index, False, f"Exit code {{result.returncode}}, check {{log_file}}")
        except Exception as e:
            return (index, False, str(e))
    else:
        # Direct output to terminal
        try:
            result = sp.run(run_command, shell=True, check=True)
            return (index, True, None)
        except sp.CalledProcessError as e:
            return (index, False, f"Exit code {{e.returncode}}")

# Determine job count for this module
job_count = "{job_count}"

if job_count == "$default_jobs":
    # Dynamic job count based on module-specific logic
    num_jobs = None
    
    # Module-specific JSON file selection (matching HPC logic)
    if "{module}" == "input":
        # Use expanded_reaches_of_interest.json if it exists
        num_jobs = get_json_length(json_files['expanded'])
        if num_jobs is None:
            print("Error: expanded_reaches_of_interest.json not found for input module")
            print("Make sure expanded_combine_data has been run first")
            sys.exit(1)
    
    elif "{module}" in ["metroman", "metroman_consolidation"]:
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

# Determine number of parallel workers
total_cores = os.cpu_count() or 2  # fallback in case os.cpu_count() returns None
max_workers = max(1, total_cores // 2)  # use at least 1 worker

# Docker command template
command_template = """{command_dict[module]}"""

print(f"\\nStarting module: {module}")
print(f"Running {{num_jobs}} job(s)")
print(f"Using {{max_workers}} out of {{total_cores}} available cores for parallel processing.")
if use_logging:
    print(f"Logs will be written to: {{logs_dir}}")
print()

# Create output directory if needed
if "{module}" == "prediagnostics":
    prediag_dir = os.path.join(r'{mnt_dir}', 'diagnostics', 'prediagnostics')
    if not os.path.exists(prediag_dir):
        os.makedirs(prediag_dir)
        print(f"Created directory: {{prediag_dir}}")

# Run jobs in parallel
indices = list(range(num_jobs))
with Pool(processes=max_workers) as pool:
    args_list = list(zip(indices, repeat(command_template)))
    results = pool.map(run_docker, args_list)

# Report results
successful = 0
failed = 0
for index, success, error_msg in results:
    if success:
        successful += 1
        print(f"Job {{index}} completed successfully")
    else:
        failed += 1
        print(f"Job {{index}} failed: {{error_msg}}")

print(f"\\nAll jobs completed for module '{module}'")
print(f"Successful: {{successful}}/{{num_jobs}}")
print(f"Failed: {{failed}}/{{num_jobs}}")
if use_logging:
    print(f"Logs saved in: {{logs_dir}}")
'''

        output_script_path = os.path.join(sh_dir, f"run_{module}.py")
        with open(output_script_path, 'w') as f:
            f.write(script_content)
        
        os.chmod(output_script_path, 0o755)
        output_paths.append(output_script_path)
        print(f"Created: {output_script_path}")