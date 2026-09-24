#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage:"
    echo "  $0 -j JOB_ID"
    echo "  $0 -n RUN_NAME"
    exit 1
}

JOB_ID=""
RUN_NAME=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -j|--job)
            [[ -z "$JOB_ID" ]] || { echo "Job specified multiple times"; exit 1; }
            JOB_ID="$2"
            shift 2
            ;;
        -n|--name)
            [[ -z "$RUN_NAME" ]] || { echo "Name specified multiple times"; exit 1; }
            RUN_NAME="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown argument: $1"
            usage
            ;;
    esac
done

# Require exactly one mode
if [[ -n "$JOB_ID" && -n "$RUN_NAME" ]]; then
    echo "Specify exactly one of --job or --name."
    exit 1
fi

if [[ -z "$JOB_ID" && -z "$RUN_NAME" ]]; then
    usage
fi

# Resolve run name from job id
if [[ -n "$JOB_ID" ]]; then
    DRIVER_NAME=$(squeue -h -j "$JOB_ID" -o "%j" 2>/dev/null || true)

    if [[ -z "$DRIVER_NAME" ]]; then
        DRIVER_NAME=$(sacct -n -X -j "$JOB_ID" --format=JobName | head -n1 | xargs)
    fi

    RUN_NAME="${DRIVER_NAME#confluence_driver_}"
fi

# squeue/scancel -n only match exact job names (no glob support), so spawned
# module jobs (named "<module>_${RUN_NAME}_cfl") must be matched by suffix
# ourselves and cancelled by job id.
DRIVER_COUNT=$(squeue -u "$USER" -h -n "confluence_driver_${RUN_NAME}" | wc -l)

mapfile -t SPAWNED_IDS < <(squeue -u "$USER" -h -o "%i %j" | awk -v suffix="_${RUN_NAME}_cfl" '$2 ~ (suffix "$") {print $1}')
SPAWNED_COUNT=${#SPAWNED_IDS[@]}
TOTAL_COUNT=$((DRIVER_COUNT + SPAWNED_COUNT))

echo "Run name: $RUN_NAME"
echo "Will cancel: $TOTAL_COUNT jobs"

if [[ "$TOTAL_COUNT" -eq 0 ]]; then
    echo "No matching active jobs found."
    exit 0
fi

read -rp "Proceed? [y/N] " ans
[[ "$ans" =~ ^[Yy]$ ]] || exit 0

scancel -u "$USER" -n "confluence_driver_${RUN_NAME}" || true
if [[ "$SPAWNED_COUNT" -gt 0 ]]; then
    scancel "${SPAWNED_IDS[@]}" || true
fi

echo "Done."