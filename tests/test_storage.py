from types import SimpleNamespace
from pathlib import Path
import os
import subprocess
import sys

import services.storage as storage


def test_volume_seeding_preserves_existing_files(tmp_path, monkeypatch):
    bundled = tmp_path / "app"
    volume = tmp_path / "volume"
    (bundled / "data").mkdir(parents=True)
    paths = {}
    for name, attribute in [("series.json", "SERIES_PATH"), ("settings.json", "SETTINGS_PATH"), ("captcha_answers.json", "CAPTCHA_ANSWERS_PATH")]:
        (bundled / "data" / name).write_text('{"seed": true}', encoding="utf-8")
        paths[attribute] = volume / name
    monkeypatch.setattr(storage, "settings", SimpleNamespace(BASE_DIR=bundled, DATA_DIR=volume, **paths))
    storage.prepare_storage()
    assert all(path.read_text() == '{"seed": true}' for path in paths.values())
    paths["SERIES_PATH"].write_text('{"existing": true}')
    storage.prepare_storage()
    assert paths["SERIES_PATH"].read_text() == '{"existing": true}'


def test_full_startup_on_new_volume_without_telegram_network(tmp_path):
    env = os.environ.copy()
    volume = tmp_path / "volume"
    env.update(BOT_TOKEN="12345:TEST", ADMIN_IDS="", TELEGRAM_PROXY="", DATA_DIR=str(volume),
               DB_PATH=str(volume / "users.db"), LOGS_DIR=str(volume / "logs"))
    script = '''
import asyncio, json, sqlite3
from aiogram import Dispatcher
import main
from config import settings

async def fake_polling(self, bot, **kwargs):
    assert settings.DB_PATH.is_file()
    assert settings.SERIES_PATH.parent == settings.DATA_DIR
    assert settings.LOGS_DIR.parent == settings.DATA_DIR
    catalog = json.loads(settings.SERIES_PATH.read_text(encoding="utf-8"))
    assert catalog["schema_version"] == 2
    with sqlite3.connect(settings.DB_PATH) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0
    print("SMOKE_OK")

Dispatcher.start_polling = fake_polling
asyncio.run(main.main())
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "SMOKE_OK" in result.stdout
