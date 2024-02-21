from pathlib import Path

import marisa_trie  # type: ignore
import pytest

from import_embargo.core import (
    ModuleTreeBuildingMode,
    get_filenames_to_check,
    get_import_nodes,
    get_package_config,
    is_operation_allowed,
    main,
)


@pytest.mark.parametrize(
    "imported_module, pattern, result",
    (
        ("a.b.c", "a", True),
        ("b", "a", False),
        ("a.c", "a", True),
        ("a.c", "a.b", False),
        ("a.b", "a.b", True),
        ("a.bc", "a.b", False),
        ("a.b.c", "a.b", True),
    ),
)
def test_is_operation_allowed(imported_module: str, pattern: str, result: bool):
    trie = (marisa_trie.Trie([pattern + "."]), False)
    outcome = is_operation_allowed(
        imported_module=imported_module,
        allow=trie,
    )
    assert outcome == result


def test_get_package_config():
    root_path = Path(".").cwd()

    config = get_package_config(
        directory_path=Path(
            f"{root_path}/tests/test_structure/module_c/hello.py"
        ).parent,
        config_lookup={},
        root_path=root_path.cwd(),
    )
    assert config is not None
    assert config.allowed[ModuleTreeBuildingMode.IMPORT][0].keys() == []

    config = get_package_config(
        directory_path=Path(
            f"{root_path}/tests/test_structure/module_a/submodule_a/service.py"
        ).parent,
        config_lookup={},
        root_path=root_path.cwd(),
    )
    assert config is not None
    assert config.allowed[ModuleTreeBuildingMode.IMPORT][0].keys() == []


def test_get_import_nodes():
    root = Path().cwd()
    test_file = Path(f"{root}/tests/test_structure/module_b/service.py")
    result = get_import_nodes(test_file)
    assert result is not None
    assert len(result) == 3
    first_node = result[0]
    assert first_node.module == "tests.test_structure.module_a"
    children = first_node.names
    assert len(children) == 1
    assert children[0].name == "service"

    second_node = result[1]
    assert second_node.module == "tests.test_structure.module_a.service"
    children = second_node.names
    assert children[0].name == "is_weather_nice_today"


def test_get_filenames_to_check():
    root_path = Path().cwd()

    filenames = get_filenames_to_check(
        app_root_path=root_path,
        filenames=[
            "tests/test_structure/module_c/hello.py",
            "tests/test_structure/module_b/service.py",
        ],
    )
    assert len(filenames) == 2

    filenames = get_filenames_to_check(
        app_root_path=root_path, filenames=["tests/test_structure/module_c"]
    )
    assert len(filenames) == 2

    filenames = get_filenames_to_check(
        app_root_path=root_path, filenames=["tests/test_structure/module_a"]
    )
    assert len(filenames) == 4

    filenames = get_filenames_to_check(app_root_path=root_path, filenames=["tests"])
    assert len(filenames) == 23


def test_main_with_fail_import():
    args = [
        "tests/test_structure/module_a",
        "tests/test_structure/module_b",
        "tests/test_structure/module_c",
    ]
    with pytest.raises(SystemExit):
        main(args)


def test_main_with_fail_export():
    args = [
        "tests/test_structure/module_d/service_with_bad_import.py",
    ]

    with pytest.raises(SystemExit):
        main(args)


@pytest.mark.parametrize(
    "args",
    (
        (
            [
                "tests/test_structure/module_b",
                "tests/test_structure/module_d/service.py",
                "tests/test_structure/module_f",
            ]
        ),
        [
            "tests/test_structure/module_f/private_submodule_f",
        ],
        [
            "tests/test_structure/module_f/private_submodule_f/__init__.py",
        ],
    ),
)
def test_main_happy_path(args):
    main(args)
