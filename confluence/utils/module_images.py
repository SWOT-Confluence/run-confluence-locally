import os
import re
import shutil
import subprocess as sp
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib import resources
from pathlib import Path

from confluence.utils.config import Config
from confluence.utils.module_names import get_image_name, get_repo_name, strip_modifiers

LOAD_APPTAINER_SCRIPT = resources.files("confluence") / "scripts" / "load_apptainer.sh"


def ensure_apptainer_loaded():
    """Source load_apptainer.sh and merge the resulting environment (e.g. PATH
    changes from `module load`) into this process, so subprocesses spawned
    afterwards (such as `apptainer build`) can find the apptainer binary.
    """
    result = sp.run(
        ["bash", "-c", f"source {LOAD_APPTAINER_SCRIPT} 1>&2 && env -0"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to load Apptainer module:\n{result.stderr.decode()}")

    # Merge the subprocess environment into the current process environment. 
    # This is necessary because `module load` commands in the sourced 
    # script may modify PATH and other environment variables.
    for entry in result.stdout.decode().split("\0"):
        if "=" in entry:
            key, _, value = entry.partition("=")
            os.environ[key] = value


def _validate_dir(dir: str | Path) -> Path:
    """Coerce str -> Path and validate type. Use at every entry point."""
    if isinstance(dir, str):
        dir = Path(dir)
    elif not isinstance(dir, Path):
        raise TypeError(f"Argument must be a Path or str. Got {type(dir)}.")
    return dir


def _clone_worker(
    name: str,
    github_name: str,
    default_branch: str,
    branch_map: dict,
    repo_dir: Path,
    log_dir: Path,
) -> tuple[str, Path]:
    branch = branch_map.get(name, default_branch)
    path = repo_dir / name
    repo_name = get_repo_name(name)
    log_file_path = log_dir / f"{name}_clone.log"

    if ":" in branch:
        custom_org, actual_branch = branch.split(":", 1)
        url = f"https://github.com/{custom_org}/{repo_name}.git"
        branch_name = actual_branch
    else:
        url = f"https://github.com/{github_name}/{repo_name}.git"
        branch_name = branch

    if path.exists():
        shutil.rmtree(path)

    cmd = ["git", "clone", "--branch", branch_name, url, name]
    print("Clone command: " + " ".join(cmd))

    with open(log_file_path, "w") as log_file:
        result = sp.run(cmd, cwd=repo_dir, stdout=log_file, stderr=sp.STDOUT)

    if result.returncode != 0:
        raise RuntimeError(f"Clone failed with exit code {result.returncode}. See {log_file_path}")

    return name, log_file_path


def clone_repos(
    repo_names: list,
    github_name: str,
    default_branch: str,
    branch_map: dict,
    repo_dir: str | Path,
    max_workers: int = None,
):
    repo_dir = _validate_dir(repo_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)
    max_workers = _resolve_max_workers(max_workers)

    log_dir = repo_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    print(f"Cloning {len(repo_names)} module repositories: {repo_names}")
    print(f"Full logs of clone progress in: {log_dir}")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _clone_worker,
                name,
                github_name,
                default_branch,
                branch_map,
                repo_dir,
                log_dir,
            ): name
            for name in repo_names
        }

        for future in as_completed(futures):
            name = futures[future]
            try:
                _, log_path = future.result()
            except Exception as e:
                print(f"[{name}]. Check {log_dir}. \nException: {e}")


def copy_local_repos(local_map: dict, repo_dir: str | Path):
    """Copy module source from local directories (modules not on GitHub) in place of a clone."""
    repo_dir = _validate_dir(repo_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)

    for name, src in local_map.items():
        path = repo_dir / name
        if path.exists():
            shutil.rmtree(path)
        print(f"Copying local repository: {src} -> {path}")
        shutil.copytree(
            src,
            path,
            ignore=shutil.ignore_patterns("__pycache__", "*.sif", "Singularity.def"),
        )
        # The image keeps these modes and runs as the user, not as root: a directory without the
        # o+x bit (e.g. a group-only source tree) can't be entered at runtime.
        sp.run(["chmod", "-R", "a+rX", str(path)], check=True)


