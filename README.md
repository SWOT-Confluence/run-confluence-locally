siv4dvar is currently not working due to bug with the module .ini config file. 

# Install
Create a python environment using uv OR conda. If you don't use uv already you may need to install it using ```pip install uv```
```bash
uv sync
```
```bash
conda create -n confluence-env python=3.11 pip -c conda-forge -y
conda activate confluence-env
pip install -e .
```

# Run

## Configuration & Experiment Scenarios
The .yml configuration file controls the entire flow of a Confluence run. Below are 3 common experimental scenarios and their relevant configuration options. After that is a full listing of the config options. 

### 1. Full end-to-end run
**Goal:** Run the entire confluence pipeline in a single call. 

This configuration is aimed at getting as close as possible to reproducing the full 'online' versions of Confluence produced by PO.DAAC. This configuration will download the SWORD dataset, SOS priors, SVS validation file, and module code from their source repositories and download SWOT reach and node data via hydrocron. 

See the 1_end_to_end.yml file in the examples directory for the full config. This file is set up to clone the repositories under the 'main' branch of the SWOT-Confluence Github account and run them as is. 

### 2. Reusing data from a previous run.
**Goal:** Test Confluence without redownloading or duplicating input files. 

On the UMass Unity HPC, this is particularly useful as we have run confluence through `prediagnostics` for all reaches in SWORD v17. This means we can just bind this directory to our new run and skip downloading. We can also bind the priors and SWORD datasets instead of downloading or copying. See the 2_partial_run.yml file in the examples directory for full config, but the most relevant section is:
```yml
swot_input_bind_dir: "/nas/cee-ice/data/Confluence_Runs/global_vD/global_vD_mnt/input/swot"
priors_bind_dir: "/nas/cee-water/cjgleason/ted/confluence/end_to_end/end_to_end/input/sos"
sword_bind_dir: "/nas/cee-water/cjgleason/ted/confluence/end_to_end/end_to_end/input/sword"
svs_copy_dir: "/nas/cee-water/cjgleason/ted/confluence/end_to_end/end_to_end/validation"
```
These settings will alow us to reuse global download of SWOT data and the priors, SWORD, and SVS data from the previous end-to-end run. This saves ~10-300GB of disk storage per experiment depending on the scale of your testing. Even though you will not run the `input` module, you will still need to run the `setfinder` and `combine_data` modules to define the reach sets. These can be a subset of what is available in the `swot_input_bind_dir`.
> **Note** this assumes the previous run contains all the reaches that you need for your experiment. Because of the way the prediagnostics module works, you are also unable to change the filtering and flagging of the data. 


### 3. Development and testing
**Goal:**  Use a module repository from a particular user and branch.

This is largely the same as scenario 2, but with a custom branch id. See the 3_development.yml file in the examples directory for full config, but the most relevant section is:
```yml
module_branches:
  unconstrained_momma: "tedlanghorst:dev"
```
This will pull the module 'unconstrained_momma' 



### Configuration (`Config`)

| Option| Description |
| --- | --- |
| `root_dir` | Root directory for the pipeline. |
| `run_name` | Identifier for the current run. |
| `roi_file` | Path to the Region of Interest file. Must exist on the filesystem. |
| `sword_version` | Explicit version of SWORD to use. Must be `"16"` or `"17"`. |
| `swot_input_bind_dir` | Mount point for SWOT input files. **Constraint:** Must share the same filesystem root as `root_dir`. Cannot be used if `modules_to_run` includes `input` or `prediagnostics`. |
| `priors_bind_dir` | Mount point for priors data. **Constraint:** Must share the same filesystem root as `root_dir`. Mutually exclusive with `priors_copy_dir` and `priors_zenodo_doi`. |
| `priors_copy_dir` | Directory from which to copy priors data. **Constraint:** Mutually exclusive with `priors_bind_dir` and `priors_zenodo_doi`. |
| `priors_zenodo_doi` | Zenodo DOI for downloading priors data. **Constraint:** Mutually exclusive with `priors_bind_dir` and `priors_copy_dir`. |
| `sword_bind_dir` | Mount point for SWORD data. **Constraint:** Must share the same filesystem root as `root_dir`. Mutually exclusive with `sword_copy_dir` and `sword_zenodo_doi`. |
| `sword_copy_dir` | Directory from which to copy SWORD data. **Constraint:** Mutually exclusive with `sword_bind_dir` and `sword_zenodo_doi`. |
| `sword_zenodo_doi` | Zenodo DOI for downloading SWORD data. **Constraint:** Mutually exclusive with `sword_bind_dir` and `sword_copy_dir`. |
| `svs_copy_dir` | Directory from which to copy SVS data. **Constraint:** Mutually exclusive with `svs_repo_filename`. |
| `svs_repo_filename` | SVS repository filename to use. **Constraint:** Mutually exclusive with `svs_copy_dir`. |
| `default_github_username` | Default username for cloning necessary GitHub repositories. |
| `default_repository_branch` | Default branch to checkout upon cloning repositories. |
| `default_image_release_tag` | Default container image tag to pull/use. |
| `max_reaches` | Maximum number of reaches to process. Must be $\ge$ 0 (0 likely implies all). |
| `overwrite_run` | If true, clears/overwrites an existing directory matching `run_name`. |
| `clone_repos` | If true, initiates cloning of required source repositories. |
| `build_modules` | If true, triggers the module build processes. |
| `container_platform` | Container execution platform. Currently limited to `"apptainer"`. |
| `submit_driver` | If true, submits the primary driver script to the job scheduler. |
| `modules_to_run` | Array of module names scheduled for execution. **Constraint:** Each listed module must have a corresponding entry in `module_templates`. |
| `modules_to_build` |  Array of module names designated for building. Defaults to an empty list. |
| `module_branches` |  Key-value pairs overriding the default repository branch for specific modules. Defaults to empty. |
| `hpc` | HPC configuration parameters. See **HPC Options** below. |
| `module_templates` | Mapping of module names to their execution templates. See **ModuleTemplate Options** below. |
| `dirs` |  Internally populated dictionary of execution paths. Do not set manually; populated during setup. |

