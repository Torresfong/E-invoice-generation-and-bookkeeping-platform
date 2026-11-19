"""The invoice engine (`core`) and the MyInvois module (`myinvois`) stay on their own sides of D59 (1), D61 (3), D61 (4).

`core` is the invoice engine and imports nothing from `myinvois`. It reaches LHDN only through the gateway port
(`core.ports`) and the job runner (`core.jobs`), and asks only `core.einvoicing.einvoicing_enabled`. `myinvois` may read
`core`. The check reads the source, so a file added by a later ticket is covered without anyone remembering to list it.
"""

import ast
import json
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


def _stray_code_list_files(root):
    """Files under `root` named like one of the code lists in `core/lhdn_codes/`, in any format (#173).

    The names come from the Core manifest, so a refreshed or added list is covered, and an unrelated JSON fixture is not.
    """
    sources = json.loads((ENGINE / "lhdn_codes" / "manifest.json").read_text(encoding="utf-8"))
    stems = {Path(entry["filename"]).stem.lower() for entry in sources["lists"].values()} | {"manifest"}
    return sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and path.stem.lower() in stems
    )


# --- what myinvois may read of core (#47, added as an acceptance criterion from the #128 review) -------------------
#
# `core.lhdn_codes` and `core.lhdn_fields` predate this check (#122, #172) and are already documented doors in
# docs/architecture/myinvois-module.md; this whitelist does not reopen that decision, only enforces it alongside
# the three doors D61 (4) names (the gateway port, the job runner, `einvoicing_enabled`) and the specific core
# models myinvois reads (the invoice and its buyer snapshot; `TenantModel`, so its own tables carry a company id
# the same way every other tenant table does).

ALLOWED_CORE_MODULES = {"core.ports", "core.jobs", "core.einvoicing", "core.lhdn_codes", "core.lhdn_fields"}
ALLOWED_CORE_MODELS = {"Invoice", "TenantModel"}


def _myinvois_source_files():
    root = BACKEND / "myinvois"
    return sorted(
        p for p in root.rglob("*.py")
        if "migrations" not in p.parts and "__pycache__" not in p.parts and p.relative_to(root).parts[0] != "tests"
    )


def _core_leaf_imports(path, package):
    """Every `core...` name this file imports, resolved to its full dotted path; never the bare base of a
    `from core.x import y`, only the leaf `core.x.y` it actually names."""
    leaves = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            leaves += [alias.name for alias in node.names if alias.name == "core" or alias.name.startswith("core.")]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            if base == "core" or base.startswith("core."):
                leaves += [f"{base}.{alias.name}" for alias in node.names]
    return leaves


def _core_violations(leaves):
    violations = []
    for leaf in leaves:
        parts = leaf[len("core."):].split(".") if leaf.startswith("core.") else []
        if not parts:
            violations.append(leaf)  # the bare package: no visibility into what is actually used
        elif parts[0] == "models":
            if len(parts) < 2 or parts[1] not in ALLOWED_CORE_MODELS:
                violations.append(leaf)
        elif f"core.{parts[0]}" not in ALLOWED_CORE_MODULES:
            violations.append(leaf)
    return violations


@pytest.mark.parametrize("path", _myinvois_source_files(), ids=lambda p: str(p.relative_to(BACKEND)))
def test_myinvois_reads_only_its_whitelisted_doors_into_core(path):
    violations = _core_violations(_core_leaf_imports(path, _package_of(path)))
    assert violations == [], f"{path.relative_to(BACKEND)} reaches outside the core whitelist: {violations}"


def test_the_whitelist_catches_an_import_of_a_core_module_outside_it(tmp_path):
    file = tmp_path / "probe.py"
    file.write_text("from core import views\n", encoding="utf-8")

    assert _core_violations(_core_leaf_imports(file, "myinvois")) == ["core.views"]


def test_the_whitelist_catches_a_core_model_outside_the_two_named(tmp_path):
    file = tmp_path / "probe.py"
    file.write_text("from core.models import Customer\n", encoding="utf-8")

    assert _core_violations(_core_leaf_imports(file, "myinvois")) == ["core.models.Customer"]


def test_the_whitelist_catches_the_bare_core_package(tmp_path):
    file = tmp_path / "probe.py"
    file.write_text("import core\n", encoding="utf-8")

    assert _core_violations(_core_leaf_imports(file, "myinvois")) == ["core"]


def test_the_whitelist_lets_every_named_door_through(tmp_path):
    file = tmp_path / "probe.py"
    file.write_text(
        "from core import ports, jobs, einvoicing\nfrom core.ports import myinvois as gw\n"
        "from core.jobs import register\nfrom core.models import Invoice, TenantModel\n"
        "from core.lhdn_codes import load\nfrom core.lhdn_fields import phone_ok\n",
        encoding="utf-8",
    )

    assert _core_violations(_core_leaf_imports(file, "myinvois")) == []


def test_myinvois_holds_no_second_copy_of_the_lhdn_code_lists():
    """The code-list data lives only in `core/lhdn_codes/`, so a refresh has one target and copies cannot drift (#173)."""
    assert _stray_code_list_files(BACKEND / "myinvois") == []


@pytest.mark.parametrize("name", ["TaxTypes.json", "taxtypes.csv", "manifest.json", "CountryCodes.JSON"])
def test_the_copy_check_names_a_code_list_in_any_format(tmp_path, name):
    (tmp_path / "codes").mkdir()
    (tmp_path / "codes" / name).write_text("[]", encoding="utf-8")

    assert _stray_code_list_files(tmp_path) == [f"codes/{name}"]


def test_the_copy_check_lets_an_unrelated_fixture_stand(tmp_path):
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "gateway_response.json").write_text("{}", encoding="utf-8")

    assert _stray_code_list_files(tmp_path) == []
