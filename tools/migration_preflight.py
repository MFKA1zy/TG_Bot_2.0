"""Migrate an isolated snapshot only. Source SQLite is opened read-only via backup API."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
JSON_FILES=('series.json','settings.json','captcha_answers.json','.message_cleanup.json')


def require(condition,message):
    if not condition:
        raise ValueError(message)


def database_snapshot(path):
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('SQLite integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('SQLite foreign key check failed')
        result={}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            quoted='"'+name.replace('"','""')+'"'
            cursor=db.execute('SELECT * FROM '+quoted)
            result[name]={'columns':[item[0] for item in cursor.description], 'rows':cursor.fetchall()}
        return result


def assert_preserved(before,after):
    for name,table in before.items():
        require(name in after,f'Table lost: {name}')
        positions=[after[name]['columns'].index(column) for column in table['columns']]
        rows={tuple(row[i] for i in positions) for row in after[name]['rows']}
        require(set(table['rows'])<=rows,f'Existing rows changed or lost: {name}')
        if name!='schema_migrations':
            require(len(table['rows'])==len(after[name]['rows']),f'Unexpected row count: {name}')


def run(source_db,source_data,output):
    source_db=Path(source_db).resolve(strict=True)
    source_data=Path(source_data).resolve(strict=True)
    output=Path(output).resolve()
    if output.exists() or output==source_data or output in source_data.parents or source_data in output.parents:
        raise ValueError('Output must be a new directory outside the source data directory')
    if output==ROOT or output in ROOT.parents or source_db==output/'users.db':
        raise ValueError('Unsafe output path')
    output.mkdir(parents=True,exist_ok=False)
    destination=output/'users.db'
    with sqlite3.connect(source_db.as_uri()+'?mode=ro',uri=True) as source:
        with sqlite3.connect(destination) as target:
            source.backup(target)
    original_json={}
    for name in JSON_FILES:
        path=source_data/name
        if path.exists():
            original_json[name]=json.loads(path.read_text(encoding='utf-8'))
            shutil.copy2(path,output/name)
    if 'series.json' not in original_json:
        raise ValueError('Source series.json is required; no empty catalogue will be substituted')
    before=database_snapshot(destination)
    script='''
import asyncio
from database.connection import init_db
from services.series_service import SeriesService
from config import settings
asyncio.run(init_db())
SeriesService(settings.SERIES_PATH).migrate_catalog()
'''
    env={key:value for key,value in os.environ.items() if key not in {'BOT_TOKEN','ENV_FILE','DATA_DIR','DB_PATH','LOGS_DIR'}}
    # Explicit file prevents config from loading production .env. No polling/network.
    (output/'preflight.env').write_text('BOT_TOKEN=\n',encoding='utf-8')
    env.update(ENV_FILE=str(output/'preflight.env'),BOT_TOKEN='',DATA_DIR=str(output),
               DB_PATH=str(destination),LOGS_DIR=str(output/'logs'))
    for _ in range(2):
        subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=env,check=True,capture_output=True)
        after=database_snapshot(destination)
        assert_preserved(before,after)
        if _==0:
            first=after
        else:
            require(after==first,'Second migration changed SQLite data')
    series=json.loads((output/'series.json').read_text(encoding='utf-8'))
    original=original_json['series.json']
    original_rows=original['series']
    migrated={row['id']:row for row in series['series']}
    require(len(original_rows)==len(migrated),'Catalogue row count changed')
    for row in original_rows:
        require(all(migrated[row['id']].get(key)==value for key,value in row.items()),'Existing catalogue data changed')
    for name,value in original_json.items():
        if name!='series.json':
            require(json.loads((output/name).read_text(encoding='utf-8'))==value,f'Changed JSON: {name}')
    report={'source_opened_read_only':True,'migrations_run':2,'integrity':'ok','existing_rows_preserved':True,
            'tables_before':{name:len(table['rows']) for name,table in before.items()},
            'tables_after':{name:len(table['rows']) for name,table in after.items()},
            'series_before':len(original_rows),'series_after':len(migrated),
            'snapshot_sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}
    (output/'preflight-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-db',required=True,type=Path)
    parser.add_argument('--source-data',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(run(args.source_db,args.source_data,args.output),indent=2))
