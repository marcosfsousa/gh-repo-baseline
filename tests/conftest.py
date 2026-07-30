# tests/conftest.py

"""
Shared fixtures. The only interesting one is ``boot``.

``scripts/bootstrap-repo.py`` is named for the command line, not for the import
system — the hyphen makes it unimportable by name. It is loaded from its path
rather than renamed, because the name a person types is the more important of the
two and a test suite is a bad reason to change it.
"""

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "templates"


def _load(path, name):
    """
    Import a file by path under an explicit module name.

    Loaded as a module rather than ``exec``'d into a dict, which is what an
    earlier version of this did: ``exec`` leaves ``__file__`` undefined, and both
    files here compute paths from it at module level. A loader sets it.

    The name is explicit so the template copy cannot collide with the live
    ``tests/test_required_checks.py`` that pytest itself imports.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so a traceback inside the module resolves its own
    # frames instead of reporting them against a module that "does not exist".
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def boot():
    """``scripts/bootstrap-repo.py`` as a module."""
    return _load(REPO_ROOT / "scripts" / "bootstrap-repo.py", "bootstrap_repo")


@pytest.fixture(scope="session")
def template_guard():
    """
    ``templates/tests/test_required_checks.py`` as a module.

    Importing it does not run it as a test — nothing here calls its test classes.
    It is imported for its parser, so the two copies of that parser can be held
    to producing the same answer.
    """
    return _load(TEMPLATES / "tests" / "test_required_checks.py", "template_guard")
