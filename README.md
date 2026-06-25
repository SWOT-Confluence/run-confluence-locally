# Setup
1. Clone this repo on your HPC and navigate inside the directory. 

2. Create a python environment using uv. If you don't use uv already you may need to install it using ```pip install uv``` before calling:
    ```bash
    uv sync
    ```
3. Activate the uv environment:
    ```bash
    source .venv/bin/activate
    ```
# Run
The run.py script is intended to be able to do a full run of Confluence offline based on a single config file. In the next section are 3 common experimental scenarios and their relevant configuration options. In all cases, the way to run Confluence is to call:
```bash
python run.py </path/to/config.yml>
```
With the path to your configuration file as the only argument to the run.py script. 

The first thing that python will do is try to validate your config file against some basic rules... if any rules are broken it will throw an error into the console. One of those rules is that all path arguments in the config file must be **absolute** paths, meaning the have the entire path from the root of the file system and starts with a '/'. 


## Cancelling a run
Confluence is built to be pretty tolerant to individual failures, such that even when things are entirely broken, modules will continue to be submitted and failing. It's really important to monitor the status of the jobs and even check on the reports located in the run directory for problems. A single Confluence run can spawn thousands of slurm jobs and you want to avoid wasting time and compute resources. 

When you submit a Confluence run, you'll see a message like this:
```bash
Submitted batch job <JOB_ID>
```
We call this job a 'slurm_driver' which coordinates all the modules into many individual slurm jobs. If you just cancelled this job, the current module would continue to run, potentially for many hours depending on the scale of your run. The script `kill_jobs.sh` will kill this slurm_driver script and all of the workers based on their naming convention. You can call it using:
```bash
bash kill_jobs.sh -j <JOB_ID>
```

## 1. Full end-to-end run
**Goal:** Run the entire Confluence pipeline in a single call. 

This configuration is aimed at getting as close as possible to reproducing the full 'online' versions of Confluence produced by PO.DAAC. This configuration will download the SWORD dataset, SOS priors, SVS validation file, and module code from their source repositories and download SWOT reach and node data via hydrocron. 

See the 1_end_to_end.yml file in the examples directory for the full config. 

## 2. Reusing data from a previous run.
**Goal:** Test Confluence without redownloading or duplicating input files. 

On the UMass Unity HPC, this is particularly useful as we have run confluence through `prediagnostics` for all reaches in SWORD v17. This means we can just bind this directory to our new run and skip downloading. We can also bind the priors and SWORD datasets instead of downloading or copying. See the 2_partial_run.yml file in the examples directory for full config, but the most relevant section is:
```yml
swot_input_bind_dir: "/nas/cee-ice/data/Confluence_Runs/global_vD/global_vD_mnt/input/swot"
priors_bind_dir: "/nas/cee-water/cjgleason/ted/confluence/C_vs_D/confluence_v4D/v4D_mnt/input/sos"
sword_bind_dir: "/nas/cee-water/cjgleason/ted/confluence/C_vs_D/confluence_v4D/v4D_mnt/input/sword"
svs_copy_dir: "/nas/cee-water/cjgleason/ted/confluence/C_vs_D/confluence_v4D/v4D_mnt/input/svs"
```
These settings will alow us to reuse global download of SWOT data and the priors, SWORD, and SVS data from the previous end-to-end run. This saves ~10-300GB of disk storage per experiment depending on the scale of your testing. Even though you will not run the `input` module, you will still need to run the `setfinder` and `combine_data` modules to define the reach sets. These can be a subset of what is available in the `swot_input_bind_dir`.
> **Note** this assumes the previous run contains all the reaches and the same prediagnostics filter that you need for your experiment. The SWOT data will be bound as 'read-only' and cannot be appended or modified. 


## 3. Development and testing
**Goal:**  Iterate on module development while reusing data and module images.

This is similar to example two, but notice there are two yml files in the example directory: `3a_development_setup` and `3b_development_iteration`. The 3a configuration file is similar to example 1 where we are running the entire flow as a baseline using the standard versions of the modules and downloading fresh data. After this run, the idea is that we will modify the source code of a module and test the impact of that change on the rest of Confluence. The 3b configuration file takes several time-saving shortcuts by relying on the 3a outputs of the run. There are several notable changes:

