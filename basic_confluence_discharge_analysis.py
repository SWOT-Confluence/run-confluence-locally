#!/usr/bin/env python3
 
"""
SWOT Discharge Analysis Pipeline
Process SWOT discharge algorithms, calculate consensus, and generate visualizations
"""

import os
import json
import pandas as pd
import numpy as np
import datetime
import time
import pathlib
import sys
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import netCDF4 as nc
from datetime import timedelta
import seaborn as sns
from scipy.stats import spearmanr
import warnings
import argparse


# Color dictionary for algorithms
color_dict = {
    'sic4dvar': 'green',
    'momma': 'blue',
    'neobam': 'purple',
    'geobam': 'purple',
    'hivdi': 'deeppink',
    'metroman': 'orange',
    'sad': 'tomato',
    'gauge': 'black',
    'consensus': 'sienna'
}


def calc_cons(df):
    """Calculate simple median consensus across algorithms"""
    if 'consensus' not in df['algo'].unique():
        algo_Q_cons_values = (
            df[df['algo'] != 'gauge']
            .groupby(['reach_id', 'time'])['Q']
            .median()
            .reset_index()
        )
        algo_Q_cons_values['algo'] = 'consensus'
        df = pd.concat([df, algo_Q_cons_values], ignore_index=True)
    return df


def coeffVar(df, reach_list):
    """Calculate coefficient of variation for algorithms, consensus, and gauge"""
    filtered_df = df[df['reach_id'].isin(reach_list)]

    # Calculate CV for each algorithm
    grouped = filtered_df.groupby(['reach_id', 'algo']).agg(
        mean_algo_Q=('Q', 'mean'),
        sd_algo_Q=('Q', 'std')
    ).reset_index()

    grouped['CV'] = np.where(
        grouped['mean_algo_Q'] == 0,
        np.nan,
        grouped['sd_algo_Q'] / grouped['mean_algo_Q']
    )
    
    # Calculate CV for consensus
    grouped_cons = filtered_df[filtered_df['algo'] == 'consensus'].drop_duplicates(
        subset=['reach_id', 'time', 'Q']
    ).groupby(['reach_id']).agg(
        mean_cons_Q=('Q', 'mean'),
        sd_cons_Q=('Q', 'std')
    ).reset_index()    
    
    grouped_cons['CV_cons'] = grouped_cons['sd_cons_Q'] / grouped_cons['mean_cons_Q']

    # Calculate CV for gauge
    grouped_gauge = filtered_df[filtered_df['algo'] == 'gauge'].drop_duplicates(
        subset=['reach_id', 'time', 'Q']
    ).groupby(['reach_id']).agg(
        mean_gauge_Q=('Q', 'mean'),
        sd_gauge_Q=('Q', 'std')
    ).reset_index().dropna()
    
    grouped_gauge['CV_gauge'] = grouped_gauge['sd_gauge_Q'] / grouped_gauge['mean_gauge_Q']
    
    # Merge all CV calculations
    merged_df = pd.merge(grouped, grouped_cons, on='reach_id', how='left')
    final_df = pd.merge(merged_df, grouped_gauge, on='reach_id', how='left')
    
    return round(final_df, 3)


def append_coeffVar(dfs_q):
    """Append coefficient of variation to dataframes"""
    for label, df in dfs_q.items():
        if df.empty:
            continue

        coeff_df = coeffVar(df, df[df['algo']=='consensus'].reach_id.unique())

        df = df.merge(
            coeff_df[['reach_id', 'algo', 'CV', 'CV_cons', 'CV_gauge']],
            on=['reach_id', 'algo'], how='left'
        )

        dfs_q[label] = df

    return dfs_q


