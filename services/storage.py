"""Seed a new persistent volume while preserving every existing file."""
from config import settings


def prepare_storage() -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    for destination in (settings.SERIES_PATH, settings.SETTINGS_PATH, settings.CAPTCHA_ANSWERS_PATH):
        source = settings.BASE_DIR / "data" / destination.name
        if source.resolve() == destination.resolve() or destination.exists():
            continue
        if not source.is_file():
            raise RuntimeError(f"Missing initial data file: {source.name}")
        content = source.read_bytes()
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as output:
                output.write(content)
        except FileExistsError:
            pass
