from types import SimpleNamespace

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