def _parse_entrypoint(dockerfile_path: Path) -> str | None:
    content = dockerfile_path.read_text()
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("ENTRYPOINT") and "[" in line:
            matches = re.findall(r'"([^"]*)"', line)
            if matches:
                return " ".join(matches)
    return None


def _get_override_files(mod_dir: Path) -> list[str]:
    override_extensions = {
        ".py",
        ".sh",
        ".bash",
        ".r",
        ".R",
        ".pl",
        ".json",
        ".yaml",
        ".yml",
        ".cfg",
        ".toml",
        ".ini",
    }
    skip_dirs = {
        ".git",
        "__pycache__",
        ".github",
        ".eggs",
        "env",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
    }
    skip_files = {
        "Dockerfile",
        "Dockerfile_input",
        "Dockerfile_deploy",
        "Singularity.def",
        "requirements.txt",
        ".gitignore",
        ".dockerignore",
        "setup.py",
        "setup.cfg",
        "pyproject.toml",
        "LICENSE",
        "README.md",
    }

    override_files = []
    for root, dirs, files in os.walk(mod_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for fname in files:
            if fname in skip_files:
                continue
            if Path(fname).suffix.lower() in override_extensions:
                full_path = Path(root) / fname
                rel_path = full_path.relative_to(mod_dir)
                override_files.append(str(rel_path))
    return sorted(override_files)


def _build_def_file(
    mod_dir: Path,
    image_name: str,
    tag: str,
    dockerfile_name: str,
    def_filename: str,
    log_name: str,
    is_output: bool = False,
    is_hivdi: bool = False,
) -> Path | None:
    dockerfile_path = mod_dir / dockerfile_name
    if not dockerfile_path.exists():
        print(f"{log_name}: {dockerfile_name} not found, skipping.")
        return None

    entrypoint = _parse_entrypoint(dockerfile_path)
    override_files = _get_override_files(mod_dir)
    requirements_path = mod_dir / "requirements.txt"
    has_requirements = requirements_path.exists()
    # pip needs git on PATH to resolve `pkg @ git+https://...` requirements, but the
    # base images don't all ship it.
    needs_git = has_requirements and "git+" in requirements_path.read_text()

    def_content = [
        "Bootstrap: docker",
        f"From: ghcr.io/swot-confluence/{image_name}:{tag}\n",
        "%files",
    ]

    if has_requirements:
        def_content.append("    requirements.txt /app/requirements.txt")

    for rel_path in override_files:
        src_full = mod_dir / rel_path
        target = f"{rel_path}/." if src_full.is_dir() else rel_path
        def_content.append(f"    {target} /app/{rel_path}")

    def_content.extend(
        [
            "\n%post",
            "    set -eu",
            f'    echo "=== {log_name}: building image ==="',
        ]
    )

    if has_requirements:
        def_content.extend(
            [
                f'    echo "=== {log_name}: installing requirements ==="',
                # --ignore-installed: some base images ship apt-installed python packages with no
                # pip RECORD file, which pip can't uninstall when a requirement wants a newer version.
                "    python3 -m pip install --no-cache-dir --break-system-packages "
                "--ignore-installed -r /app/requirements.txt",
            ]
        )

    if is_output:
        def_content.extend(
            [
                "    if [ -d /app/output/output ]; then",
                "        cp -rf /app/output/output/* /app/output/",
                "        rm -rf /app/output/output",
                "    fi",
            ]
        )

    if is_hivdi:
        def_content.extend(
            [
                "    if [ -f /app/hivdi_config.json ]; then",
                "        cp -f /app/hivdi_config.json /app/H2iVDI/hivdi_config.json",
                "    fi",
            ]
        )

    def_content.append("\n%runscript")
    if entrypoint:
        def_content.append(f'    exec {entrypoint} "$@"')
    else:
        def_content.append('    exec "$@"')

    def_content.extend(
        [
            "\n%environment",
            "    export PYTHONUNBUFFERED=1",
        ]
    )

    def_path = mod_dir / def_filename
    def_path.write_text("\n".join(def_content) + "\n")

    print(f"{log_name}: {def_filename} created ({len(override_files)} local files).")
    return def_path


def _create_lakeflow_defs(mod_dir: Path, tag: str) -> list[Path]:
    configs = [
        ("lakeflow_input", "Dockerfile_input", "Singularity_lakeflow_input.def"),
        ("lakeflow_deploy", "Dockerfile_deploy", "Singularity_lakeflow_deploy.def"),
    ]
    for sub_name, dockerfile_name, def_name in configs:
        _build_def_file(
            mod_dir=mod_dir,
            image_name=sub_name,
            tag=tag,
            dockerfile_name=dockerfile_name,
            def_filename=def_name,
            log_name=sub_name,
        )


def create_defs(
    modules: list[str],
    repo_dir: str | Path,
    tag: str = "latest",
    tag_map: dict | None = None,
) -> Path | list[Path] | None:
    repo_dir = _validate_dir(repo_dir)
    tag_map = tag_map or {}

    for mod in modules:
        mod_dir = repo_dir / mod

        if mod == "lakeflow":
            return _create_lakeflow_defs(mod_dir, tag)

        # A module that is not published on ghcr.io/swot-confluence ships its own
        # self-contained recipe: use it instead of overlaying files on a release image.
        local_def = mod_dir / "Singularity.local.def"
        if local_def.exists():
            shutil.copy2(local_def, mod_dir / "Singularity.def")
            print(f"{mod}: Singularity.def copied from Singularity.local.def (self-contained build).")
            continue

        image_name = get_image_name(mod)
        _build_def_file(
            mod_dir=mod_dir,
            image_name=image_name,
            tag=tag_map.get(mod, tag),
            dockerfile_name="Dockerfile",
            def_filename="Singularity.def",
            log_name=mod,
            is_output=(mod == "output"),
            is_hivdi=(mod == "hivdi"),
        )


def _build_worker(
    mod_name: str,
    container_platform: str,
    repo_dir: Path,
    sif_path: Path,
    def_name: str,
    log_dir: Path,
    build_args: list[str],
    build_tmpdir: Path | None,
):
    match container_platform:
        case "apptainer":
            cmd = [
                "apptainer",
                "build",
                "--force",
                "--ignore-fakeroot-command",
                *build_args,
                str(sif_path),
                def_name,
            ]
        case _:
            raise ValueError(f"{container_platform = } has not been implemented.")

    worker_cache_dir = repo_dir / ".apptainer_cache" / mod_name
    worker_cache_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["APPTAINER_CACHEDIR"] = str(worker_cache_dir)
    env["SINGULARITY_CACHEDIR"] = str(worker_cache_dir)

    if build_tmpdir is not None:
        worker_tmp = Path(build_tmpdir) / mod_name
        worker_tmp.mkdir(parents=True, exist_ok=True)
        env["APPTAINER_TMPDIR"] = str(worker_tmp)
        env["SINGULARITY_TMPDIR"] = str(worker_tmp)

    log_file_path = log_dir / f"{mod_name}_build.log"

    with open(log_file_path, "w") as log_file:
        result = sp.run(
            cmd,
            cwd=str(repo_dir / mod_name),
            stdout=log_file,
            stderr=sp.STDOUT,
            env=env,
            check=False,
        )

    if result.returncode != 0:
        print(f"\n  BUILD FAILED: {mod_name}")
        print(f"   log: {log_file_path}")
        raise RuntimeError(f"Build failed (exit={result.returncode})")

    return mod_name, log_file_path


def _resolve_max_workers(max_workers: int | None) -> int | None:
    """None means "auto": full parallelism inside a Slurm allocation (SLURM_JOB_ID
    set by salloc/sbatch/srun), throttled down on a shared login node, where several
    concurrent subprocesses each spawn one thread per core by default (`apptainer
    build` via mksquashfs, and `git clone` via index-pack), and can otherwise exhaust
    the login node's shared process/thread limits.
    """
    if max_workers is not None:
        return max_workers
    return None if os.environ.get("SLURM_JOB_ID") else 2


def create_sifs(
    modules: list[str],
    container_platform: str,
    sif_dir: str | Path,
    repo_dir: str | Path,
    build_args: list[str] | None = None,
    build_tmpdir: Path | None = None,
    max_workers: int = None,
):
    build_args = build_args or []
    sif_dir = _validate_dir(sif_dir)
    repo_dir = _validate_dir(repo_dir)
    max_workers = _resolve_max_workers(max_workers)

    if container_platform == "apptainer":
        ensure_apptainer_loaded()

    log_dir = sif_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    print(f"\n\nBuilding {len(modules)} images: {modules}")
    print(f"Full logs of build progress in: {log_dir}")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for mod in modules:
            if mod == "lakeflow":
                for sub_name in ("lakeflow_input", "lakeflow_deploy"):
                    sif_path = sif_dir / f"{sub_name}.sif"
                    def_file = f"Singularity_{sub_name}.def"
                    futures[
                        executor.submit(
                            _build_worker,
                            sub_name,
                            container_platform,
                            repo_dir,
                            sif_path,
                            def_file,
                            log_dir,
                            build_args,
                            build_tmpdir,
                        )
                    ] = sub_name
            else:
                sif_path = sif_dir / f"{mod}.sif"
                def_file = "Singularity.def"
                futures[
                    executor.submit(
                        _build_worker,
                        mod,
                        container_platform,
                        repo_dir,
                        sif_path,
                        def_file,
                        log_dir,
                        build_args,
                        build_tmpdir,
                    )
                ] = mod

        for future in as_completed(futures):
            mod_name = futures[future]
            try:
                _, log_path = future.result()
                print(f"[{mod_name}] Complete")
            except Exception as e:
                print(f"[{mod_name}] Failed. See {log_dir}\nException: {e}")
                raise e


def _get_modules_to_build(cfg: Config):
    if cfg.no_build:
        return set()

    # set to removed duplicates after removing modifiers (e.g. expanded and non_expanded setfinder)
    modules = set([strip_modifiers(mod) for mod in cfg.modules_to_run])

    if cfg.rebuild_all_modules:
        return modules

    images = cfg.dirs["sif"].glob("*.sif")
    image_names = set([im.stem for im in images])
    without_images = modules - image_names

    if cfg.modules_to_rebuild:
        return without_images.union(set(cfg.modules_to_rebuild))
    else:
        return without_images


def setup_modules(cfg: Config, build_max_workers: int | None = None):
    modules_to_build = _get_modules_to_build(cfg)

    print("\n")
    local_repos = {m: cfg.local_repo_dirs[m] for m in modules_to_build if m in cfg.local_repo_dirs}
    if local_repos:
        copy_local_repos(local_repos, cfg.dirs["modules"])

    if cfg.clone_repos:
        clone_repos(
            modules_to_build - local_repos.keys(),
            cfg.default_github_username,
            cfg.default_repository_branch,
            cfg.repo_branches,
            cfg.dirs["run"] / "modules",
        )
    else:
        existing_repos = {d.name for d in cfg.dirs["modules"].iterdir() if d.is_dir()}
        missing_repos = modules_to_build - existing_repos
        if missing_repos:
            raise ValueError(f"Missing repositories for {missing_repos}. Set cfg.clone_repos = True to download.")
        else:
            print("Using existing repository files.")

    print("\n")
    if modules_to_build:
        create_defs(
            modules_to_build,
            cfg.dirs["modules"],
            cfg.default_image_release_tag,
            tag_map=cfg.image_release_tags,
        )
        create_sifs(
            modules_to_build,
            cfg.container_platform,
            cfg.dirs["sif"],
            cfg.dirs["modules"],
            build_args=cfg.apptainer_build_args,
            build_tmpdir=cfg.build_tmpdir,
            max_workers=build_max_workers,
        )
    else:
        print("Skipping module rebuild.")