1. We commented out the priors, sword, and svs download instructions. You will see a warning about not specifying these data, but know that the data are still there from the 3a run. If instead of downloading, you used the bind options for these datasets (as in example 2), you will need to keep those in place for 3b since that data is not actually in your mnt. 
    ```yml
    # priors_zenodo_doi: "10.5281/zenodo.20541765"
    # sword_zenodo_doi: "10.5281/zenodo.15299138"
    # svs_repo_filename: "SVS_v1_0_1.nc"
    ```
    
1. We cut out most of the modules from our `modules_to_run` list. We can simply reuse the data and files generated by the modules up to `prediagnostics`, and the other FLPE algorithsm do not interact with our planned changes to `momma`. We do want to keep running the `consensus`, `validation`, and `output` since these rely on the outputs of `unconstrained_momma`. 
    ```yml
    modules_to_run:
      - "unconstrained_momma"
      - "consensus"
      - "validation"
      - "output"
    ```

1. We set a different user and branch name for our new module code. This will pull the momma repository from the github user `tedlanghorst` and the branch `dev`. Any modules that are not specified here will fall back to pulling from the `default_github_username` and `default_repository_branch` parameters. Note that this refers to the repository name (momma) instead of the module name (unconstrained_momma).
    ```yml
    repo_branches:
      momma: "tedlanghorst:dev"
    ```

1. We specified **only** `momma` in the `repos_to_rebuild` argument, which means we will not rebuild the images for `consensus`, `validation`, and `output`.
    ```yml
    rebuild_all_modules: False
    repos_to_rebuild:
      - "momma"
    ```

You can run the 3b configuration multiple times after pushing new code to the `momma` repository. However, several of the intermediate outputs will be overwritten. The output files always have unique names and will not be overwritten, but you will need to keep track of the connections between specific module code versions and output files. 

### Configuration (`Config`)
> **Disclaimer** this table was generated with AI based on the code from .confluence/utils/config.py. This python file is the actual source of the configuration requirements and is fairly readable if you run into any issues. 
#### General Options

