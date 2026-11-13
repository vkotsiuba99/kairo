"""
Підвантаження credentials.env для скриптів, які запускають поза docker-compose.

У контейнері змінні приходять через `env_file`, і цей модуль нічого не робить.
Локально ж скрипт стартує з голим оточенням, тому файл треба знайти самому —
він лежить у корені репозиторію, на рівень вище за backend/.
"""
import os
from pathlib import Path
from typing import Optional

CANDIDATES = ("credentials.env", ".env")


def find_env_file(start: Optional[Path] = None) -> Optional[Path]:
    here = (start or Path(__file__).resolve()).parent
    for directory in [here, *here.parents]:
        for name in CANDIDATES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def load_credentials(override: bool = False) -> Optional[Path]:
    """Читає key=value у os.environ. Наявні змінні оточення мають пріоритет."""
    path = find_env_file()
    if path is None:
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value
    return path
