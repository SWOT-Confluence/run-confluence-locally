from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.types import DirectoryPath, FilePath


class HPC(BaseModel):
    partition: str
    account: str
    time: str
    batch_size: int = Field(..., gt=0)
    concurrent_jobs: int = Field(..., gt=0)
    reach_chunks: int = Field(..., gt=0)


class ModuleTemplate(BaseModel):
    time: str
    mem: str
    j2_file: str
    # kind of sloppy but module specific args go in an unvalidated dict
    module_args: dict = Field(default_factory=dict)


class Config(BaseModel):
    @classmethod
    def from_file(cls, file_path: Path | str) -> "Config":
        """
        Load the config from a YAML or JSON file. Automatically detects file type by extension.
        """
        file_path = Path(file_path).expanduser().resolve()
        if not file_path.is_file():
            raise FileNotFoundError(f"Config file not found: {file_path}")

        ext = file_path.suffix.lower()
        if ext in [".yml", ".yaml"]:
            with open(file_path) as f:
                cfg_dict = yaml.safe_load(f)
        else:
            raise ValueError("config file must have a .yml or .yaml extension")

        return cls(**cfg_dict)

    def to_file(self, out_path: Path | str):
        out_path = Path(out_path)
        with open(out_path, "w") as outfile:
            yaml.dump(self.model_dump(mode="json"), outfile)

    def __str__(self) -> str:
        return self.model_dump_json(indent=4)

    model_config = ConfigDict(extra="forbid")

    root_dir: Path
    run_name: str
    roi_file: FilePath  # must exist
    sword_version: Literal["16", "17"]

    swot_input_bind_dir: DirectoryPath | None = None

    priors_bind_dir: DirectoryPath | None = None
    priors_copy_dir: DirectoryPath | None = None
    priors_zenodo_doi: str | None = None

    sword_bind_dir: DirectoryPath | None = None
    sword_copy_dir: DirectoryPath | None = None
    sword_zenodo_doi: str | None = None

    svs_copy_dir: DirectoryPath | None = None
    svs_repo_filename: str | None = None

    default_github_username: str
    default_repository_branch: str
    default_image_release_tag: str

    max_reaches: int = Field(0, gte=0)
    overwrite_run: bool = False
    clone_repos: bool

    # TODO implement docker
    # The shell scripts allow Docker commands for run and bind based on this arg, but I haven't
    # had implemented the build commands for docker yet.
    container_platform: Literal["apptainer"] = "apptainer"
    submit_driver: bool

    modules_to_run: list[str]

    rebuild_all_modules: bool
    modules_to_rebuild: list[str] = Field(default_factory=list)

    repo_branches: dict[str, str] = Field(default_factory=dict)

    hpc: HPC = Field(default_factory=HPC)
    module_templates: dict[str, ModuleTemplate]

    # Will be populated during run setup.
    dirs: dict[str, Path] = Field(default_factory=dict)


    @field_validator(
        "root_dir",
        "roi_file",
        "swot_input_bind_dir",
        "priors_bind_dir",
        "priors_copy_dir",
        "sword_bind_dir",
        "sword_copy_dir",
        "svs_copy_dir",
        mode="before",
    )
    @classmethod
    def require_absolute_paths(cls, v):
        if v is None:
            return v
            
        if not Path(v).expanduser().is_absolute():
            raise ValueError(f"Path must be absolute (full path that begins with a '/'). Received: '{v}'")
            
        return v

    @model_validator(mode="after")
    def validate_copy_xor_download(self):
        # Only one of each element in the list here can logically be spec'd.
        # We wouldn't bind the priors AND copy them into our mount, so we should throw a
        # validation error and tell user to clarify in their config before running.
        exclusive_groups = [
            ("priors_bind_dir", "priors_copy_dir", "priors_zenodo_doi"),
            ("sword_bind_dir", "sword_copy_dir", "sword_zenodo_doi"),
            ("svs_copy_dir", "svs_repo_filename"),
        ]

        for attr_group in exclusive_groups:
            no_data_flag = False
            values = [getattr(self, a) is not None for a in attr_group]
            if sum(values) > 1:
                raise ValueError(f"Only specify one of {attr_group} as have conflicting sources for the data.")
            if sum(values) == 0:
                print(
                    f"None of {attr_group} was specified so the data will not be "
                    + "bound, copied, or mounted into the input directory."
                )
                no_data_flag = True

        if no_data_flag:
            print("This is not an issue if these data already exist in the input dir, otherwise the job will fail.")

        return self

    @model_validator(mode="after")
    def validate_swot_input_binding(self):
        # Binding the swot files will only work if you are not running `input` and `prediagnostics` modules.
        # Input will not work because the /mnt/input/sword dir will not be writable under this bind, and
        binds_input = self.swot_input_bind_dir is not None
        runs_input = bool({"input", "prediagnostics"} & set(self.modules_to_run))

        if binds_input and runs_input:
            raise ValueError(
                "Binding the input SWOT files will only work if you are skipping 'input' and 'prediagnostics' modules. "
                + "The directory will bind as read only so these modules will not be able to "
                + "write (input) or modify (prediagnostics) the files."
            )

        return self

    @model_validator(mode="after")
    def validate_filesystem_binds(self):
        bind_paths = [
            "swot_input_bind_dir",
            "priors_bind_dir",
            "sword_bind_dir",
        ]

        root_fs = Path(self.root_dir).parts[1]
        offending = [
            bp for bp in bind_paths if getattr(self, bp) is not None and Path(getattr(self, bp)).parts[1] != root_fs
        ]

        if offending:
            raise ValueError(f"{offending} bind paths are not on the same filesystem as the root_dir.")

        return self

    @model_validator(mode="after")
    def validate_module_spec(self):
        to_run = set(self.modules_to_run)
        templates = set(self.module_templates.keys())

        missing_templates = to_run - templates
        if missing_templates:
            raise ValueError(f"missing templates for modules that were set to run: {missing_templates = }.")

        return self
