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


def _is_myinvois(name):
    return name == "myinvois" or name.startswith("myinvois.")


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(path, package):
    """Absolute names a file imports, relative imports resolved against `package` (the file's own dotted package)."""
    names = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                # `from . import x` is level 1: the file's own package. Each further dot climbs one package up.
                parts = package.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            names.append(base)
            # `from pkg import myinvois` names the module in the alias.
            names.extend(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
    return names


def _dynamic_imports(path):
    """Literal module names passed to `import_module(...)` or `__import__(...)`."""
    found = []
    for node in ast.walk(_parse(path)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        first = node.args[0]
        if called in {"import_module", "__import__"} and isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.append(first.value)
    return found


def _package_of(path):
    return ".".join(path.relative_to(BACKEND).with_suffix("").parts[:-1])


def _violations(path):
    package = _package_of(path)
    return [n for n in _imported_modules(path, package) if _is_myinvois(n)] + [
        n for n in _dynamic_imports(path) if _is_myinvois(n)
    ]


def _engine_files():
    return sorted(p for p in ENGINE.rglob("*.py") if "migrations" not in p.parts and "__pycache__" not in p.parts)


def test_the_check_looks_at_the_engine_source():
    # A wrong path would parametrise zero cases below and pass without checking anything.
    names = {p.name for p in _engine_files()}
    assert {"jobs.py", "einvoicing.py", "views.py"} <= names


@pytest.mark.parametrize("path", _engine_files(), ids=lambda p: str(p.relative_to(BACKEND)))
def test_the_invoice_engine_imports_nothing_from_the_myinvois_module(path):
    assert _violations(path) == [], f"{path.relative_to(BACKEND)} reaches myinvois: the engine must not (D61 (3))"


@pytest.mark.parametrize(
    "source",
    [
        "import myinvois",
        "import myinvois.submission",
        "from myinvois import submission",
        "from myinvois.submission import submit_invoice",
        "from .. import myinvois",
        "from ..myinvois import submission",
        "from importlib import import_module\nimport_module('myinvois.submission')",
        "__import__('myinvois')",
    ],
)
def test_the_check_catches_each_way_of_reaching_the_myinvois_module(tmp_path, source):
    # Placed as if it were `core/engine_file.py`, so `..` climbs to the top level where `myinvois` lives.
    path = BACKEND / "core" / "engine_file.py"
    file = tmp_path / "engine_file.py"
    file.write_text(source + "\n", encoding="utf-8")
    package = _package_of(path)
    found = [n for n in _imported_modules(file, package) if _is_myinvois(n)] + [
        n for n in _dynamic_imports(file) if _is_myinvois(n)
    ]
    assert found


def test_the_check_lets_the_engine_use_its_own_doors_and_nested_relative_imports(tmp_path):
    file = tmp_path / "engine_file.py"
    file.write_text(
        "from core import ports\nfrom core.einvoicing import einvoicing_enabled\nfrom core.jobs import enqueue\n"
        "from ..models import Job\nfrom . import fakes\n",
        encoding="utf-8",
    )
    found = [n for n in _imported_modules(file, "core.ports") if _is_myinvois(n)] + [
        n for n in _dynamic_imports(file) if _is_myinvois(n)
    ]
    assert found == []
