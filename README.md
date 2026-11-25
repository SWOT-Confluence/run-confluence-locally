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

## Running without Slurm
On a workstation (or inside a single Slurm allocation), set `scheduler: "local"` and replace the `hpc` block with an optional `local` block (see [Local Options](#local-options-local)). `run.py` then runs the modules itself, in order, with up to `local.concurrent_jobs` tasks at once, and returns when the run is done. `configs/examples/4_local_workstation.yml` is a full example.

- Monitoring works as for Slurm runs, except that nothing watches tasks while a module is running: the gate checks each module after its canary (if on) and when it finishes, the run is audited at the end, and `locate`, `abort` and `throttle` work as below. The progress log is `log/<run_name>_local-<epoch>.out`, and task logs are in `report/` as usual.
- Ctrl-C stops the run: no new tasks start and the running ones are killed (a second Ctrl-C skips the 10 s grace period). The end-of-run audit is skipped; run `python -m confluence.monitor audit <run_dir>` if you want it.
- `run.py` exits with 0 when every module ran and passed its gate, and 3 when the run was aborted or interrupted.


## Cancelling a run
Confluence is built to be tolerant of individual failures. A single run can spawn thousands of slurm jobs, so a systemically broken module (bad image, wrong collection name, ...) used to keep failing through every batch and module. Monitoring (next section) now stops such runs automatically, but it's still worth checking on new runs. 

When you submit a Confluence run, you'll see a message like this:
```bash
Submitted batch job <JOB_ID>
```
We call this job a 'slurm_driver' which coordinates all the modules into many individual slurm jobs. If you just cancelled this job, the current module would continue to run, potentially for many hours depending on the scale of your run. The script `kill_jobs.sh` will kill this slurm_driver script and all of the workers based on their naming convention. You can call it using:
```bash
bash kill_jobs.sh -j <JOB_ID>
```

## Monitoring a run
Monitoring is switched on by default, so you don't need to do anything to get it. The optional `monitor:` block in your config only changes its settings (see [Monitor Options](#monitor-options-monitor-all-optional)).

### What happens automatically
When you submit a run, three things look after it:

- **The driver checks each module as it runs.** It checks the results after every batch and after the whole module. If a module is clearly broken, the driver **stops the whole run** instead of moving on, and it writes the reason to `<run_dir>/monitor/ABORT`. "Broken" means, for example, that most reaches crash, almost no outputs contain data, or the container image can't be opened. A few failing reaches are normal and don't stop a run. Neither do reaches that simply have no SWOT data.
- **A monitor runs inside the driver job.** While modules run, it keeps a progress table in `<run_dir>/monitor/status.txt`, and it can also stop the run if a module fails partway through.
- **An audit job writes the end-of-run report.** The driver queues it at start-up as a small Slurm job named `audit_<run_name>_cfl` (it shows as pending with reason `Dependency`). It starts as soon as the driver ends, whether the run finished, was aborted, hit its time limit or was cancelled. It writes `<run_dir>/monitor/audit.md`, with an easier-to-read copy in `audit.html` (open it in a browser or VS Code; print it to get a PDF).

> [!TIP]
> **Turn on the canary for risky runs.** With `canary_tasks` set, every per-reach module first runs on a small "canary" batch and is checked before the rest of its reaches are submitted, so a broken module (a missing image, a bad config, a code change that crashes) is stopped after a few minutes on a handful of reaches, before the rest are even submitted. It is **off by default**. Turn it on when you change module code, branches, images or priors:
>
> ```yaml
> monitor:
>   canary_tasks: 2   # 2 array tasks = 2 x reach_chunks reaches per module
> ```
>
> Without the canary, the monitor still stops a broken module partway through its first batch, but only after enough reaches have failed.

### Files to look at
All of these are in your run directory (`<root_dir>/confluence_<run_name>/`):

| File | What it tells you |
| --- | --- |
| `monitor/status.txt` | Live progress: one row per module with reaches done/failed, outputs written and an ETA. |
| `monitor/ABORT` | Only exists if the run was stopped. It says which module, and why. |
| `monitor/audit.md` (and `audit.html`) | End-of-run report. Its verdict is PASS, WARN or FAIL. It shows how many reaches each module finished, with a legend for every outcome (OK, no data, failed, ...), the SWOT download, failed Slurm tasks and the full text of the most common errors. |
| `RUN_GUIDE.md` | A plain-language overview of the run: how it was built (config, module versions), where every file is, and the results. |
| `monitor/events.log` | Timeline of every check the driver, the monitor and the audit job made. |

### Commands for checking a run
Run these from this repo with the environment activated (`source .venv/bin/activate`). `<run_dir>` is the run directory above.

```bash
# How far along is my run? (same table as monitor/status.txt)
python -m confluence.monitor status <run_dir>

# What happened to one reach? Shows each module's result, output file and log file, plus the log lines that explain it.
python -m confluence.monitor locate <run_dir> <reach_id>

# Stop a run cleanly. The driver and the monitor stop at their next check (the audit job still runs); the reason is recorded in monitor/ABORT.
python -m confluence.monitor abort --run-dir <run_dir> --reason "wrong config"

# Change how many array tasks run at once (hpc.concurrent_jobs, or local.concurrent_jobs) while the run is going. See below.
python -m confluence.monitor throttle <run_dir> 200
```

#### Scaling a running run up or down
`throttle` changes `hpc.concurrent_jobs` for the running driver without restarting it. Within about 10 seconds the driver sets the new limit on the array that is running (Slurm's `scontrol update ArrayTaskThrottle`), and it submits every later array with it. The change is recorded in `monitor/events.log`.

```bash
python -m confluence.monitor throttle <run_dir>           # show the current limit
python -m confluence.monitor throttle <run_dir> 100       # scale down, e.g. when the cluster is busy
python -m confluence.monitor throttle <run_dir> 600       # scale back up
python -m confluence.monitor throttle <run_dir> --reset   # back to hpc.concurrent_jobs from the config
```

- Lowering the limit does not stop running tasks. Slurm starts no new ones until fewer than the limit are running.
- An array holds at most `batch_size` tasks, so a limit above `batch_size` has no extra effect.
- The limit lasts only for this driver. A resubmitted run starts from `hpc.concurrent_jobs` in the config again.
- Local runs: the new limit applies from the next task start on, for every later module too. A module listed in `local.module_concurrency` never runs more tasks than its own limit.

To check a finished run again, or compare it with another run, write a new report with `audit`. It reads every module's outputs, so run it through Slurm rather than on the login node:
```bash
sbatch -A <account> --mem 16G --time 2:00:00 --wrap \
  "python -m confluence.monitor audit <run_dir> --baseline <other_run_dir>"
```
`--baseline` is optional. It adds a side-by-side comparison with another run (e.g. which reaches gained or lost results). These commands also work on runs made before monitoring existed. Add `--out <dir>` to write the report somewhere else instead of into the run directory, e.g. for someone else's run.

### If your run was stopped
1. Read the reason in `monitor/ABORT` (also printed at the end of the driver log in `log/`). It names the module, the rule that fired and the most common error message, with an example log file.
2. Fix the problem: config, image, input data, etc.
3. Resubmit as usual. An old `ABORT` file is moved aside automatically when the driver starts again.

`abort` vs `kill_jobs.sh`: `abort` lets the driver stop at its next check and keeps a record of why. `kill_jobs.sh` cancels every job of the run immediately.

### For developers
How the checks work is documented in `confluence/monitor/`: the per-module expectations are in `registry.py`, and the stop rules in `rules.py`.

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
      momma: "post_UAT"
    ```

1. We specified **only** `momma` in the `repos_to_rebuild` argument, which means we will not rebuild the images for `consensus`, `validation`, and `output`.
    ```yml
    rebuild_all_modules: False
    modules_to_rebuild:
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
| `svs_copy_dir` | DirectoryPath \| None | Directory from which to copy SVS data; it must hold exactly one `.nc` file. **Constraint:** Must be an absolute path and is mutually exclusive with `svs_copy_file` and `svs_repo_filename`. |
| `svs_copy_file` | FilePath \| None | A single SVS file to copy, for when its directory holds more than one. **Constraint:** Must be an absolute path and is mutually exclusive with `svs_copy_dir` and `svs_repo_filename`. |
| `svs_repo_filename` | String \| None | SVS repository filename to use. **Constraint:** Mutually exclusive with `svs_copy_dir` and `svs_copy_file`. |
| `default_github_username` | String | Default username for cloning necessary GitHub repositories. |
| `default_repository_branch` | String | Default branch to checkout upon cloning repositories. |
| `default_image_release_tag` | String | Default container image tag to pull/use. |
| `max_reaches` | Integer | Maximum number of reaches to process. Must be >= 0. Defaults to 0. |
| `overwrite_run` | Boolean | If true, clears/overwrites an existing directory matching `run_name`. Defaults to false. |
| `clone_repos` | Boolean | If true, initiates cloning of required source repositories. |
| `container_platform` | String | Container execution platform. Currently limited to `"apptainer"`. |
| `submit_driver` | Boolean | If true, submits the primary driver script to Slurm (or, with `scheduler: "local"`, runs the modules right away). If false, only the scripts are written. |
| `apptainer_build_args` | List[String] | Extra flags for `apptainer build`, e.g. `["--ignore-subuid"]` on a host without a setuid starter or subuid mappings. Defaults to empty. |
| `build_tmpdir` | Path \| None | Scratch directory for `apptainer build` (several GB per image), for hosts where `/tmp` is too small or unusable. Defaults to Apptainer's own choice. |
| `modules_to_run` | List[String] | Array of module names scheduled for execution. **Constraint:** Each listed module must exist in `module_templates`. |
| `rebuild_all_modules` | Boolean | If true, triggers the rebuild processes for all modules. |
| `modules_to_rebuild` | List[String] | Array of specific module names designated for building/rebuilding. Defaults to empty. |
| `repo_branches` | Dictionary | Key-value pairs overriding the default repository branch for specific modules. Defaults to empty. |
| `image_release_tags` | Dictionary | Key-value pairs overriding `default_image_release_tag` for specific modules (e.g. `momma: "3.0.0"`). The base image largely determines the module version, so pin this alongside `repo_branches` when running an older release. Defaults to empty. |
| `local_repo_dirs` | Dictionary | Per-module local source directory (e.g. `mymodule: "/path/to/mymodule"`), copied into the run's `modules/` in place of a GitHub clone. Copied fresh whenever the module is (re)built. Defaults to empty. |
| `scheduler` | String | `"slurm"` (default) or `"local"` (see "Running without Slurm"). |
| `hpc` | HPC Object | Slurm settings. **Constraint:** Required when `scheduler` is `"slurm"`; ignored otherwise. |
| `local` | Local Object | Local scheduler settings (see Local Options). Only used when `scheduler` is `"local"`. |
| `monitor` | Monitor Object | Run gate / canary / monitor / audit settings (see Monitor Options). Defaults to enabled. |
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
| `concurrent_jobs` | Integer | Maximum number of array tasks running at once. Must be > 0. Can be changed while a run is going with `python -m confluence.monitor throttle` (see "Scaling a running run up or down"). |
| `reach_chunks` | Integer | Division parameter for processing reaches. Must be > 0. |

---

#### Local Options (`local`)

| Option | Type | Description |
| --- | --- | --- |
| `concurrent_jobs` | Integer \| None | Tasks running at once. Defaults to one per CPU core. Can be changed while a run is going with `python -m confluence.monitor throttle`. |
| `reach_chunks` | Integer | Index entries per task. Must be > 0. Default `1`. |
| `module_concurrency` | Dictionary | Per-module replacement for `concurrent_jobs`, for memory-hungry modules (e.g. `output: 4`). Defaults to empty. |

---

#### Monitor Options (`monitor`, all optional)

| Option | Type | Description |
| --- | --- | --- |
| `enabled` | Boolean | Check each module after every batch (and after its canary, if on); stop the run if a module is broken. Default `true`. |
| `watcher` | Boolean | Run the monitor inside the driver job (live `status.txt`, mid-array aborts) and queue the end-of-run audit job. Local runs have no watcher; this only switches their end-of-run audit. Default `true`. |
| `canary_tasks` | Integer | **Canary batch (off by default; see the tip under "Monitoring a run").** Size of the small test batch each per-reach module runs and is checked on before the rest, in array tasks (each task is `reach_chunks` reaches). `0` = off. `2` is a good value. A module is only canaried if it has at least 3× this many tasks. Default `0`. |
| `poll_seconds` | Integer | How often the monitor checks the run. Default `60`. |
| `modules` | Dictionary | Per-module settings, e.g. `validation: {gate: false}` turns off checks and the canary for one module. Keys: `gate`, `canary_tasks`, `abort_fail_frac`, `abort_coverage`, `warn_coverage`. |
| *Advanced (rarely needed)* | | |
| `min_units` / `min_units_floor` | Integer | Eligible units that must have finished before rate rules apply (never fewer than the floor). Defaults `30` / `5`. |
| `abort_fail_frac` / `warn_fail_frac` | Float | Failed share of eligible units that aborts / warns. Defaults `0.5` / `0.1`. |
| `fatal_min_units` / `fatal_frac` | Integer / Float | A fatal signature aborts once it is in this many units and this share of finished units. Defaults `3` / `0.2`. |
| `gate_probe_sample` | Integer | Outputs opened with netCDF4 per gate call. Default `60`. |
| `gate_wait_seconds` | Integer | How long the gate waits for unit files to appear over NFS. Default `60`. |
| `pending_fatal_minutes` | Integer | Pending with a fatal reason (`InvalidAccount`, ...) this long aborts. Default `10`. |
| `benign_patterns` / `fatal_patterns` | List[String] | Extra regexes, added to the built-in lists in `confluence/monitor/signatures.py`. |

---

#### Module Template Options (`ModuleTemplate`)


| Option | Type | Description |
| --- | --- | --- |
| `time` | String | Module-specific walltime limit (e.g., `"01:00:00"`). |
| `mem` | String | Module-specific memory allocation (e.g., `"16G"`). |
| `j2_file` | String | Path or filename of the Jinja2 template controlling module execution. |
| `cpus` | Integer | CPUs per array task of this module (`--cpus-per-task`; Slurm only). Default `1`. |
| `reach_chunks` | Integer | Index entries per array task for this module only, in place of `hpc.reach_chunks` (or `local.reach_chunks`). Defaults to the scheduler's `reach_chunks`. |
| `module_args` | Dictionary | Unvalidated key-value pairs for arbitrary, module-specific arguments. Defaults to empty. |





#### Module arguments worth knowing

- `prediagnostics.module_args.config_file`: path of a prediagnostics `config.R` that is bound over the image's own, to change the filter parameters (`GLOBAL_PARAMS`) without rebuilding the image.

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



