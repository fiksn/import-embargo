import argparse
import ast
import dataclasses
import enum
import json
import sys
from pathlib import Path
from typing import TypeAlias

import marisa_trie  # type: ignore

IGNORE_LIST = {"__pycache__", ".mypy_cache", ".DS_Store", ".ruff_cache"}


class ModuleTreeBuildingMode(enum.Enum):
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"
    BYPASS = "BYPASS"


@dataclasses.dataclass
class Config:
    # Trie also contains a bool whether it is empty
    allowed: dict[ModuleTreeBuildingMode, tuple[marisa_trie.Trie, bool]]
    path: str


ConfigLookup: TypeAlias = dict[str, Config]


def get_import_nodes(filename: str) -> list[ast.ImportFrom]:
    with open(filename) as f:
        file = f.read()
    tree = ast.parse(file)

    return [node for node in tree.body if isinstance(node, ast.ImportFrom)]


def build_path_from_import(module_import: str, root_path: Path) -> Path:
    """
    Build an absolute path to a file based on a app root path and module import.
    """
    module_path = Path(module_import.replace(".", "/"))
    return root_path / module_path


def build_module_from_path(path: Path, root_path: Path) -> str:
    return str(path.relative_to(root_path)).replace("/", ".").replace(".py", "")


def get_package_config(
    directory_path: Path, root_path: Path, config_lookup: ConfigLookup
) -> Config | None:
    potential_embargo_file = Path(directory_path) / Path("__embargo__.json")

    cached_config = config_lookup.get(str(potential_embargo_file))
    if cached_config is not None:
        return cached_config

    if not potential_embargo_file.exists():
        if directory_path == root_path:
            return None
        return get_package_config(directory_path.parent, root_path, config_lookup)

    json_config = json.loads(potential_embargo_file.read_text())
    config = Config(
        path=str(potential_embargo_file),
        allowed={},
    )
    for which, name in [
        (ModuleTreeBuildingMode.IMPORT, "allowed_import_modules"),
        (ModuleTreeBuildingMode.EXPORT, "allowed_export_modules"),
        (ModuleTreeBuildingMode.BYPASS, "bypass_export_check_for_modules"),
    ]:
        val = json_config.get(name)
        if val is None:
            config.allowed[which] = (marisa_trie.Trie([]), True)
        else:
            # Insert everything with trailing .
            # This avoids a.bc matching a.b
            modules = [x + "." for x in json_config.get(name)]
            config.allowed[which] = (marisa_trie.Trie(modules), False)
    config_lookup[str(potential_embargo_file)] = config
    return config


def is_local_import(module_import: ast.ImportFrom) -> bool:
    """
    Determines if import is local or from third party library
    """
    module = module_import.module
    if module is None:
        return False
    module_path = module.split(".")
    first_package = module_path[0]

    return Path(first_package).is_dir() or Path(first_package + ".py").exists()


def is_operation_allowed(
    imported_module: str, allow: tuple[marisa_trie.Trie, bool]
) -> bool:
    """
    Determines if imported module is allowed with regards to "allow list" allow in
    form of a trie.

    If you import the following module:
        from a import b
    At least 'a' needs to be allowed in config.

    If the following module is imported:
        from a import b
    and the allowed path is: ['a.c']
    the import will reported as violation as it has diverged from the 'a.c' path.
    """
    if allow[1]:
        # Trie is empty
        return True
    # Dot is appended since everything was added to the trie with dot postfix
    return len(allow[0].prefixes(imported_module + ".")) > 0


def get_filenames_to_check(filenames: list[str], app_root_path: Path) -> list[Path]:
    all_files: list[Path] = []
    for filename in filenames:
        path = app_root_path / Path(filename)
        if path.is_file():
            all_files.append(path)
        else:
            for path in path.rglob("*.py"):
                if not any(dir_name in IGNORE_LIST for dir_name in path.parts):
                    all_files.append(path)
    return all_files


