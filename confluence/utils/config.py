from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
    ValidationError,
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
            with open(file_path, "r") as f:
                cfg_dict = yaml.safe_load(f)
        else:
            raise ValueError(f"config file must have a .yml or .yaml extension")

        return cls(**cfg_dict)

    def to_file(self, out_path: Path | str):
        out_path = self.dirs["run"] / "config.yml"
        with open(out_path, "w") as outfile:
            yaml.dump(self.model_dump(), outfile)

    def __str__(self) -> str:
        return self.model_dump_json(indent=4)

    model_config = ConfigDict(extra="forbid")

    root_dir: Path
    run_name: str
    roi_file: FilePath  # must exist
    sword_version: Literal["16", "17"]

    priors_bind_dir: DirectoryPath = None
    priors_copy_dir: DirectoryPath = None
    priors_zenodo_doi: str = None

    priors_bind_dir: DirectoryPath = None
    sword_copy_dir: DirectoryPath = None
    sword_zenodo_doi: str = None

    svs_copy_dir: DirectoryPath = None
    svs_repo_filename: str = None

    default_github_username: str
    default_repository_branch: str
    default_image_release_tag: str

    max_reaches: int = Field(0, gte=0)
    overwrite_run: bool
    clone_repos: bool

    build_modules: bool
    container_platform: Literal["apptainer"]  # TODO implement docker
    submit_driver: bool

    modules_to_run: list[str]

    module_branches: dict[str, str] = Field(default_factory=dict)

    hpc: HPC = Field(default_factory=HPC)
    module_templates: dict[str, ModuleTemplate]

    # Will be populated during run setup.
    dirs: dict[str, Path] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_copy_xor_download(self):
        # Only one of each element in the list here can logically be spec'd.
        # We can't bind the priors AND copy them into our mount, so we should throw a
        #  validation error and tell user to clarify in their config before continuing.
        exclusive_groups = [
            ("priors_bind_dir", "priors_copy_dir", "priors_zenodo_doi"),
            ("sword_bind_dir", "sword_copy_dir", "sword_zenodo_doi"),
            ("svs_copy_dir", "svs_repo_filename"),
        ]

        for attr_group in exclusive_groups:
            values = [getattr(self, a) is not None for a in attr_group]
            if sum(values) > 1:
                raise ValidationError(
                    f"Only specify one of {attr_group} as have conflicting sources for the data."
                )
            if sum(values) == 0:
                print(
                    f"None of {attr_group} was specified so the data will not be bound, copied, or mounted into the input directory."
                )

        return self

    @model_validator(mode="after")
    def validate_module_spec(self):
        to_run = set(self.modules_to_run)
        templates = set(self.module_templates.keys())

        missing_templates = to_run - templates
        if missing_templates:
            raise ValidationError(
                f"missing templates for modules that were set to run: {missing_templates = }."
            )

        return self
