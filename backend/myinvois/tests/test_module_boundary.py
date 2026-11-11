"""The invoice engine (`core`) and the MyInvois module (`myinvois`) stay on their own sides of D59 (1), D61 (3), D61 (4).

`core` is the invoice engine and imports nothing from `myinvois`. It reaches LHDN only through the gateway port
(`core.ports`) and the job runner (`core.jobs`), and asks only `core.einvoicing.einvoicing_enabled`. `myinvois` may read
`core`. The check reads the source, so a file added by a later ticket is covered without anyone remembering to list it.
"""

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
ENGINE = BACKEND / "core"
MYINVOIS = BACKEND / "myinvois"


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                yield "." * node.level + (node.module or "")  # a relative import
            else:
                yield node.module or ""
                # `from . import x` / `import_module` aside, `from pkg import myinvois` names the module in the alias.
                yield from (f"{node.module}.{alias.name}" for alias in node.names)


def _reaches_myinvois(name, own_package):
    if name == "myinvois" or name.startswith("myinvois."):
        return True
    # A relative import climbing out of the package (`from .. import myinvois`) cannot be resolved by name; refuse the
    # shape outright in the engine, where the dots would have to climb above `core` to name a sibling app.
    return own_package == "core" and name.startswith("..")


def _engine_files():
    return sorted(p for p in ENGINE.rglob("*.py") if "migrations" not in p.parts and "__pycache__" not in p.parts)


def test_the_myinvois_app_is_a_separate_package_next_to_core():
    assert (MYINVOIS / "__init__.py").is_file() and (MYINVOIS / "apps.py").is_file()
    assert (MYINVOIS / "submission.py").is_file()


def test_submission_rules_live_in_the_myinvois_app_not_in_core():
    assert not (ENGINE / "submission.py").exists()


@pytest.mark.parametrize("path", _engine_files(), ids=lambda p: str(p.relative_to(BACKEND)))
def test_the_invoice_engine_imports_nothing_from_the_myinvois_module(path):
    offending = [name for name in _imported_modules(path) if _reaches_myinvois(name, "core")]
    assert offending == [], f"{path.relative_to(BACKEND)} imports {offending}: the engine must not import myinvois (D61 (3))"


def _dynamic_imports_of_myinvois(path):
    """Names of `import_module("myinvois...")` / `__import__("myinvois...")` calls with a literal module name."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        first = node.args[0]
        if called in {"import_module", "__import__"} and isinstance(first, ast.Constant) and isinstance(first.value, str):
            if first.value == "myinvois" or first.value.startswith("myinvois."):
                found.append(first.value)
    return found


@pytest.mark.parametrize("path", _engine_files(), ids=lambda p: str(p.relative_to(BACKEND)))
def test_the_engine_cannot_reach_myinvois_by_a_dynamic_import(path):
    assert _dynamic_imports_of_myinvois(path) == []


def test_the_checker_catches_a_dynamic_import(tmp_path):
    path = tmp_path / "engine_file.py"
    path.write_text('from importlib import import_module\nimport_module("myinvois.submission")\n', encoding="utf-8")
    assert _dynamic_imports_of_myinvois(path) == ["myinvois.submission"]


def test_the_myinvois_app_is_installed(settings):
    assert "myinvois" in settings.INSTALLED_APPS


def test_the_myinvois_app_adds_no_tenant_table():
    from django.apps import apps

    assert list(apps.get_app_config("myinvois").get_models()) == []


@pytest.mark.parametrize(
    "source",
    [
        "import myinvois",
        "import myinvois.submission",
        "from myinvois import submission",
        "from myinvois.submission import submit_invoice",
        "from .. import myinvois",
    ],
)
def test_the_checker_catches_each_way_of_importing_the_myinvois_module(tmp_path, source):
    path = tmp_path / "engine_file.py"
    path.write_text(source + "\n", encoding="utf-8")
    assert [n for n in _imported_modules(path) if _reaches_myinvois(n, "core")]


def test_the_checker_lets_the_engine_use_its_own_door():
    source = "from core import ports\nfrom core.einvoicing import einvoicing_enabled\nfrom core.jobs import enqueue\n"
    path = Path(__file__).with_name("_door.py")
    try:
        path.write_text(source, encoding="utf-8")
        assert [n for n in _imported_modules(path) if _reaches_myinvois(n, "core")] == []
    finally:
        path.unlink(missing_ok=True)
