import os 
import shutil
import re
from pathlib import Path
import subprocess as sp


# Modules with repo names that differ from `regular` name.
# Defaults to the `regular` name if not listed.
REPO_NAME_MAP = {
    'offline': 'offline-discharge-data-product-creation',
    'moi': 'MOI',
    'validation': 'Validation',
    'hivdi': 'h2ivdi',
    'busboi': 'BUSBOI',
    'lakeflow': 'LakeFlow_Confluence',
}

IMAGE_NAME_MAP = {
    'hivdi': 'h2ivdi'
}

def _validate_dir(dir: str | Path) -> Path:
    """Coerce str -> Path and validate type. Use at every entry point."""
    if isinstance(dir, str):
        dir = Path(dir)
    elif not isinstance(dir, Path):
        raise TypeError(
            f"Argument must be a Path or str. Got {type(dir)}."
        )
    return dir



def clone_repos(
    github_name: str,
    repo_dir: str | Path,
    repo_names: list,
    branch: str | dict = "main",
):
    """Clone repositories with specified branch.
 
    Parameters
    ----------
    github_name : str
        GitHub username or organization name
    repo_dir : str or Path
        Directory to clone repos into
    repos : dict[str, str]
        dictionary of repositories and their github branch to pull.
    """
    repo_dir = _validate_dir(repo_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)
    for name, branch in repo_names.items():
        path = repo_dir / name
        repo_name = REPO_NAME_MAP.get(name, name)
        
        # NEW: handle 'org:branch' syntax for forks
        if ':' in branch:
            custom_org, actual_branch = branch.split(':', 1)
            url = f"https://github.com/{custom_org}/{repo_name}.git"
            branch_name = actual_branch
        else:
            url = f"https://github.com/{github_name}/{repo_name}.git"
            branch_name = branch
        
        # rest of function unchanged
        if path.exists():
            print(f"[Remove] Deleting existing {name} to overwrite...")
            shutil.rmtree(path)
        print(f"[Clone] Cloning {name} from branch {branch_name}...")
        sp.run(["git", "clone", "--branch", branch_name, url, name], cwd=repo_dir)



def _create_lakeflow_defs(mod_dir: Path, tag: str) -> list[Path]:
    """Lakeflow has two Dockerfiles (input + deploy) → two SIFs."""
    sub_images = [
        ("lakeflow_input", "Dockerfile_input"),
        ("lakeflow_deploy", "Dockerfile_deploy"),
    ]
    created = []
    for sub_name, dockerfile_name in sub_images:
        dockerfile_path = mod_dir / dockerfile_name
        if not dockerfile_path.exists():
            print(f"lakeflow: {dockerfile_name} not found, skipping {sub_name}")
            continue

        # Same auto-discovery / entrypoint parsing as main function
        content = dockerfile_path.read_text()
        entrypoint = None
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("ENTRYPOINT") and "[" in line:
                matches = re.findall(r'"([^"]*)"', line)
                if matches:
                    entrypoint = " ".join(matches)

        override_extensions = {".py", ".sh", ".bash", ".r", ".R",
                               ".pl", ".json", ".yaml", ".yml", ".cfg", ".toml", ".ini"}
        skip_dirs = {".git", "__pycache__", ".github", ".eggs",
                     "env", "venv", ".mypy_cache", ".pytest_cache"}
        skip_files = {"Dockerfile", "Dockerfile_input", "Dockerfile_deploy",
                      "Singularity.def", "requirements.txt",
                      ".gitignore", ".dockerignore", "setup.py", "setup.cfg",
                      "pyproject.toml", "LICENSE", "README.md"}

        override_files = []
        for root, dirs, files in os.walk(mod_dir):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
            for fname in files:
                if fname in skip_files:
                    continue
                ext = Path(fname).suffix.lower()
                if ext in override_extensions:
                    full_path = Path(root) / fname
                    rel_path = full_path.relative_to(mod_dir)
                    override_files.append(str(rel_path))

        def_content = f"""Bootstrap: docker
From: ghcr.io/swot-confluence/{sub_name}:{tag}

%files
"""
        for rel_path in sorted(override_files):
            src_full = mod_dir / rel_path
            if src_full.is_dir():
                def_content += f"    {rel_path}/. /app/{rel_path}\n"
            else:
                def_content += f"    {rel_path} /app/{rel_path}\n"

        def_content += "\n%post\n"
        def_content += f'    echo "=== {sub_name}: {len(override_files)} local files overridden ==="\n'

        if entrypoint:
            def_content += f'\n%runscript\n    exec {entrypoint} "$@"\n'

        def_path = mod_dir / f"Singularity_{sub_name}.def"
        def_path.write_text(def_content)
        print(f"{sub_name}: Singularity.def created ({len(override_files)} local files)")
        created.append(def_path)

    return created

