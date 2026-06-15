import re
import shutil
import subprocess as sp
import tempfile
from importlib import resources
from pathlib import Path

import requests
import yaml
from zenodo_get import download as zenodo_download

from confluence.utils.config import Config


def _create_directory_structure(run_dir: Path, mnt_dir: Path):
    # These get added to the cfg.dirs dictionary
    dir_list = ["log", "modules", "report", "sh_scripts", "sif"]

    # These paths are created as new directories but not added
    # to the dirs dictionary.
    mnt_dir_list = [
        "diagnostics/prediagnostics",
        "diagnostics/postdiagnostics/basin",
        "diagnostics/postdiagnostics/reach",
        "flpe/busboi",
        "flpe/hivdi",
        "flpe/metroman/sets",
        "flpe/momma",
        "flpe/sad",
        "flpe/consensus",
        "flpe/sic4dvar",
        "input/sos",
        "input/sword",
        "input/swot",
        "input/svs",
        "logs",
        "moi",
        "offline",
        "output/sos",
        "validation/figs",
        "validation/stats",
    ]

    dirs = {dir: (run_dir / dir) for dir in dir_list}
    for dir in dirs.values():
        dir.mkdir(mode=755, parents=True, exist_ok=True)
    dirs["run"] = run_dir
    dirs["mnt"] = mnt_dir
    dirs["input"] = mnt_dir / "input"

    for dir in mnt_dir_list:
        (mnt_dir / dir).mkdir(mode=755, parents=True, exist_ok=True)

    sp.run(["chmod", "-R", "755", str(run_dir)])

    return dirs


def strip_sword_version_letters(target_dir: Path, target_version: str):
    # Group 1: prefix up to 'v'
    # Group 2: numeric version
    # [a-zA-Z]: exactly one alphabetical character (dropped from new name)
    # Group 3: remaining suffix ending with '.nc'
    pattern = re.compile(r"(.*v)(\d+)[a-zA-Z](.*\.nc)$")

    for file_path in target_dir.glob("*.nc"):
        match = pattern.match(file_path.name)
        if match:
            prefix, version, suffix = match.groups()

            # Make sure this file is actually our base version
            if version != str(target_version):
                continue

            new_name = f"{prefix}{version}{suffix}"
            if file_path.name != new_name:
                new_path = file_path.with_name(new_name)
                file_path.rename(new_path)
                print(f"Renamed: {file_path.name} -> {new_name}")


def _copy_nc_files(source_dir: Path, target_dir: Path, expected_n: int):
    # Copy files if they were configd and exist
    source_files = list(Path(source_dir).glob("*.nc"))
    copied_files = []
    if len(source_files) == expected_n:
        for file in source_files:
            print(f"Copying {file.name}")
            shutil.copy2(str(file), str(target_dir / file.name))
            copied_files.append(target_dir / file.name)
        return copied_files
    else:
        raise ValueError(f"Expected {expected_n} files in priors dir but found {len(source_files)}\n{source_files}")


def _copy_or_download_sos(cfg: Config):
    sos_dir = cfg.dirs["input"] / "sos"

    if cfg.priors_bind_dir:
        print(f"SOS prior files will be bound from: {cfg.priors_bind_dir}")
        return
    if cfg.priors_copy_dir:
        _copy_nc_files(cfg.priors_copy_dir, sos_dir, 6)
    elif cfg.priors_zenodo_doi:
        zenodo_download(
            record_or_doi=cfg.priors_zenodo_doi,
            output_dir=sos_dir,
            file_glob="*.tar.gz",
            verbosity=1,
        )
        archives = list(sos_dir.glob("*.tar.gz"))
        if len(archives) > 1:
            raise RuntimeError("More than 1 .tar.gz found for priors.")
        elif len(archives) == 0:
            raise RuntimeError("No .tar.gz file found for priors.")
        archive_path = archives[0]

        cmd = ["tar", "--strip-components=1", "-xzf", str(archive_path), "-C", str(sos_dir)]
        sp.run(cmd, check=True)
        archive_path.unlink()

    else:
        # print(
        #     f"Unspecified source of SOS data. This is ok if they already exist in {cfg.dirs['mnt'].stem}."
        # )
        return

    strip_sword_version_letters(sos_dir, cfg.sword_version)


