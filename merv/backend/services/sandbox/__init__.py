"""Transitional shim — moved to backend.sandbox; deleted at de-shim.

The sys.modules alias covers package-level access; the meta-path finder
resolves old-path submodule imports (backend.services.sandbox.sandboxes,
...) to the SAME canonical backend.sandbox module objects, so no consumer
needs an edit and module identity is preserved.
"""

import importlib
import importlib.util
import sys

from ... import sandbox as _moved

_OLD = __name__
_NEW = _moved.__name__


class _AliasLoader:
    """Swap the freshly created alias module for the canonical one."""

    def __init__(self, target: str) -> None:
        self._target = target

    def create_module(self, spec):
        return None  # default fresh module; replaced in exec_module

    def exec_module(self, module) -> None:
        sys.modules[module.__name__] = importlib.import_module(self._target)


class _AliasFinder:
    """Meta-path finder mapping old dotted names onto the moved package."""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_OLD + "."):
            return None
        return importlib.util.spec_from_loader(
            fullname, _AliasLoader(_NEW + fullname[len(_OLD) :])
        )


sys.meta_path.insert(0, _AliasFinder())
sys.modules[__name__] = _moved