def create_singularity_defs(mod: str, repo_dir: str | Path, tag: str = "latest") -> Path | None:
    """Generate Singularity.def from Dockerfile, copying ALL local script files
    into /app/ to override whatever the base GHCR image had.

    This is the key advantage over Dockerfile-COPY parsing: any new file
    added to the cloned repo is picked up automatically on the next build.

    Also reinstalls the module's requirements.txt inside the SIF if present,
    which catches cases (notably MOI) where the base GHCR image is missing
    Python packages the module needs at runtime.
    """
    repo_dir = _validate_dir(repo_dir)
    mod_dir = repo_dir / mod
    
    image_name = IMAGE_NAME_MAP.get(mod, mod)

    # Special handling: lakeflow has two Dockerfiles → two SIFs
    if mod == "lakeflow":
        return _create_lakeflow_defs(mod_dir, tag)

    dockerfile_path = mod_dir / "Dockerfile"
    def_path = mod_dir / "Singularity.def"

    if not dockerfile_path.exists():
        print(f"{mod}: Dockerfile not found, skipping")
        return None

    # Parse Dockerfile for ENTRYPOINT
    content = dockerfile_path.read_text()
    entrypoint = None
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("ENTRYPOINT") and "[" in line:
            matches = re.findall(r'"([^"]*)"', line)
            if matches:
                entrypoint = " ".join(matches)

    # Discover ALL local script files to override
    override_extensions = {
        ".py", ".sh", ".bash", ".r", ".R",
        ".pl", ".json", ".yaml", ".yml", ".cfg", ".toml", ".ini",
    }
    skip_dirs = {
        ".git", "__pycache__", ".github", ".eggs",
        "env", "venv", ".mypy_cache", ".pytest_cache",
    }
    skip_files = {
        "Dockerfile", "Singularity.def", "requirements.txt",
        ".gitignore", ".dockerignore", "setup.py", "setup.cfg",
        "pyproject.toml", "LICENSE", "README.md",
    }

    override_files = []
    for root, dirs, files in os.walk(mod_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for fname in files:
            if fname in skip_files:
                continue
            ext = Path(fname).suffix.lower()
            if ext in override_extensions:
                full_path = Path(root) / fname
                rel_path = full_path.relative_to(mod_dir)
                override_files.append(str(rel_path))

    # Check for requirements.txt — used for both %files copy and %post pip install
    requirements_path = mod_dir / "requirements.txt"
    has_requirements = requirements_path.exists()

    # Build Singularity.def
    def_content = f"""Bootstrap: docker
From: ghcr.io/swot-confluence/{image_name}:{tag}

%files
"""

    # Explicit requirements.txt copy if present (for the %post pip install)
    # Note: requirements.txt is in skip_files for the auto-discovery loop above,
    # so we add it explicitly here only when we plan to reinstall it.
    if has_requirements:
        def_content += "    requirements.txt /app/requirements.txt\n"

    for rel_path in sorted(override_files):
        src_full = mod_dir / rel_path
        if src_full.is_dir():
            def_content += f"    {rel_path}/. /app/{rel_path}\n"
        else:
            def_content += f"    {rel_path} /app/{rel_path}\n"

    def_content += "\n%post\n"
    def_content += f'    echo "=== {mod}: {len(override_files)} local files overridden ==="\n'

    # Reinstall requirements.txt to catch any missing packages in the base image.
    # Critical for MOI which has had recurring missing-package issues. For modules
    # where the base image is already complete, pip detects packages are present
    # and the step is mostly a no-op.
    if has_requirements:
        def_content += f'    echo "=== {mod}: reinstalling requirements.txt ==="\n'
        def_content += "    if [ -f /app/requirements.txt ]; then\n"
        def_content += "        /app/env/bin/python3 -m pip install --no-cache-dir -r /app/requirements.txt 2>/dev/null || \\\n"
        def_content += "        python3 -m pip install --no-cache-dir -r /app/requirements.txt 2>/dev/null || \\\n"
        def_content += f'        echo "WARNING: pip install failed for {mod} — base image packages will be used"\n'
        def_content += "    fi\n"

    if mod == "output":
        def_content += """    # Fix nested output directory
    if [ -d /app/output/output ]; then
        cp -rf /app/output/output/* /app/output/
        rm -rf /app/output/output
    fi
"""

    if entrypoint:
        def_content += f"""
%runscript
    exec {entrypoint} "$@"
"""

    def_path.write_text(def_content)
    print(f"{mod}: Singularity.def created ({len(override_files)} local files)")
    return def_path


# Build SIFs (special-case lakeflow's two sub-images)
def create_sifs(modules: list[str], sif_dir: str | Path, repo_dir: str | Path):
    sif_dir = _validate_dir(sif_dir)
    repo_dir = _validate_dir(repo_dir)

    for mod in modules:
        if mod == "lakeflow":
            for sub_name in ("lakeflow_input", "lakeflow_deploy"):
                sif_path = os.path.join(sif_dir, f'{sub_name}.sif')
                def_file = f'Singularity_{sub_name}.def'
                print(f'{sub_name}: Building...')
                os.system(f'cd {repo_dir}/{mod} && apptainer build --force --ignore-fakeroot-command {sif_path} {def_file}')
        else:
            sif_path = os.path.join(sif_dir, f'{mod}.sif')
            print(f'{mod}: Building...')
            os.system(f'cd {repo_dir}/{mod} && apptainer build --force --ignore-fakeroot-command {sif_path} Singularity.def')