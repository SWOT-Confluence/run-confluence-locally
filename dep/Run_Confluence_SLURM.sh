#!/bin/bash
#SBATCH --job-name=confluence_fs_s
#SBATCH --output=./log/confluence_fs_s%j.out
#SBATCH --error=./log/confluence_fs_s%j.err
#SBATCH --partition=ceewater_cjgleason-cpu
#SBATCH --time=30:00:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=10G

total_jobs=22777           # Total number of jobs to process (reach_ids)
array_step=10000           # Size of each major array chunk (e.g., 0-9999, 10000-19999, ...)
batch_size=1000            # Number of jobs to run in each batch within the array chunk
concurrent_jobs=400        # Max number of concurrent jobs in a batch (controlled via % in --array)

# Path to job scripts
directory="/nas/cee-water/cjgleason/ellie/SWOT/confluence/confluence_fs_s/"

# List of job scripts to run through the full job range
scripts=(
    expanded_setfinder.sh
    expanded_combine_data.sh
    input_fs.sh
    non_expanded_setfinder.sh
    non_expanded_combine_data.sh
    prediagnostics_s_bb_bb.sh
    hivdi.sh
    metroman.sh
    metroman_consolidation.sh
    unconstrained_momma.sh
    neobam.sh
)

# Load required modules; not really needed for this
module load conda/latest

# Loop over each SLURM job script
for slurm_script in "${scripts[@]}"; do
    echo "Starting submission for: $slurm_script"
    date

    # Outer loop: split entire job set into 10,000-sized array ranges
    array_start=0
    while [ $array_start -le $total_jobs ]; do
        array_end=$((array_start + array_step - 1))
        if [ $array_end -gt $total_jobs ]; then
            array_end=$total_jobs
        fi

        echo "Processing array range: ${array_start}-${array_end}"

        # Inner loop: within array range, split into batches of 1000
        start=$array_start
        while [ $start -le $array_end ]; do
            end=$((start + batch_size - 1))
            if [ $end -gt $array_end ]; then
                end=$array_end
            fi

            echo "Submitting batch: ${start}-${end} from $slurm_script"

            # Submit array job for this batch
            job_output=$(sbatch --array=${start}-${end}%${concurrent_jobs} "${directory}/${slurm_script}")
            job_id=$(echo $job_output | awk '{print $4}')

            echo "Submitted batch ${start}-${end} (Job ID: $job_id), waiting for it to finish..."

            # Wait for current batch to finish before moving to the next
            while squeue -j "$job_id" 2>/dev/null | grep -q "$job_id"; do
                sleep 15
            done

            echo "Finished batch ${start}-${end} (Job ID: $job_id)"
            sleep 5  # Delay between batches

            start=$((end + 1))
        done

        array_start=$((array_end + 1))
    done
done

echo "All submissions complete"
