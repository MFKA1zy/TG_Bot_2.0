"""Profile/leaderboard timings on synthetic temporary data; no network."""
import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database.connection import init_db
from database.economy_repository import EconomyRepository
from database.reviews_repository import now


async def measure(action):
    samples=[]
    for _ in range(60):
        started=time.perf_counter()
        await action()
        samples.append((time.perf_counter()-started)*1000)
    return {"median_ms":round(statistics.median(samples),3),"p95_ms":round(sorted(samples)[56],3)}


async def run():
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/"benchmark.db"
        await init_db(path)
        repository=EconomyRepository(path)
        timestamp=now()
        async with repository.connection(True) as db:
            await db.executemany("INSERT INTO users(telegram_id,first_name,language,captcha_passed,created_at,points_balance,points_earned) VALUES (?,?, 'en',1,?,100,100)",[(user,f"User {user}",timestamp) for user in range(1,1201)])
            await db.executemany("INSERT INTO confirmed_views(user_id,series_id,confirmed_at,reward_eligible) VALUES (?,?,?,1)",[(user,series,timestamp) for user in range(1,1201) for series in range(1,11)])
            await db.executemany("INSERT INTO points_ledger(user_id,operation,amount,reason,series_id,created_at,idempotency_key,balance_after) VALUES (?,'view',10,'view',?,?,?,?)",[(user,series,timestamp,f"view:{user}:{series}",series*10) for user in range(1,1201) for series in range(1,11)])
            await db.executemany("INSERT INTO favorites VALUES (?,?,?)",[(user,series,timestamp) for user in range(1,1201) for series in range(1,6)])
            await db.executemany("INSERT INTO ratings VALUES (?,?,8,?)",[(user,series,timestamp) for user in range(1,1201) for series in range(1,4)])
        return {"users":1200,"views":12000,"ledger_operations":12000,"repeats":60,"network":False,
            "profile":await measure(lambda:repository.profile(600)),
            "leaderboard_first_page":await measure(lambda:repository.leaderboard(600,0)),
            "leaderboard_last_page":await measure(lambda:repository.leaderboard(600,119)),
            "points_history":await measure(lambda:repository.history(600))}


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path)
    arguments=parser.parse_args()
    result=json.dumps(asyncio.run(run()),indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True,exist_ok=True)
        arguments.output.write_text(result+"\n",encoding="utf-8")
    print(result)
