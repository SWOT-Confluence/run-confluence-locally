# Install
Create a python environment using uv OR conda:
## uv
```bash
uv sync
```
## conda
```bash
conda create -n confluence-env python=3.11 pip -c conda-forge -y
conda activate confluence-env
pip install -e .
```

# Run
1. Modify the configuration .yml file 
1. ``` python run.py <path/to/config_file.yml>```
1. If you have config'd `submit_driver: True` the slurm driver script will automatically be submitted to sbatch. Otherwise, the driver script path will be printed for you to take a look. To run it, call ```sbatch <path/to/driver_script.sh>```


##### Choose reaches to process
  6. Edit xxx_mnt/input/reaches_of_interest.json to be a list of reaches you want to process. Leave it as it is to target the devset. 

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