---

### HPC Options (`HPC`)

| Option | Type | Description |
| --- | --- | --- |
| `partition` | String | Target HPC partition or queue for job submission. |
| `account` | String | HPC account or allocation name for billing limits. |
| `time` | String | Walltime requested for the job (e.g., `"02:00:00"`). |
| `batch_size` | Integer | Volume of items processed per batch. Must be > 0. |
| `concurrent_jobs` | Integer | Maximum number of parallel jobs permitted. Must be > 0. |
| `reach_chunks` | Integer | Division parameter for processing reaches. Must be > 0. |

---

### Module Template Options (`ModuleTemplate`)

| Option | Type | Description |
| --- | --- | --- |
| `time` | String | Module-specific walltime limit (e.g., `"01:00:00"`). |
| `mem` | String | Module-specific memory allocation (e.g., `"16G"`). |
| `j2_file` | String | Path or filename of the Jinja2 template controlling module execution. |
| `module_args` | Dictionary | Unvalidated key-value pairs for arbitrary, module-specific arguments. Defaults to empty. |








# Results and Reminders
Modules MUST be run in serial and are dependent on each other (algorithm modules can be run in any order within the larger sequence).

Thus, any change to an early module or reaches_of_interest.json requires an entirely new confluence directory /mnt creation.

Results for setfinder through combine_data can be found in xxx_mnt/input/, hydrocron data can be found in xxx_mnt/input/swot/, prediagnostics in xxx_mnt/diagnostics, each algo results as format *reach_id*_*algo*.nc in xxx_mnt/flpe/*algo*, all results collected as .nc files by continent in xxx_mnt/output/.

To parse and organize discharge data, see:

PO.DAAC cookbook for working with SOS: Navigating reaches and nodes

Github Repo: Confluence Post Run Tools



### Module Descriptions (table)

| Module                              | Git Branch          | Number of Jobs / Reaches           | Description                                                                                                                                           |
|-------------------------------------|---------------------|------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| Expanded Setfinder (setfinder)                  | main                | 6                                  | Creates sets (groups of connected reaches) starting with your reaches of interest and looking up and down the river                                    |
| Expanded Combine Data (combine_data)               | main                | 1                                  | Combines the files generated in the setfinder into continent level data                                                                                |
| Input                               | input_D_products                | Number of Reaches                  | Pulls reach data from hydrocron and stores them in netcdfs, outputs to `/mnt/input/swot`                                                               |
| Non-Expanded Setfinder (setfinder)                          | main                | 6                                  | Creates sets (groups of connected reaches) only using the reaches that were pulled successfully using Input                                            |
| Non-Expanded Combine Data (combine_data)                        | main                | 1                                  | Combines files generated in the setfinder into continent level data **OVERWRITES continent.json**                                                                                   |
| Prediagnostics                      | main                | Number of Reaches                  | Filters reach data netcdfs based on a series of bitwise filters and outlier detectors. **OVERWRITES SWORD NETCDFS**                                          |
| Priors                              | main                | 6                                  | Pulls gauge data from external gauge agencies and builds the prior database (Priors SoS) - constrained and unconstrained                         |
| Metroman                            | main                | Number of Sets in `metrosets.json` | Runs the metroman FLPE algorithm, outputs to `/mnt/flpe/metroman/sets`                                                                                 |
| Metroman Consolidation              | main                | Number of Reaches                  | Takes the set level results of metroman and turns them into individual files, outputs to `/mnt/flpe/metroman`                                          |
| Momma, BUSBOI, SAD, H2ivdi, Sic4dvar        | main   | Number of Reaches                  | Runs the corresponding FLPE algorithm                                                                                                                  |
| MOI                                 | main                | Number of basins in `basins.json`  | Combines FLPE algorithm results (not currently working because of SWORD v16 topology issues)                                                           |
| Offline (offline-discharge-data-product-creation)  | main                | Number of Reaches                  | Runs NASA SDS's discharge algorithm                                                                                                                    |
| Validation                          | main                | Number of Reaches                  | If there is a validation gauge on the reach then summary stats are produced. (All gauges are validation in unconstrained runs)                          |
| Output                              | add-sword-version                | 6                                  | Outputs results netcdf files that store all previous results data, outputs to `/mnt/output/sos`                                                        |



