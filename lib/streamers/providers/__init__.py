from importlib import import_module
from pkgutil import iter_modules
from pathlib import Path

# alle Module im providers-Package importieren
_package_dir = Path(__file__).resolve().parent

for _, module_name, _ in iter_modules([str(_package_dir)]):
    import_module(f"{__name__}.{module_name}")