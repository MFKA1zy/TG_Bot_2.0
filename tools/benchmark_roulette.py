"""Reproducible local SQLite timings on temporary data; no Telegram requests."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database.connection import init_db
from database.roulette_repository import RouletteRepository
from tools.benchmark_points import measure


async def run():
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'users.db'
        await init_db(path)
        repo=RouletteRepository(path,randbelow=lambda n:75)
        async with repo.connection(True) as db:
            await db.executemany("INSERT INTO users(telegram_id,first_name,language,captcha_passed,points_balance,points_earned) VALUES (?,?,'en',1,0,0)",[(i,f'User {i}') for i in range(1,1261)])
            ledger=[]
            spins=[]
            for uid in range(1,1201):
                for day in range(1,11):
                    key=f'seed:{uid}:{day}'
                    index=len(ledger)+1
                    timestamp=f'2026-01-{day:02d}T00:00:00+00:00'
                    deadline=f'2026-01-{day+1:02d}T00:00:00+00:00'
                    ledger.append((index,uid,timestamp,'roulette:'+key))
                    spins.append((uid,timestamp,deadline,index,key))
            await db.executemany("INSERT INTO points_ledger(id,user_id,operation,amount,reason,created_at,idempotency_key,balance_after) VALUES (?,?,'roulette_reward',0,'roulette',?,?,0)",ledger)
            await db.executemany("INSERT INTO roulette_spins(user_id,prize_id,awarded_points,spun_at,next_available_at,transaction_id,request_key) VALUES (?,1,0,?,?,?,?)",spins)
            await db.execute('DROP INDEX roulette_user_history')
        before={'status':await measure(lambda:repo.status(600)), 'history':await measure(lambda:repo.history(600))}
        async with repo.connection(True) as db:
            await db.execute('CREATE INDEX roulette_user_history ON roulette_spins(user_id,id DESC)')
        after={'status':await measure(lambda:repo.status(600)), 'history':await measure(lambda:repo.history(600))}
        users=iter(range(1201,1261))
        async def spin():
            uid=next(users)
            await repo.spin(uid,f'new:{uid}')
        return {'users':1260,'initial_spins':12000,'repeats':60,'network':False,
            'baseline_without_history_index':before,'with_history_index':after,
            'atomic_new_spin':await measure(spin),'idempotent_replay':await measure(lambda:repo.spin(1201,'new:1201')),
            'notes':'Repository wall-clock timings include connection and transaction costs; exclude Telegram latency and 150ms animation.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=json.dumps(asyncio.run(run()),indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(result+'\n',encoding='utf-8')
    print(result)
