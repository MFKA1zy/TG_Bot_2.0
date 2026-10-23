"""Start a separately provisioned test bot; never load the production environment."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def isolated_environment(env_file):
    env_file = Path(env_file).resolve()
    if env_file == ROOT / '.env' or not env_file.is_file():
        raise ValueError('Use a separate existing .env.test file')
    values = dict(dotenv_values(env_file, interpolate=False))
    production = dotenv_values(ROOT / '.env', interpolate=False)
    token = values.get('BOT_TOKEN', '')
    if not token or ':' not in token or token == production.get('BOT_TOKEN') or token == os.getenv('BOT_TOKEN'):
        raise ValueError('Set a separate BotFather test token in the test file')
    data = Path(values.get('DATA_DIR') or ROOT / '.staging-data').resolve()
    forbidden = {ROOT / 'data', ROOT / 'database', Path('/data').resolve()}
    prod_data = production.get('DATA_DIR') or os.getenv('DATA_DIR')
    if prod_data:
        forbidden.add(Path(prod_data).resolve())
    for source in forbidden:
        if data == source or data in source.parents or source in data.parents:
            raise ValueError('Test DATA_DIR must be separate from production data')
    for raw in (production.get('DB_PATH'), os.getenv('DB_PATH')):
        if raw:
            production_db=Path(raw).resolve()
            if production_db==data/'users.db' or data in production_db.parents:
                raise ValueError('Test directory contains the production database path')
    if values.get('DB_PATH') and Path(values['DB_PATH']).resolve() != data / 'users.db':
        raise ValueError('Test DB_PATH must be DATA_DIR/users.db')
    if values.get('LOGS_DIR') and Path(values['LOGS_DIR']).resolve() != data / 'logs':
        raise ValueError('Test LOGS_DIR must be DATA_DIR/logs')
    # Remove inherited application secrets, aliases, and config. Load only this file.
    keys = set(dotenv_values(ROOT / '.env.example')) | set(production)
    keys |= {'ENV_FILE', 'CHANNEL_ID', 'CHANNEL_URL', 'TELEGRAM_CHANNEL_ID',
             'TELEGRAM_CHANNEL_URL', 'REQUIRE_SUBSCRIPTION', 'MANDATORY_SUBSCRIPTION'}
    env = {key:value for key,value in os.environ.items() if key not in keys}
    env.update({key:value for key,value in values.items() if value is not None})
    env.update(ENV_FILE=str(env_file), DATA_DIR=str(data), DB_PATH=str(data/'users.db'),
               LOGS_DIR=str(data/'logs'))
    return env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, default=ROOT/'.env.test')
    parser.add_argument('--check', action='store_true', help='Validate isolation without network or writes')
    args = parser.parse_args()
    try:
        env = isolated_environment(args.env_file)
    except ValueError as error:
        parser.error(str(error))
    if args.check:
        print('Staging environment isolation: OK (token not displayed)')
        return
    raise SystemExit(subprocess.call([sys.executable, str(ROOT/'main.py')], cwd=ROOT, env=env))


if __name__ == '__main__':
    main()