| Option | Type | Description |
| --- | --- | --- |
| `root_dir` | Path | Root directory for the pipeline. **Constraint:** Must be an absolute path. |
| `run_name` | String | Identifier for the current run. |
| `roi_file` | FilePath | Path to the Region of Interest file. **Constraint:** Must exist on the filesystem and be an absolute path. |
| `sword_version` | String | Explicit version of SWORD to use. Must be `"16"` or `"17"`. |
| `swot_input_bind_dir` | DirectoryPath \| None | Mount point for SWOT input files. **Constraint:** Must be an absolute path, share the same filesystem root as `root_dir`, and cannot be used if `modules_to_run` includes `input` or `prediagnostics`. |
| `priors_bind_dir` | DirectoryPath \| None | Mount point for priors data. **Constraint:** Must be an absolute path, share the same filesystem root as `root_dir`, and is mutually exclusive with `priors_copy_dir` and `priors_zenodo_doi`. |
| `priors_copy_dir` | DirectoryPath \| None | Directory from which to copy priors data. **Constraint:** Must be an absolute path and is mutually exclusive with `priors_bind_dir` and `priors_zenodo_doi`. |
| `priors_zenodo_doi` | String \| None | Zenodo DOI for downloading priors data. **Constraint:** Mutually exclusive with `priors_bind_dir` and `priors_copy_dir`. |
| `sword_bind_dir` | DirectoryPath \| None | Mount point for SWORD data. **Constraint:** Must be an absolute path, share the same filesystem root as `root_dir`, and is mutually exclusive with `sword_copy_dir` and `sword_zenodo_doi`. |
| `sword_copy_dir` | DirectoryPath \| None | Directory from which to copy SWORD data. **Constraint:** Must be an absolute path and is mutually exclusive with `sword_bind_dir` and `sword_zenodo_doi`. |
| `sword_zenodo_doi` | String \| None | Zenodo DOI for downloading SWORD data. **Constraint:** Mutually exclusive with `sword_bind_dir` and `sword_copy_dir`. |
| `svs_copy_dir` | DirectoryPath \| None | Directory from which to copy SVS data. **Constraint:** Must be an absolute path and is mutually exclusive with `svs_repo_filename`. |
| `svs_repo_filename` | String \| None | SVS repository filename to use. **Constraint:** Mutually exclusive with `svs_copy_dir`. |
| `default_github_username` | String | Default username for cloning necessary GitHub repositories. |
| `default_repository_branch` | String | Default branch to checkout upon cloning repositories. |
| `default_image_release_tag` | String | Default container image tag to pull/use. |
| `max_reaches` | Integer | Maximum number of reaches to process. Must be >= 0. Defaults to 0. |
| `overwrite_run` | Boolean | If true, clears/overwrites an existing directory matching `run_name`. Defaults to false. |
| `clone_repos` | Boolean | If true, initiates cloning of required source repositories. |
| `container_platform` | String | Container execution platform. Currently limited to `"apptainer"`. |
| `submit_driver` | Boolean | If true, submits the primary driver script to the job scheduler. |
| `modules_to_run` | List[String] | Array of module names scheduled for execution. **Constraint:** Each listed module must exist in `module_templates`. |
| `rebuild_all_modules` | Boolean | If true, triggers the rebuild processes for all modules. |
| `modules_to_rebuild` | List[String] | Array of specific module names designated for building/rebuilding. Defaults to empty. |
| `repo_branches` | Dictionary | Key-value pairs overriding the default repository branch for specific modules. Defaults to empty. |
| `hpc` | HPC Object | HPC configuration parameters. Defaults to an instantiated `HPC` model. |
| `module_templates` | Dictionary | Mapping of module names to their execution templates (`ModuleTemplate`). |
| `dirs` | Dictionary | Internally populated dictionary of execution paths. Do not set manually; populated during setup. |

---

#### HPC Options (`HPC`)

| Option | Type | Description |
| --- | --- | --- |
| `partition` | String | Target HPC partition or queue for job submission. |
| `account` | String | HPC account or allocation name for billing limits. |
| `time` | String | Walltime requested for the job (e.g., `"02:00:00"`). |
| `batch_size` | Integer | Volume of items processed per batch. Must be > 0. |
| `concurrent_jobs` | Integer | Maximum number of parallel jobs permitted. Must be > 0. |
| `reach_chunks` | Integer | Division parameter for processing reaches. Must be > 0. |

---

#### Module Template Options (`ModuleTemplate`)

| Option | Type | Description |
| --- | --- | --- |
| `time` | String | Module-specific walltime limit (e.g., `"01:00:00"`). |
| `mem` | String | Module-specific memory allocation (e.g., `"16G"`). |
| `j2_file` | String | Path or filename of the Jinja2 template controlling module execution. |
| `module_args` | Dictionary | Unvalidated key-value pairs for arbitrary, module-specific arguments. Defaults to empty. |





# Results and Reminders
Modules MUST be run in serial and are dependent on each other (algorithm modules can be run in any order within the larger sequence).

Results for setfinder through combine_data can be found in xxx_mnt/input/, hydrocron data can be found in xxx_mnt/input/swot/, prediagnostics in xxx_mnt/diagnostics, each algo results in xxx_mnt/flpe/\<algo\>/\<reach_id\>_\<algo\>.nc, and final results collected by continent in xxx_mnt/output/.

To parse and organize discharge data, see:
- [PO.DAAC cookbook for working with SOS: Navigating reaches and nodes](https://podaac.github.io/tutorials/notebooks/datasets/SWOT_L4_DAWG_SOS_DISCHARGE.html)
- [Confluence Post Run Notebooks](https://github.com/SWOT-Confluence/sos-notebooks)
- [The SWOT DAWG Youtube channel](https://www.youtube.com/@SWOTDAWG)



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



