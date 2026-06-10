from pathlib import Path

import numpy as np
import pandas as pd
import netCDF4 as nc

from confluence.utils.config import Config

CONVENTION_MAP = {
    "metroman": "allq",
    "busboi": "Q",
    "hivdi": "Q",
    "momma": "Q",
    "sic4dvar": "Q_da",
    "consensus": "consensus_q",
}

ALGOS = ["hivdi", "metroman", "momma", "busboi", "sic4dvar", "consensus"]

METRICS = ["nse", "rsq", "kge", "rmse", "testn", "nrmse", "nbias", "spearmanr", "sige"]


def write_validation(output_dir: Path):
    cont_dfs = []
    for sos_output_file in (output_dir / "sos").glob("*.nc"):
        ds = nc.Dataset(sos_output_file)
        grp_ds = ds["validation"]["flpe"]

        gage_ids = nc.chartostring(grp_ds["gageid"][:]).astype(str)
        algo_names = nc.chartostring(grp_ds["algo_names"][0]).astype(str)
        algo_names = [n for n in algo_names if n != ""]

        multi_cols = pd.MultiIndex.from_product(
            [algo_names, METRICS], names=["algorithm", "statistic"]
        )

        reach_ids = ds["reaches"]["reach_id"][:].filled()
        df = pd.DataFrame(index=gage_ids, columns=multi_cols)
        df.index.name = "gage_id"

        for j, algo in enumerate(algo_names):
            # df[:, (algo, 'reach_id')] = np.nan
            for metric in METRICS:
                try:
                    data = grp_ds[metric][:, j]
                except IndexError as e:
                    # print(f"{metric} not found in {algo} output files")
                    pass
                if np.ma.isMaskedArray(data):
                    data = data.filled(np.nan).astype(float)

                df.loc[:, (algo, metric)] = data

            # Set the testn to nan if it was not tested...
            df.loc[df[(algo, "nse")].isna(), (algo, "testn")] = np.nan

        ds.close()

        # df = df[df.index.str.strip() != '']
        df = df.dropna(axis=0, how="all")
        cont_dfs.append(df)

    df = pd.concat(cont_dfs)
    df.to_parquet(output_dir / "dataframes" / "validation_stats.parquet")


def write_hydrographs(svs_file: Path, output_dir: Path, sword_version: str):
    svs = nc.Dataset(svs_file)
    svs_reaches = list(svs[f"reach_id_v{sword_version}"][:].filled())
    svs_days = svs["time"][:].filled(np.nan)
    svs_days = pd.to_datetime(svs_days, unit="D", origin="2023-01-01").normalize()

    sos_files = list((output_dir / "sos").glob("*SOS_results*.nc"))
    all_dfs = {algo: [] for algo in ALGOS}
    for sos_file in sos_files:
        ds = nc.Dataset(sos_file)

        sos_reaches = list(ds["reaches"]["reach_id"][:].filled())
        svs_sos_reach = np.intersect1d(sos_reaches, svs_reaches)

        for reach_id in svs_sos_reach:
            svs_reach_index = svs_reaches.index(reach_id)
            q_obs = svs["Q"][svs_reach_index, :].filled(np.nan)
            svs_df = pd.DataFrame(index=svs_days, data={"q_obs": q_obs})
            # removes the blank spaces that nc.chartostring does not remove for some reason.
            station_id = (
                svs["station_id"][:, svs_reach_index, 0]
                .compressed()
                .tobytes()
                .decode("utf-8")
                .strip()
            )

            sos_reach_index = sos_reaches.index(reach_id)
            raw_reach_time = ds["reaches"]["time"][sos_reach_index]
            sos_time_mask = raw_reach_time > 0
            reach_time = pd.to_datetime(
                raw_reach_time[sos_time_mask], unit="s", origin="2000-01-01"
            ).normalize()
            for algo in ALGOS:
                algo_Q = ds[algo][CONVENTION_MAP[algo]][sos_reach_index]

                if all(algo_Q < 0):
                    continue

                algo_Q = algo_Q[sos_time_mask]
                valid_mask = algo_Q > 0

                sos_df = pd.DataFrame(
                    index=reach_time[valid_mask],
                    data={"station_id": station_id, "q_pred": algo_Q[valid_mask]},
                )
                validation_df = sos_df.join(svs_df).dropna()
                all_dfs[algo].append(validation_df)
        ds.close()
    svs.close()

    for algo, df_list in all_dfs.items():
        df = pd.concat(df_list)
        df.to_parquet(output_dir / "dataframes" / f"{algo}_timeseries.parquet")


def write_parquets(cfg: Config):
    write_validation(cfg.dirs["mnt"] / "output")

    svs_file = list((cfg.dirs["mnt"] / "validation").glob("*.nc"))[0]
    write_hydrographs(svs_file, cfg.dirs["mnt"] / "output", cfg.sword_version)