def _copy_or_download_sword(cfg: Config):
    sword_dir = cfg.dirs["input"] / "sword"

    if cfg.sword_bind_dir:
        print(f"SWORD files will be bound from: {cfg.sword_bind_dir}")
        return
    if cfg.sword_copy_dir is not None:
        _copy_nc_files(cfg.sword_copy_dir, sword_dir, 6)
    elif cfg.sword_zenodo_doi is not None:
        zenodo_download(
            record_or_doi=cfg.sword_zenodo_doi,
            output_dir=sword_dir,
            file_glob="*_netcdf.zip",
            verbosity=1,
        )
        archives = list(sword_dir.glob("*_netcdf.zip"))
        if len(archives) > 1:
            raise RuntimeError("More than 1 *_netcdf.zip found for priors.")
        elif len(archives) == 0:
            raise RuntimeError("No *_netcdf.zip file found for priors.")
        archive_path = archives[0]

        with tempfile.TemporaryDirectory() as tmpdir:
            sp.run(
                ["unzip", "-q", str(archive_path), "-d", tmpdir],
                check=True,
            )

            root_dir = next(Path(tmpdir).iterdir())

            shutil.copytree(
                root_dir,
                sword_dir,
                dirs_exist_ok=True,
            )

        archive_path.unlink()
    else:
        # print(
        #     f"Unspecified source of SOS data. This is ok if they already exist in {cfg.dirs['mnt'].stem}."
        # )
        return

    strip_sword_version_letters(sword_dir, cfg.sword_version)


def _copy_or_download_svs(cfg: Config):
    # svs file name does not include sword version number so renaming not needed.
    svs_dir = cfg.dirs["input"] / "svs"

    if cfg.svs_copy_dir is not None:
        copied_files = _copy_nc_files(cfg.svs_copy_dir, svs_dir, 1)
        return copied_files[0]
    elif cfg.svs_repo_filename is not None:
        target_version = cfg.svs_repo_filename
        dataset_doi = "doi:10.18419/DARUS-5843"
        base_url = "https://darus.uni-stuttgart.de"
        api_url = f"{base_url}/api/datasets/:persistentId/?persistentId={dataset_doi}"
        response = requests.get(api_url)
        response.raise_for_status()

        data = response.json()
        files = data.get("data", {}).get("latestVersion", {}).get("files", [])

        file_id = None
        for f in files:
            data_file = f.get("dataFile", {})
            if data_file.get("filename") == target_version:
                file_id = data_file.get("id")
                break

        if file_id is None:
            raise FileNotFoundError(f"Filename {target_version} not found in SVS repository.")

        download_url = f"{base_url}/api/access/datafile/{file_id}"

        response = requests.get(download_url, stream=True)
        response.raise_for_status()
        with open(svs_dir / target_version, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        return svs_dir / cfg.svs_repo_filename
    else:
        # try to find the SVS file so that we can return path for validation module
        svs_files = list((cfg.dirs["input"] / 'validation').glob('*SVS*.nc'))
        if len(svs_files) == 0:
            raise RuntimeError("Could not find the SVS file in the validation directory.")
        elif len(svs_files) > 1: 
            raise RuntimeError("Found multiple files matching *SVS*.nc name pattern in the validation directory.")
        
        return svs_files[0]


def setup_dirs(cfg: Config):
    run_dir = cfg.root_dir.resolve() / f"confluence_{cfg.run_name}"
    mnt_dir = run_dir / f"{cfg.run_name}_mnt"

    if cfg.overwrite_run and run_dir.is_dir():
        print("Removing existing directory before running")
        try:
            shutil.rmtree(run_dir)
        except Exception as e:
            print(f"Failed to remove the existing run directory, likely due to open file handles: {e}")
            raise

    dir_dict = _create_directory_structure(run_dir, mnt_dir)
    cfg.dirs = dir_dict

    shutil.copy2(cfg.roi_file, mnt_dir / "input" / "reaches_of_interest.json")

    source_path = resources.files("confluence") / "continent.json"
    dest_path = mnt_dir / "input" / "continent.json"
    # this file changes after non_expanded_setfinder and we don't want to overwrite it
    # if we are restarting a run.
    if not dest_path.is_file():
        shutil.copy2(source_path, dest_path)

    _copy_or_download_sos(cfg)
    _copy_or_download_sword(cfg)
    svs_path = _copy_or_download_svs(cfg)

    cfg.module_templates['validation'].module_args['svs_filename'] = str(svs_path.name)

    out_path = run_dir / "config.yml"
    with open(out_path, "w") as outfile:
        yaml.dump(cfg, outfile)

    return cfg
