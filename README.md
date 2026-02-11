# REQUIREMENTS

* docker installed locally - somewhere where you have sudo priveledges to the point where "docker --version" completes successfully (Install: https://docs.docker.com/engine/install/)
* Docker Desktop on your local computer and a dockerhub or GitHub account (free, for HPC users)
* singularity or apptainer (HPC)
* a python environment

# INSTRUCTIONS
These instructions provide an overall description for running confluence and supplement either the local or HPC notebook. In parentheses I indicate where to run the associated functions or where to place downloaded folders. For best results, choose either the HPC or local .ipynb notebook and then follow along. If running on an HPC, the notebook can exist in a local directory for the first few steps to use docker (same as local) and then build scripts in HPC, or can exist in HPC for entire workflow if building from GitHub container registry.  

### Access Confluence Modules and Scripts (LOCAL)
1. Fork (locally, usually main branch) confluence modules - see bottom table for correct name/branch 
  i.e. git clone https://github.com/SWOT-Confluence/prediagnostics (many more modules explained at end of docs)

2. Fork scripts to run confluence modules
   i.e. git clone https://github.com/SWOT-Confluence/run-confluence-locally.git

3. Use notebook provided or terminal to clone branches needed into a modules directory on machine
4. Use notebook provided or terminal to rename confluence_empty to confluence_xxx and empty_mnt one level below to xxx_mnt so that 'xxx' is the same as parent confluence_xxx folder tag name
   
### Prepare Confluene Module Images using Docker (LOCAL)
3. Run the "Prepare Images Locally" section of this notebook locally
    - Use the notebook or terminal to edit the run name (directory/module/tag name to point any customizations and specific run). Best if it is the same 'xxx' name as the confluence run if testing multiple changes
    - edit the modules to include or exclude depending on needs
         **NOTE** setfinder and combine_data are run twice as 'expanded_*' and 'non_expanded_*', but they are only built in docker once
    - edit the script_jobs and command dict names to correctly reflect your modules of interest
    - This can take some time initially to build in docker
    - You can check your work in Docker Desktop (you should see images appearing in your docker account)

### Create Confluence Folder Structure where confluence results will live (LOCAL OR HPC)
4. Download empty directory structure. Notebook is tailored to this structure

  (pip or conda) install gdown  
  gdown 16FdIV0xyaQaNfvxR7OJ_p8ljaI9gv1pu  
  tar -xzvf {whichever tar.gz you downloaded} and then rename to confluence_xxx  

Here is an example of the initial folder structure

confluence_xxx  
├── modules
├── empty_xxx  
│   ├── diagnostics  
│   ├── flpe  
│   │   ├── geobam  
│   │   ├── hivdi  
│   │   ├── metroman  
│   │   │   └── sets  
│   │   ├── momma  
│   │   ├── sad  
│   │   ├── consensus 
│   │   └── sic4dvar  
│   ├── input  
│   │   └── reaches_of_interest.json  
│   ├── logs  
│   ├── mnt_dirs.sh  
│   ├── moi  
│   ├── offline  
│   ├── output  
│   └── validation  
├── sh_scripts
├── report  
└── sif  

##### Choose reaches to process
  6. Edit xxx_mnt/input/reaches_of_interest.json to be a list of reaches you want to process. Leave it as it is to target the devset. 
      NOTE: ***HIGHLY SUGGESTED FOR FIRST RUN*** Priors takes a long time, if you do not need to build it, replace with the latest .nc priors files (one per continent) in /xxx_mnt/input/sos/priors/


### Run Confluence (HPC)
3. Run the HPC notebook to create SLURM submission scripts for each module
- specify job details that match your HPC or node limitations 
- creates sif (singularity/apptainer) and sh_script (runs modules) and report (metadata and error) directories 

7. Run the Confluence Driver Script Generator section of this notebook on your HPC to create a SLURM submission script that runs each of the modules one by one (the one click run)
   - sbatch slurm_driver.sh

### Run Confluence (LOCAL)
6. Run the 'run_confluence_docker_local.ipynb' sections to create a local .py scripts to run individual modules and/or a .sh script that will run with docker to execute multiple in your terminal


### MAKE CHANGES TO CONFLUENCE 
Testing and Development:
- Create and run a mnt through input or prediagnostics as a base
- Create venv or alter docker command in command_dict to run certain module scripts, inputs point to base mnt, output to new directory (symlink is helpful here)

Scaling and Containerizing:
- Copy or clone module 
- Make changes to the code
- Load image to docker with either new name or new tag - again, helpful to name the confluence run same as the tag 
- Build image with the specific tag 
- Run confluence 


### Results and Reminders
1. Modules MUST be run in serial and are dependent on each other (algorithm modules can be run in any order within the larger sequence)
2. Thus, any change to an early module or reaches_of_interest.json requires an entirely new confluence directory /mnt creation
3. Results for setfinder through combine_data can be found in xxx_mnt/input/, hydrocron data can be found in xxx_mnt/input/swot/, prediagnostics in xxx_mnt/diagnostics, each algo results as format *reach_id*_*algo*.nc in xxx_mnt/flpe/*algo*, all results collected as .nc files by continent in xxx_mnt/ouptut/
4. To parse and organize discharge data, see 
    PO.DAAC cookbook for working with SOS:
    https://podaac.github.io/tutorials/notebooks/datasets/SWOT_L4_DAWG_SOS_DISCHARGE.html#navigating-reaches-and-nodes
    
    Github Repo:
    https://github.com/SWOT-Confluence/confluence-post-run-tools/tree/main

### Module Descriptions (table)

| Module                              | Git Branch          | Number of Jobs / Reaches           | Description                                                                                                                                           |
|-------------------------------------|---------------------|------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| Expanded Setfinder (setfinder)                  | main                | 7                                  | Creates sets (groups of connected reaches) starting with your reaches of interest and looking up and down the river                                    |
| Expanded Combine Data (combine_data)               | main                | 1                                  | Combines the files generated in the setfinder into continent level data                                                                                |
| Input                               | input_D_products                | Number of Reaches                  | Pulls reach data from hydrocron and stores them in netcdfs, outputs to `/mnt/input/swot`                                                               |
| Non-Expanded Setfinder (setfinder)                          | main                | 7                                  | Creates sets (groups of connected reaches) only using the reaches that were pulled successfully using Input                                            |
| Non-Expanded Combine Data (combine_data)                        | main                | 1                                  | Combines files generated in the setfinder into continent level data                                                                                    |
| Prediagnostics                      | main                | Number of Reaches                  | Filters reach data netcdfs based on a series of bitwise filters and outlier detectors. **OVERWRITES NETCDFS**                                          |
| Priors                              | main                | 7                                  | Pulls gauge data from external gauge agencies and builds the prior database (Priors SoS) which is eventually hosted on PO.DAAC                         |
| Metroman                            | main                | Number of Sets in `metrosets.json` | Runs the metroman FLPE algorithm, outputs to `/mnt/flpe/metroman/sets`                                                                                 |
| Metroman Consolidation              | main                | Number of Reaches                  | Takes the set level results of metroman and turns them into individual files, outputs to `/mnt/flpe/metroman`                                          |
| Momma, Neobam, SAD, Sic4dvar        | main, main, main    | Number of Reaches                  | Runs the corresponding FLPE algorithm                                                                                                                  |
| MOI                                 | main                | Number of basins in `basins.json`  | Combines FLPE algorithm results (not currently working because of SWORD v16 topology issues)                                                           |
| Offline                             | main                | Number of Reaches                  | Runs NASA SDS's discharge algorithm                                                                                                                    |
| Validation                          | main                | Number of Reaches                  | If there is a validation gauge on the reach then summary stats are produced. (All gauges are validation in unconstrained runs)                          |
| Output                              | add-sword-version                | 7                                  | Outputs results netcdf files that store all previous results data, outputs to `/mnt/output/sos`                                                        |



