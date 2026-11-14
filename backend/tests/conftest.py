"""
Тести завжди працюють з окремою тимчасовою SQLite-базою.

Без цього резервний режим бази даних підключався до PostgreSQL, якщо на 5432
локально працював стенд Docker, і тести писали в його базу та навантажували його.
"""

import os
import tempfile

_db = os.path.join(tempfile.mkdtemp(prefix="kairo-tests-"), "kairo.db").replace("\\", "/")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db}"
