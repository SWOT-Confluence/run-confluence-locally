import shutil
import tarfile
import zipfile
from importlib import resources
from pathlib import Path

import yaml
import requests
from zenodo_get import download as zenodo_download


def _create_directory_structure(run_dir: Path, mnt_dir: Path):
    dir_list = ["log", "modules", "report", "sh_scripts", "sif"]

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
        dir.mkdir(parents=True, exist_ok=True)
    dirs["run"] = run_dir
    dirs["mnt"] = mnt_dir

    for dir in mnt_dir_list:
        (mnt_dir / dir).mkdir(parents=True, exist_ok=True)

    return dirs


def _copy_or_download_sos(cfg: dict):
    # Recast as path just for typing. pydantic would fix this.
    sos_dir = Path(cfg["dirs"]["mnt"]) / "input" / "sos"

    # Copy files if they were configd and exist
    source_dir = cfg.get("priors_copy_dir", None)
    if source_dir is not None:
        source_files = list(Path(source_dir).glob("*.nc"))
        if len(source_files) == 6:
            for file in source_files:
                print(f"Copying {file.name}")
                shutil.copy2(str(file), str(sos_dir / file.name))
            return
        elif len(source_files) > 0:
            raise ValueError(
                f"Expected 6 files in priors dir but found {len(source_files)}"
            )

    zenodo_download(
        record_or_doi=cfg["priors_zenodo_doi"],
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


def _copy_or_download_sword(cfg: dict):
    sword_dir = Path(cfg["dirs"]["mnt"]) / "input" / "sword"

    # Copy files if they were configd and exist
    source_dir = cfg.get("sword_copy_dir", None)
    if source_dir is not None:
        source_files = list(Path(source_dir).glob("*.nc"))
        if len(source_files) == 6:
            for file in source_files:
                print(f"Copying {file.name}")
                shutil.copy2(str(file), str(sword_dir / file.name))
            return
        elif len(source_files) > 0:
            raise ValueError(
                f"Expected 6 files in sword dir but found {len(source_files)}"
            )

    zenodo_download(
        record_or_doi=cfg["sword_zenodo_doi"],
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


def _copy_or_download_svs(cfg: dict):
    svs_dir = Path(cfg["dirs"]["mnt"]) / "validation"

    # Copy files if they were configd and exist
    source_dir = cfg.get("svs_copy_dir", None)
    if source_dir is not None:
        source_files = list(Path(source_dir).glob("*.nc"))
        if len(source_files) == 1:
            file = source_files[0]
            print(f"Copying {file.name}")
            shutil.copy2(str(file), str(svs_dir / file.name))
            return
        elif len(source_files) > 0:
            raise ValueError(
                f"Expected 1 file in svs dir but found {len(source_files)}"
            )

    target_version = cfg["svs_repo_filename"]
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
    with open(svs_dir / target_version, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)


def setup_dirs(cfg: dict):
    run_dir = Path(cfg["root_dir"]).resolve() / f"confluence_{cfg['run_name']}"
    mnt_dir = run_dir / f"{cfg['run_name']}_mnt"

    if cfg["overwrite_run"] and run_dir.is_dir():
        print("Removing existing directory before running")
        shutil.rmtree(run_dir)

    dir_dict = _create_directory_structure(run_dir, mnt_dir)
    cfg["dirs"] = dir_dict

    shutil.copy2(cfg["roi_file"], mnt_dir / "input" / "reaches_of_interest.json")

    continent_path = resources.files("confluence") / "continent.json"
    shutil.copy2(continent_path, mnt_dir / "input" / "continent.json")

    _copy_or_download_sos(cfg)
    _copy_or_download_sword(cfg)
    _copy_or_download_svs(cfg)


    out_path = run_dir / "config.yml"
    with open(out_path, "w") as outfile:
        yaml.dump(cfg, outfile)

    return cfg
