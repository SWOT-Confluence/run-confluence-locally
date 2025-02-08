#!/bin/bash

# Parameters
total_jobs=100  # Total number of jobs you need to submit
batch_size=1000  # Maximum number of jobs to submit per batch
concurrent_jobs=400  # Limit on concurrent jobs per batch (adjust to fit your QOS)

# SLURM script to be submitted
slurm_script="input.sh"
date

# Submit in batches
start=0
while [ $start -lt $total_jobs ]; do
    end=$((start + batch_size - 1))
    if [ $end -ge $total_jobs ]; then
        end=$((total_jobs - 1))
    fi

    # Submit the first batch
    echo "Submitting jobs $start to $end"
    job_id=$(sbatch --array=${start}-${end}%${concurrent_jobs} $slurm_script)
    job_id_number=$(echo $job_id | awk '{print $4}')

    # Wait until the batch is completed
    echo "Waiting for job array $job_id_number to finish..."
    while squeue -j $job_id_number | grep -q "PD"; do
        sleep 10
    done

    echo "Batch $job_id_number has finished. Submitting next batch."
    date
    
    start=$((end + 1))

    # Optional: Add a small delay between submissions to avoid overwhelming the scheduler
    sleep 5
done
