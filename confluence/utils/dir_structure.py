import shutil
import tarfile
import zipfile
import subprocess as sp
from importlib import resources
from pathlib import Path

import yaml
import requests
from zenodo_get import download as zenodo_download

from confluence.utils.config import Config


def _create_directory_structure(run_dir: Path, mnt_dir: Path):
    # These get added to the cfg.dirs dictionary
    dir_list = ["log", "modules", "report", "sh_scripts", "sif"]

    # These paths are created as new directories but not added
    # to the dirs dictionary.
    mnt_dir_list = [
        "diagnostics",
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
        "logs",
        "moi",
        "offline",
        "output",
        "validation",
    ]

    dirs = {dir: (run_dir / dir) for dir in dir_list}
    for dir in dirs.values():
        dir.mkdir(mode=755, parents=True, exist_ok=True)
    dirs["run"] = run_dir
    dirs["mnt"] = mnt_dir

    for dir in mnt_dir_list:
        (mnt_dir / dir).mkdir(mode=755, parents=True, exist_ok=True)

    sp.call(['chmod', '-R', '755', str(run_dir)])

    return dirs


def _copy_nc_files(source_dir: Path, target_dir: Path, expected_n: int):
    # Copy files if they were configd and exist
    source_files = list(Path(source_dir).glob("*.nc"))
    if len(source_files) == expected_n:
        for file in source_files:
            print(f"Copying {file.name}")
            shutil.copy2(str(file), str(target_dir / file.name))
        return
    elif len(source_files) > 0:
        raise ValueError(
            f"Expected {expected_n} files in priors dir but found {len(source_files)}\n{source_files}"
        )


def _copy_or_download_sos(cfg: Config):
    sos_dir = cfg.dirs["mnt"] / "input" / "sos"

    # Copy files if they were configd and exist
    if cfg.priors_copy_dir is not None:
        _copy_nc_files(cfg.priors_copy_dir, sos_dir, 6)
        return

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

    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts
            # Bypass the root directory to extract directly into sos_dir
            if len(parts) > 1:
                member.name = str(Path(*parts[1:]))
                tar.extract(member, path=sos_dir)
    archive_path.unlink()


def _copy_or_download_sword(cfg: Config):
    sword_dir = cfg.dirs["mnt"] / "input" / "sword"

    # Copy files if they were configd and exist
    if cfg.sword_copy_dir is not None:
        _copy_nc_files(cfg.sword_copy_dir, sword_dir, 6)
        return

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

    with zipfile.ZipFile(archive_path, "r") as z:
        for member in z.infolist():
            parts = Path(member.filename).parts
            # Bypass the root directory to extract directly into sword_dir
            if len(parts) > 1:
                member.filename = str(Path(*parts[1:]))
                z.extract(member, path=sword_dir)
    archive_path.unlink()


def _copy_or_download_svs(cfg: Config):
    svs_dir = cfg.dirs["mnt"] / "validation"

    # Copy files if they were configd and exist
    if cfg.svs_copy_dir is not None:
        _copy_nc_files(cfg.svs_copy_dir, svs_dir, 1)
        return

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
        raise FileNotFoundError(
            f"Filename {target_version} not found in SVS repository."
        )

    download_url = f"{base_url}/api/access/datafile/{file_id}"

    response = requests.get(download_url, stream=True)
    response.raise_for_status()
    with open(svs_dir / target_version, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)


def setup_dirs(cfg: Config):
    run_dir = cfg.root_dir.resolve() / f"confluence_{cfg.run_name}"
    mnt_dir = run_dir / f"{cfg.run_name}_mnt"

    if cfg.overwrite_run and run_dir.is_dir():
        print("Removing existing directory before running")
        shutil.rmtree(run_dir)

    dir_dict = _create_directory_structure(run_dir, mnt_dir)
    cfg.dirs = dir_dict

    shutil.copy2(cfg.roi_file, mnt_dir / "input" / "reaches_of_interest.json")

    continent_path = resources.files("confluence") / "continent.json"
    shutil.copy2(continent_path, mnt_dir / "input" / "continent.json")

    # _copy_or_download_sos(cfg)
    # _copy_or_download_sword(cfg)
    # _copy_or_download_svs(cfg)

    out_path = run_dir / "config.yml"
    with open(out_path, "w") as outfile:
        yaml.dump(cfg, outfile)

    return cfg