def plot_cdf_coeff(dfs_q, color_dict, algos_to_plot):
    """Plot the CDF of Coefficient of Variation for each DataFrame"""
    for label, df in dfs_q.items():
        if df.empty:
            continue

        plt.figure(figsize=(15, 8))

        for algo in algos_to_plot:
            algo_df = df[df['algo'] == algo]
            if algo_df.empty or 'CV' not in algo_df:
                continue

            coeff_var_algo_sorted = np.sort(algo_df['CV'].dropna())
            cdf = np.arange(1, len(coeff_var_algo_sorted) + 1) / len(coeff_var_algo_sorted)
            
            median_val = np.round(np.median(algo_df['CV']), 2)
            percentile_68 = np.round(np.percentile(algo_df['CV'], 68), 2)
            
            plt.plot(
                coeff_var_algo_sorted,
                cdf,
                label=f'{algo} (n={len(algo_df.reach_id.unique())}, 68_perc={percentile_68})',
                linewidth=6 if algo in ['consensus', 'gauge'] else 3,
                color=color_dict.get(algo, 'black'),
                linestyle='-.' if algo == 'gauge_swot_match' else '-'
            )

        plt.hlines(y=0.66, xmin=0, xmax=10, color='black', linestyle='--', linewidth=3)
        plt.xlabel(f'Coefficient of Variation ({label})', fontsize=30)
        plt.ylabel('Proportion', fontsize=30)
        plt.xticks(np.arange(0, 3.6, 0.25), fontsize=16, rotation=45)
        plt.yticks(fontsize=26, rotation=45)
        plt.gca().tick_params(axis='y', pad=15)
        plt.legend(loc='lower right', fontsize=26)
        plt.grid(True)
        plt.tight_layout()
        plt.xlim([0, 2.5])

        plt.show()


def find_nc_files(directory):
    """Find NetCDF result files in directory"""
    return [f for f in os.listdir(directory) if 'results' in f and f.endswith('.nc')]


def get_discharge(file_path, algorithm, variable, scaleLevel, reachList):
    """Generator to yield discharge rows for each reach_id in NetCDF file"""
    results = nc.Dataset(file_path)
    reach_ids = list(np.ma.getdata(results['reaches/reach_id'][:]))
    
    swot_ts = datetime.datetime(2000, 1, 1, 0, 0, 0)
    
    for idx, reach_id in enumerate(reach_ids):
        time = results['reaches']['time'][idx]
        missing = results['reaches/time'].missing_value
        
        if hasattr(time, '__len__'):
            nonmissing_indexes = np.where(time != missing)[0]
            time = time[nonmissing_indexes]
        else:
            time = np.array([])
            nonmissing_indexes = np.array([], dtype=int)
        
        if len(time) == 0:
            continue
            
        discharge_time = [(swot_ts + datetime.timedelta(seconds=st)).strftime("%Y-%m-%d") for st in time]
        
        discharge_q = results[algorithm][variable][idx]
        
        if hasattr(discharge_q, '__len__') and len(discharge_q) > 1:
            valid_indexes = [i for i in nonmissing_indexes if i < len(discharge_q)]
            discharge_q = discharge_q[valid_indexes]
            q_missing = results[algorithm][variable].missing_value
            discharge_q = np.where(discharge_q == missing, np.nan, discharge_q)
        else:
            discharge_q = np.full(len(time), np.nan)
        
        for q, t in zip(discharge_q, discharge_time):
            yield {'algo': algorithm, 'reach_id': reach_id, 'time': t, 'Q': q}
    
    results.close()


def process_and_save(input_dir, algorithms, variable_map, output_dir, scaleLevel, reach_list_json):
    """
    Process NetCDF files and save discharge data to CSV
    
    Parameters:
    - input_dir: Directory containing NetCDF files
    - output_dir: Directory where CSV files will be saved
    """
    # Load reach list from JSON file
    with open(reach_list_json, 'r') as f:
        reach_list = json.load(f)
    reach_list = [str(r) for r in reach_list]

    # Find NetCDF files in input directory
    nc_files = find_nc_files(input_dir)
    file_paths = [os.path.join(input_dir, file) for file in nc_files]
    print(f"Found {len(file_paths)} NetCDF files in {input_dir}")
    print(file_paths)

    for algorithm, variable in variable_map.items():
        # Save CSV to output_dir (not input_dir)
        output_file = os.path.join(output_dir, f"{algorithm}_q_{scaleLevel}.csv")

        with open(output_file, 'w') as f:
            for i, file_path in enumerate(file_paths, 1):
                print(f"Processing file {i}/{len(file_paths)}: {os.path.basename(file_path)}")
                rows = get_discharge(file_path, algorithm, variable, scaleLevel, reach_list)
                df = pd.DataFrame(rows)

                if not df.empty and 'Q' in df.columns:
                    df = df.dropna(subset=['Q'])
                    df.to_csv(f, mode='a', header=f.tell() == 0, index=False)

        print(f"Done processing {algorithm}, output saved to {output_file}")


