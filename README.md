# REQUIREMENTS

* docker installed locally - somewhere where you have sudo priveledges to the point where "docker --version" completes successfully (Install: https://docs.docker.com/engine/install/)
* a dockerhub account (free)
* singularity or apptainer installed on your HPC
* a python environment

# INSTRUCTIONS

### Access Confluence Modules and Scripts (LOCAL)
1. Fork and clone (locally, usually main branch) confluence modules - see bottom table for correct name/branch 
  i.e. git clone https://github.com/SWOT-Confluence/prediagnostics (many more modules explained at end of docs)

2. Fork and clone scripts to run confluence modules
   i.e. git clone https://github.com/SWOT-Confluence/run-confluence-locally.git

### Prepare Confluene Module Images using Docker (LOCAL)
3. Run the "Prepare Images Locally" section of this notebook locally
    - edit the run name (directory/module/tag name to point any customizations and specific run). Best if it is the same 'xxx' name as the confluence run if testing multiple changes
    - edit the modules to include or exclude depending on needs
    - This can take some time initially

### Create Confluence Folder Structure where confluence results will live (LOCAL OR HPC)
4. Download empty directory structure

  (pip or conda) install gdown
  gdown 16FdIV0xyaQaNfvxR7OJ_p8ljaI9gv1pu
  tar -xzvf {whichever tar.gz you downloaded} and then rename to confluence_xxx

##### Customize confluence run
  5. Rename empty_mnt one level below to xxx_mnt so that 'xxx' is the same as parent confluence_xxx folder tag name
  
##### Choose reaches to process
  6. Edit xxx_mnt/input/reaches_of_interest.json to be a list of reaches you want to process. Leave it as it is to target the devset. 
      NOTE: ***HIGHLY SUGGESTED FOR FIRST RUN*** Priors takes a long time, if you do not need to build it, replace with the latest .nc priors files (one per continent) in /xxx_mnt/input/sos/priors/


### Run Confluence (HPC)
6. Run the "run_confluence_singularity_hpc" sections on your HPC that create SLURM submission scripts for each module
(setup and run run-confluence-locally/run_confluence_HPC.ipynb to generate a submission script that defines job details)
- creates sif (singularity) and sh_script (runs modules) and report (metadata and error) directories 

7. Run the Confluence Driver Script Generator section of this notebook on your HPC to create a SLURM submission script that runs each of the modules one by one (the one click run)
   - sbatch cfl_SLURM_wrapper.sh

### Run Confluence (LOCAL)
6. Run the 'run_confluence_docker_local.ipynb' sections to create a local .py scripts to run individual modules and/or a .sh script that will run with docker to execute multiple in your terminal


### MAKE CHANGES TO CONFLUENCE 
- Copy or clone module (local)
- Make changes to the code (local)
- Load image to docker with either new name or new tag - again, helpful to name the confluence run same as the tag (LOCAL)
- Build image with the specific tag (LOCAL)
- Run confluence (LOCAL or HPC) 


### Results and Reminders
1. Modules MUST be run in serial and are dependent on each other (algorithm modules can be run in any order within the larger sequence)
2. Thus, any change to an early module or reaches_of_interest.json requires an entirely new confluence directory _mnt creation
3. Results for setfinder through combine_data can be found in xxx_mnt/input/, hydrocron data can be found in xxx_mnt/input/swot/, prediagnostics in xxx_mnt/diagnostics, each algo results as format *reach_id*_*algo*.nc in xxx_mnt/flpe/*algo*, all results collected as .nc files by continent in xxx_mnt/ouptut/
4. To parse and organize discharge data, see 
    PO.DAAC cookbook for working with SOS:
    https://podaac.github.io/tutorials/notebooks/datasets/SWOT_L4_DAWG_SOS_DISCHARGE.html#navigating-reaches-and-nodes
    
    Github Repo:
    https://github.com/SWOT-Confluence/confluence-post-run-tools/tree/main

### Module Descriptions (table)

| Module                              | Git Branch          | Number of Jobs / Reaches           | Description                                                                                                                                           |
|-------------------------------------|---------------------|------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| Expanded Setfinder                  | main                | 7                                  | Creates sets (groups of connected reaches) starting with your reaches of interest and looking up and down the river                                    |
| Expanded Combine Data               | main                | 1                                  | Combines the files generated in the setfinder into continent level data                                                                                |
| Input                               | main                | Number of Reaches                  | Pulls reach data from hydrocron and stores them in netcdfs, outputs to `/mnt/input/swot`                                                               |
| Setfinder                           | main                | 7                                  | Creates sets (groups of connected reaches) only using the reaches that were pulled successfully using Input                                            |
| Combine Data                        | main                | 1                                  | Combines files generated in the setfinder into continent level data                                                                                    |
| Prediagnostics                      | main                | Number of Reaches                  | Filters reach data netcdfs based on a series of bitwise filters and outlier detectors. **OVERWRITES NETCDFS**                                          |
| Priors                              | main                | 7                                  | Pulls gauge data from external gauge agencies and builds the prior database (Priors SoS) which is eventually hosted on PO.DAAC                         |
| Metroman                            | main                | Number of Sets in `metrosets.json` | Runs the metroman FLPE algorithm, outputs to `/mnt/flpe/metroman/sets`                                                                                 |
| Metroman Consolidation              | main                | Number of Reaches                  | Takes the set level results of metroman and turns them into individual files, outputs to `/mnt/flpe/metroman`                                          |
| Momma, Neobam, SAD, Sic4dvar        | main, main, main    | Number of Reaches                  | Runs the corresponding FLPE algorithm                                                                                                                  |
| MOI                                 | main                | Number of basins in `basins.json`  | Combines FLPE algorithm results (not currently working because of SWORD v16 topology issues)                                                           |
| Offline                             | main                | Number of Reaches                  | Runs NASA SDS's discharge algorithm                                                                                                                    |
| Validation                          | main                | Number of Reaches                  | If there is a validation gauge on the reach then summary stats are produced. (All gauges are validation in unconstrained runs)                          |
| Output                              | main                | 7                                  | Outputs results netcdf files that store all previous results data, outputs to `/mnt/output/sos`                                                        |