def check_for_allowed(
    mode: ModuleTreeBuildingMode,
    file: Path,
    app_root_path: Path,
    config_lookup: ConfigLookup,
    node: ast.ImportFrom,
) -> list[str]:
    """
    Checks whether module X.py can import any other module when mode is ModuleTreeBuildingMode.IMPORT
    or whether module X.py can be imported from other modules when mode is ModuleTreeBuildingMode.EXPORT
    """
    violations: list[str] = []

    if mode == ModuleTreeBuildingMode.EXPORT:
        actual_path = build_path_from_import(
            module_import=node.module or "", root_path=app_root_path
        )
    elif mode == ModuleTreeBuildingMode.IMPORT:
        actual_path = file
    else:
        raise Exception("Invalid mode")

    config = get_package_config(
        directory_path=actual_path.parent,
        root_path=app_root_path,
        config_lookup=config_lookup,
    )
    if config is None or config.allowed[mode] is None:
        return []

    if node.module is None:
        return []

    if mode == ModuleTreeBuildingMode.EXPORT:
        # Bypass check
        if is_operation_allowed(
            imported_module=build_module_from_path(path=file, root_path=app_root_path),
            # Change bypass trie so that also when it is empty, it does not allow all
            allow=(config.allowed[ModuleTreeBuildingMode.BYPASS][0], False),
        ):
            return []

    if is_operation_allowed(
        imported_module=node.module,
        allow=config.allowed[mode],
    ):
        return []

    violations.append(f"{file}: {node.module}")
    human_readable_allowed = [x.rstrip(".") for x in config.allowed[mode][0].keys()]

    if mode == ModuleTreeBuildingMode.EXPORT:
        violations.append(f"Allowed exports: {human_readable_allowed}")
    else:
        violations.append(f"Allowed imports: {human_readable_allowed}")

    violations.append(f"Config file: {config.path}\n")
    return violations


def check_for_violations(
    filename: Path,
    app_root_path: Path,
    config_lookup: dict[str, Config],
) -> tuple[list[str], list[str]]:
    import_violations: list[str] = []
    export_violations: list[str] = []
    if not str(filename).endswith(".py"):
        print(f"Not checking file {filename}")
        return [], []

    import_nodes = get_import_nodes(str(filename))
    local_import_nodes = filter(is_local_import, import_nodes)

    for node in local_import_nodes:
        #
        # Check for allowed imports
        #
        import_violations += check_for_allowed(
            mode=ModuleTreeBuildingMode.IMPORT,
            app_root_path=app_root_path,
            file=filename,
            config_lookup=config_lookup,
            node=node,
        )

        #
        # Check for allowed exports
        #
        export_violations += check_for_allowed(
            mode=ModuleTreeBuildingMode.EXPORT,
            app_root_path=app_root_path,
            file=filename,
            config_lookup=config_lookup,
            node=node,
        )

    return import_violations, export_violations


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "filenames",
        nargs="*",
        help="List of files or directories. Example: src/module_a src/module_b",
    )
    parser.add_argument(
        "--app-root",
        dest="app_root",
        default=".",
        help="Defines the root directory where your python application lives."
        "Default value is current working directory. Example: --app-root=src",
    )
    args = parser.parse_args(argv)

    if not Path(args.app_root).exists():
        print(
            "--app-root argument does not point to root directory of python application",
            file=sys.stderr,
        )
        exit(1)

    app_root_path = Path(args.app_root).resolve()

    filenames_to_check = get_filenames_to_check(
        args.filenames, app_root_path=app_root_path
    )

    import_violations: list[str] = []
    export_violations: list[str] = []
    config_lookup: dict[str, Config] = {}

    for file in filenames_to_check:
        imp_violations, exp_violations = check_for_violations(
            filename=file,
            app_root_path=app_root_path,
            config_lookup=config_lookup,
        )
        import_violations += imp_violations
        export_violations += exp_violations

    if len(import_violations) > 0:
        print(" ❌ Import violations detected\n", file=sys.stderr)
        for violation in import_violations:
            print(violation, file=sys.stderr)

    if len(export_violations) > 0:
        print(" ❌ Export violations detected\n", file=sys.stderr)
        for violation in export_violations:
            print(violation, file=sys.stderr)

    if len(import_violations) + len(export_violations) > 0:
        exit(1)
