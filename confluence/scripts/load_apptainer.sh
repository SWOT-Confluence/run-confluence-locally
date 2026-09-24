#!/bin/bash
# Ensures apptainer is on PATH, loading the environment module if needed.
# Must be sourced (not executed) so that `module load` affects the calling shell.

if command -v apptainer &>/dev/null; then
    echo "Apptainer already available: $(apptainer --version)"
else
    version=$(
        module -t avail apptainer 2>&1 |
        grep -E '^apptainer/[0-9]+\.[0-9]+\.[0-9]+$' |
        sort -V |
        tail -1
    )

    if [[ -z "$version" ]]; then
        echo "No Apptainer module found."
        exit 1
    fi

    module load "$version"
    echo "Loaded $version"
fi
