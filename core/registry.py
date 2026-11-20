"""Plugin auto-discovery. Drop a .py file into sources/, signals/ or catalysts/ and
any subclass of that package's base class defined in it is registered automatically."""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import traceback


def discover(package: str, base_cls: type) -> list:
    pkg = importlib.import_module(package)
    found = []
    for mod_info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if mod_info.name.startswith("_") or mod_info.name == "base":
            continue
        try:
            mod = importlib.import_module(f"{package}.{mod_info.name}")
        except Exception:
            print(f"[registry] failed to load {package}.{mod_info.name}:\n{traceback.format_exc()}")
            continue
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, base_cls) and obj is not base_cls and obj.__module__ == mod.__name__ \
                    and not obj.__dict__.get("abstract", False):
                found.append(obj)
    return found