def plot_discharge_with_metrics(df_metrics, divide_date, color_dict, run_dir):
    """Plot discharge timeseries with performance metrics"""
    print(df_metrics)

    df_metrics = df_metrics.sort_values('reach_id')
    
    for reach_id in df_metrics['reach_id'].unique():
        df_reach = df_metrics[df_metrics['reach_id'] == reach_id]
        df_reach = df_reach.sort_values('time')
        
        # Skip if gauge exists but has too few points
        if 'gauge' in df_reach['algo'].values and (df_reach['algo'] == 'gauge').sum() < 10:
            continue

        plt.figure(figsize=(10, 6))

        for algorithm in df_reach['algo'].unique():
            if algorithm == 'gauge':
                continue
            
            df_algo = df_reach[df_reach['algo'] == algorithm]

            discharge_time = pd.to_datetime(df_algo['time'])
            discharge_algo = df_algo['Q']

            color = color_dict.get(algorithm, 'black')
            
            if algorithm == 'consensus':
                plt.scatter(discharge_time, discharge_algo, alpha=1.0, label=f"{algorithm.upper()}", color=color, marker='X')
                plt.plot(discharge_time, discharge_algo, color=color, alpha=1.0, linewidth=2.5)
            else:
                plt.scatter(discharge_time, discharge_algo, alpha=0.3, label=f"{algorithm.upper()}", color=color)
                plt.plot(discharge_time, discharge_algo, color=color, alpha=0.3, linestyle='--')

        # Add metrics annotations for consensus
        df_algo_q_cons = df_reach[df_reach['algo'] == 'consensus']

        if not df_algo_q_cons.empty:
            required_metrics = ['r', 'NSE', 'KGE', 'RMSE', 'nRMSE', 'nBIAS', 'rRMSE', 'n']
            if all(col in df_algo_q_cons.columns for col in required_metrics):
                metrics = df_algo_q_cons.iloc[0]
                
                text_x = pd.to_datetime(df_algo_q_cons['time']).max() + timedelta(28)
                text_y = df_algo['Q'].min() + 2
                
                metrics_text = (
                    f"R: {metrics['r']:.2f}\n"
                    f"NSE: {metrics['NSE']:.2f}\n"
                    f"KGE: {metrics['KGE']:.2f}\n"
                    f"RMSE: {metrics['RMSE']:.2f}\n"
                    f"nRMSE: {metrics['nRMSE']:.2f}\n"
                    f"nBIAS: {metrics['nBIAS']:.2f}\n"
                    f"rRMSE: {metrics['rRMSE']:.2f}\n"
                    f"n: {metrics['n']}"
                )
                
                plt.text(text_x, text_y, metrics_text, fontsize=12, ha='left', va='bottom', color='black')
            else:
                print(f"Warning: Metrics columns not found for reach {reach_id}")

        plt.vlines(x=divide_date, ymin=0, ymax=df_reach.Q.max(), colors='black', linestyle='-')

        plt.suptitle(f"Discharge for Reach ID: {reach_id}", fontsize=18)
        plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=15)
        plt.xlabel('Date', fontsize=20)
        plt.ylabel('Discharge (m³/s)', fontsize=20)
        plt.xticks(rotation=45, fontsize=15)
        plt.yticks(fontsize=15)

        plt.tight_layout()
        plt.show()
        plt.savefig(f"{run_dir}/discharge_timeseries_reach_{reach_id}.png", dpi=300, bbox_inches='tight')
        plt.close()

