import pickle
from pathlib import Path

def ensure_parent_dir(file_path):
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def save_pickle(file_path, data):
    path = ensure_parent_dir(file_path)
    with path.open("wb") as f:
        pickle.dump(data, f)


def load_pickle(file_path, default=None):
    path = Path(file_path)
    if not path.exists():
        return default
    with path.open("rb") as f:
        return pickle.load(f)