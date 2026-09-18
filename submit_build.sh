#!/bin/bash
# Runs `python run.py` on a compute node instead of the login node. The clusters at UWyo are more strict
# about login node usage than UMass, so I had to write this wrapper. The parallel git clone and apptainer
# builds are the only steps that are really resource intensive. 
#
# Usage (from the repo root):
#   sbatch submit_build.sh </path/to/config.yml> [run.py args...]
#
#SBATCH --job-name=confluence_build
#SBATCH --account=swot-ml
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=01:00:00

set -euo pipefail

source .venv/bin/activate
python run.py "$@"
