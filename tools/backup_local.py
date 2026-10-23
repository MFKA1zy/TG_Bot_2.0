"""Create a local snapshot without copying environment secrets."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import subprocess


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / ".local-backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target.mkdir(parents=True)
    diff = subprocess.run(["git", "diff", "--binary", "HEAD"], cwd=root, check=True, capture_output=True).stdout
    (target / "working-tree.patch").write_bytes(diff)
    sources = [source for directory in ("data", "database") for source in (root / directory).rglob("*")]
    for source in sources:
        if not source.is_file() or source.suffix not in {".json", ".db"}:
            continue
        destination = target / source.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".db":
            with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as db:
                with sqlite3.connect(destination) as backup:
                    db.backup(backup)
        else:
            shutil.copy2(source, destination)
    print(target)


if __name__ == "__main__":
    main()
