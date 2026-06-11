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


def strip_modifiers(name: str):
    modifiers = [
        "non_expanded_",
        "expanded_",
        "unconstrained_",
        "constrained_",
        "unconstrained_",
        "constrained_",
    ]
    stripped = name
    for mod in modifiers:
        stripped = stripped.replace(mod, "")

    return stripped


def get_repo_name(mod_name: str):
    stripped = strip_modifiers(mod_name)
    return REPO_NAME_MAP.get(stripped, stripped)


def get_image_name(mod_name: str):
    stripped = strip_modifiers(mod_name)
    return IMAGE_NAME_MAP.get(stripped, stripped)
