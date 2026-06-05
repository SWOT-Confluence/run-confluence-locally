from pathlib import Path
import json

import yaml


def from_file(file_path: Path | str) -> dict:
    """
    Load the config from a YAML or JSON file. Automatically detects file type by extension.
    """
    if isinstance(file_path, str):
        file_path = Path(file_path)

    ext = file_path.suffix.lower()
    if ext in [".yml", ".yaml"]:
        with open(file_path, "r") as f:
            cfg = yaml.safe_load(f)
    elif ext == ".json":
        with open(file_path, "r") as f:
            cfg = json.load(f)
    else:
        raise ValueError(f"Unsupported config file extension: {ext}")

    cfg["cfg_path"] = file_path
    return cfg


# def to_json(cfg: dict, path: str | Path):
#     """Dump the config to a JSON file."""
#     with open(path, "w") as f:
#         f.write(json_str)
