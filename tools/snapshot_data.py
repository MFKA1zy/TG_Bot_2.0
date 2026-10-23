"""Owner-operated consistent backup after stopping all application writers."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from tools.migration_preflight import JSON_FILES,database_snapshot


def snapshot(db_path,data_dir,output,*,writers_stopped=False):
    if not writers_stopped:
        raise ValueError('Stop all application writers before taking a cross-file snapshot')
    db_path=Path(db_path).resolve(strict=True)
    data_dir=Path(data_dir).resolve(strict=True)
    output=Path(output).resolve()
    if output.exists() or output in data_dir.parents or data_dir in output.parents or output==data_dir:
        raise ValueError('Use a new backup directory outside DATA_DIR')
    output.mkdir(parents=True,exist_ok=False)
    with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as source:
        with sqlite3.connect(output/'users.db') as target:
            source.backup(target)
    for name in JSON_FILES:
        if (data_dir/name).is_file():
            shutil.copy2(data_dir/name,output/name)
    tables=database_snapshot(output/'users.db')
    hashes={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in output.iterdir() if path.is_file()}
    report={'integrity':'ok','counts':{name:len(table['rows']) for name,table in tables.items()},'sha256':hashes}
    (output/'backup-manifest.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True,type=Path)
    parser.add_argument('--data-dir',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--writers-stopped',action='store_true')
    args=parser.parse_args()
    print(json.dumps(snapshot(args.db,args.data_dir,args.output,writers_stopped=args.writers_stopped),indent=2))