def main(run_dir, input_dir, reaches, run_name='Dtest', divide_date='2023-07-11'):
    """Main pipeline for SWOT discharge analysis"""
    os.makedirs(run_dir, exist_ok=True)

    # Configuration
    nc_input_dir = input_dir  # Directory with NetCDF files
    output_dir = run_dir       # Directory for output CSVs
    devSet_reaches = reaches
    
    variable_map = {
        # 'neobam': 'q/q',
        'momma': 'Q',
        'sic4dvar': 'Q_da',
        'metroman': 'allq',
        # 'hivdi': 'Q',
        # 'sad': 'Qa',
        'consensus': 'consensus_q'
    }
    
    algos_to_plot = ['sic4dvar', 'momma', 'consensus', 'metroman']
    divide_date = pd.to_datetime(divide_date)
    
    # Process NetCDF files and extract discharge data
    print("Processing NetCDF files...")
    print(f"Input directory (NetCDF): {nc_input_dir}")
    print(f"Output directory (CSV): {output_dir}")
    process_and_save(nc_input_dir, variable_map.keys(), variable_map, output_dir, run_name, devSet_reaches)
    
    # Load and combine all algorithm CSVs
    print("\nLoading and combining algorithm outputs...")
    dfs = [
        pd.read_csv(os.path.join(output_dir, f"{algo}_q_{run_name}.csv")).dropna(subset=['Q'])
        for algo in variable_map.keys()
    ]
    df_all = pd.concat(dfs, ignore_index=True)
    
    df_all.to_csv(os.path.join(output_dir, f'all_q_{run_name}_consensusUAT.csv'), index=False)
    
    # Calculate median consensus
    print("\nCalculating median consensus...")
    df_all_cons = calc_cons(df_all[df_all['algo'] != 'consensus'])
    df_all_cons.to_csv(f'{output_dir}/all_q_{run_name}_consensusMedian.csv', index=False)
    
    # Calculate Coefficient of Variation
    print("\nCalculating coefficient of variation...")
    dfs_q = {run_name: df_all}
    dfs_q = append_coeffVar(dfs_q=dfs_q)
    
    # Save reach IDs
    print("\nSaving reach IDs...")
    reach_ids = dfs_q[run_name]['reach_id'].unique().astype(str).tolist()
    with open(f'{output_dir}/reach_ids.json', 'w') as f:
        json.dump(reach_ids, f, indent=2)
    print(f"Saved {len(reach_ids)} reach IDs to reach_ids.json")
    
    # Plot CDF of Coefficient of Variation
    print("\nPlotting CDF of coefficient of variation...")
    plot_cdf_coeff(
        dfs_q=dfs_q,
        color_dict=color_dict,
        algos_to_plot=algos_to_plot,
        run_dir=run_dir
    )
    
    # Plot discharge timeseries with metrics
    print("\nPlotting discharge timeseries...")
    plot_discharge_with_metrics(
        df_metrics=df_all,
        divide_date=divide_date,
        color_dict=color_dict
    )
    
    print("\nPipeline complete!")
    return dfs_q


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='SWOT Discharge Analysis Pipeline')
    parser.add_argument('--run_dir', type=str, required=True,
                        help='Output directory for CSV results')
    parser.add_argument('--input', type=str, required=True,
                        help='Input directory containing NetCDF SOS files')
    parser.add_argument('--reaches', type=str, required=True,
                        help='Path to reaches_of_interest.json file')
    parser.add_argument('--run_name', type=str, default='empty',
                        help='Run name label (default: empty)')
    parser.add_argument('--divide_date', type=str, default='2023-07-11',
                        help='Date to divide plots (format: YYYY-MM-DD, default: 2023-07-11)')
    
    args = parser.parse_args()
    
    dfs_q = main(
        run_dir=args.run_dir,
        input_dir=args.input,
        reaches=args.reaches,
        run_name=args.run_name,
        divide_date=args.divide_date
    )


# Inline notebook call (uncomment to run in Jupyter environment)
# import sys
# sys.path.append('/Users/elisafriedmann/Documents/confluence')

# # Import the main function from your script
# from basic_confluence_discharge_analysis import main

# # Set up matplotlib for inline plotting
# %matplotlib inline

# # Run the analysis
# dfs_q = main(
#     run_dir='/Users/elisafriedmann/Documents/confluence/confluence_test/analysis',
#     input_dir='/Users/elisafriedmann/Documents/confluence/confluence_test/test_mnt/output/sos',
#     reaches='/Users/elisafriedmann/Documents/confluence/confluence_test/test_mnt/input/reaches.json',
#     run_name='test',
#     divide_date='2023-07-11'
# )