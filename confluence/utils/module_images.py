import os
import shutil
import re
from pathlib import Path
import subprocess as sp
from concurrent.futures import ThreadPoolExecutor, as_completed


# Modules with repo names that differ from `regular` name.
# Defaults to the `regular` name if not listed.
REPO_NAME_MAP = {
    "offline": "offline-discharge-data-product-creation",
    "moi": "MOI",
    "validation": "Validation",
    "hivdi": "h2ivdi",
    "busboi": "BUSBOI",
    "lakeflow": "LakeFlow_Confluence",
}

IMAGE_NAME_MAP = {"hivdi": "h2ivdi"}


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
    repo_name = REPO_NAME_MAP.get(name, name)

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
    log_file_path = log_dir / f"{name}_clone.log"

    with open(log_file_path, "w") as log_file:
        result = sp.run(cmd, cwd=repo_dir, stdout=log_file, stderr=sp.STDOUT)

    if result.returncode != 0:
        raise RuntimeError(
            f"Clone failed with exit code {result.returncode}. See {log_file_path}"
        )

    return name, log_file_path


def clone_repos(
    repo_names: list,
    github_name: str,
    default_branch: str,
    branch_map: dict,
    repo_dir: str | Path,
    max_workers: int = 4,
):
    repo_dir = _validate_dir(repo_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)

    log_dir = repo_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    print(f"\n\nCloning {len(repo_names)} module repositories: {repo_names}")
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
                print(f"[{name}] Complete")
            except Exception as e:
                print(f"[{name}]. Check {log_dir}. \nException: {e}")

    print("")


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
) -> Path | None:
    dockerfile_path = mod_dir / dockerfile_name
    if not dockerfile_path.exists():
        print(f"{log_name}: {dockerfile_name} not found, skipping.")
        return None

    entrypoint = _parse_entrypoint(dockerfile_path)
    override_files = _get_override_files(mod_dir)
    has_requirements = (mod_dir / "requirements.txt").exists()

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
            f'    echo "=== {log_name}: {len(override_files)} local files overridden ==="',
        ]
    )

    if has_requirements:
        def_content.extend(
            [
                f'    echo "=== {log_name}: reinstalling requirements.txt ==="',
                "    if [ -f /app/requirements.txt ]; then",
                "        /app/env/bin/python3 -m pip install --no-cache-dir -r /app/requirements.txt 2>/dev/null || \\",
                "        python3 -m pip install --no-cache-dir -r /app/requirements.txt 2>/dev/null || \\",
                f'        echo "WARNING: pip install failed for {log_name} — base image packages will be used"',
                "    fi",
            ]
        )

    if is_output:
        def_content.extend(
            [
                "    # Fix nested output directory",
                "    if [ -d /app/output/output ]; then",
                "        cp -rf /app/output/output/* /app/output/",
                "        rm -rf /app/output/output",
                "    fi",
            ]
        )

    if entrypoint:
        def_content.extend(["\n%runscript", f'    exec {entrypoint} "$@"'])

    def_path = mod_dir / def_filename
    def_path.write_text("\n".join(def_content) + "\n")
    print(f"{log_name}: {def_filename} created ({len(override_files)} local files).")

    return def_path


def _create_lakeflow_defs(mod_dir: Path, tag: str) -> list[Path]:
    configs = [
        ("lakeflow_input", "Dockerfile_input", "Singularity_lakeflow_input.def"),
        ("lakeflow_deploy", "Dockerfile_deploy", "Singularity_lakeflow_deploy.def"),
    ]
    created = []
    for sub_name, dockerfile_name, def_name in configs:
        result = _build_def_file(
            mod_dir=mod_dir,
            image_name=sub_name,
            tag=tag,
            dockerfile_name=dockerfile_name,
            def_filename=def_name,
            log_name=sub_name,
        )
        if result:
            created.append(result)
    return created


def create_defs(
    modules: list[str], repo_dir: str | Path, tag: str = "latest"
) -> Path | list[Path] | None:
    repo_dir = _validate_dir(repo_dir)

    for mod in modules:
        mod_dir = repo_dir / mod

        if mod == "lakeflow":
            return _create_lakeflow_defs(mod_dir, tag)

        image_name = IMAGE_NAME_MAP.get(mod, mod)
        return _build_def_file(
            mod_dir=mod_dir,
            image_name=image_name,
            tag=tag,
            dockerfile_name="Dockerfile",
            def_filename="Singularity.def",
            log_name=mod,
            is_output=(mod == "output"),
        )


def _build_worker(
    mod_name: str,
    container_platform: str,
    repo_dir: Path,
    sif_path: Path,
    def_name: str,
    log_dir: Path,
):
    match container_platform:
        case "apptainer":
            cmd = [
                "apptainer",
                "build",
                "--force",
                "--ignore-fakeroot-command",
                str(sif_path),
                def_name,
            ]
        case _:
            raise ValueError(f"{container_platform = } has not been implemented.")

    # Isolate cache to prevent OCI pull race conditions during multithreading
    worker_cache_dir = repo_dir / ".apptainer_cache" / mod_name
    worker_cache_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["APPTAINER_CACHEDIR"] = str(worker_cache_dir)
    env["SINGULARITY_CACHEDIR"] = str(worker_cache_dir)

    log_file_path = log_dir / f"{mod_name}_build.log"

    with open(log_file_path, "w") as log_file:
        result = sp.run(
            cmd,
            cwd=str(repo_dir / mod_name),
            stdout=log_file,
            stderr=sp.STDOUT,  # Merges stderr into stdout
            env=env,
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"Build failed with exit code {result.returncode}. See {log_file_path}"
        )

    return mod_name, log_file_path


def create_sifs(
    modules: list[str],
    container_platform: str,
    sif_dir: str | Path,
    repo_dir: str | Path,
    max_workers: int = 4,
):
    sif_dir = _validate_dir(sif_dir)
    repo_dir = _validate_dir(repo_dir)

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
                    )
                ] = mod

        for future in as_completed(futures):
            mod_name = futures[future]
            try:
                _, log_path = future.result()
                print(f"[{mod_name}] Complete")
            except Exception as e:
                print(f"[{mod_name}] Failed. See {log_dir}\nException: {e}")
