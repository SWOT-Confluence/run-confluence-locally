#!/bin/bash
#SBATCH --job-name=e_124_perm2
#SBATCH --output=./log/confluence_e_124_perm2_%j.out
#SBATCH --error=./log/confluence_e_124_perm2_%j.err
#SBATCH --partition=cpu-preempt
#SBATCH --time=30:00:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=5G


run='e_124_perm2'
echo "Run: $run"

# Define the directory where your scripts are located
directory="/nas/cee-water/cjgleason/ellie/SWOT/confluence/confluence_${run}/"  # Change this to the actual directory path

# Parameters
json_file="/nas/cee-water/cjgleason/ellie/SWOT/confluence/confluence_${run}/${run}_mnt/input/reaches_of_interest.json" # Reaches of interest file

default_jobs=$(jq length "$json_file")

# Adjust to you HPC requirements
batch_size=1000  # Maximum number of jobs to submit per batch
concurrent_jobs=400  # Limit on concurrent jobs per batch (adjust to fit your QOS)


# SLURM script to be submitted

##all module options:
#expanded_setfinder.sh expanded_combine_data.sh input_all.sh non_expanded_setfinder.sh #non_expanded_combine_data.sh prediagnostics.sh unconstrained_priors.sh metroman.sh #metroman_consolidation.sh sic4dvar.sh unconstrained_momma.sh neobam.sh sad.sh moi.sh #unconstrained_offline.sh validation.sh output.sh

# Map specific script names to their job counts
declare -A script_jobs=(
    [expanded_setfinder.sh]=7
    [expanded_combine_data.sh]=1
    [input_so.sh]=$default_jobs
    [non_expanded_setfinder.sh]=7
    [non_expanded_combine_data.sh]=1
    [prediagnostics_permissive.sh]=$default_jobs
    # [unconstrained_priors.sh]=7
    [sad.sh]=$default_jobs
    [metroman.sh]=$default_jobs
    [metroman_consolidation.sh]=$default_jobs
    [sic4dvar.sh]=$default_jobs
    [unconstrained_momma.sh]=$default_jobs
    [neobam.sh]=$default_jobs
    [moi.sh]=$default_jobs
    [unconstrained_offline.sh]=$default_jobs
    [validation.sh]=$default_jobs
    [output.sh]=7
)

scripts=(
    expanded_setfinder.sh
    expanded_combine_data.sh
    input_so.sh
    non_expanded_setfinder.sh
    non_expanded_combine_data.sh
    prediagnostics_permissive.sh
    # unconstrained_priors.sh
    sad.sh
    metroman.sh
    metroman_consolidation.sh
    sic4dvar.sh
    unconstrained_momma.sh
    neobam.sh
    moi.sh
    unconstrained_offline.sh
    validation.sh
    output.sh
)


for slurm_script in "${scripts[@]}"; do
    echo "Starting submission for: $slurm_script"
    date

    num_jobs="${script_jobs[$slurm_script]}"
    if [[ -z "$num_jobs" ]]; then
        echo "Warning: No job count found for $slurm_script. Skipping."
        continue
    fi

    start=0
    while [ $start -lt $num_jobs ]; do
        end=$((start + batch_size - 1))
        if [ $end -ge $num_jobs ]; then
            end=$((num_jobs - 1))
        fi

        echo "Submitting jobs $start to $end from $slurm_script"
        job_id=$(sbatch --array=${start}-${end}%${concurrent_jobs} "${directory}/${slurm_script}")
        job_id_number=$(echo $job_id | awk '{print $4}')

        echo "Waiting for job array $job_id_number to finish..."
        while squeue -j "$job_id_number" 2>/dev/null | grep -q "$job_id_number"; do
            # Check all tasks in this array for 'launch failed requeued held' - common issue holding up jobs
            job_info=$(squeue -j "${job_id_number}[]" --noheader -o "%i %T %R")
            held_tasks=$(echo "$job_info" | grep -i "launch failed requeued held" | awk '{print $1}')

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

        start=$((end + 1))
        sleep 5
    done
done

echo "Run $run has finished successfully."
