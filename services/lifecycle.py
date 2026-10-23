"""Bounded draining for aiogram 3.15 update tasks and the broadcast worker."""
import asyncio
import logging

logger=logging.getLogger(__name__)


async def drain_tasks(tasks,timeout=15):
    tasks={task for task in tasks if task is not asyncio.current_task() and not task.done()}
    if not tasks:
        return
    _,pending=await asyncio.wait(tasks,timeout=timeout)
    for task in pending:
        task.cancel()
    results=await asyncio.gather(*tasks,return_exceptions=True)
    logger.info('Shutdown drained %s tasks; cancelled %s.',len(tasks),len(pending))
    for result in results:
        if isinstance(result,Exception):
            logger.warning('Task ended during shutdown (%s).',type(result).__name__)


async def shutdown_workers(dispatcher,broadcast):
    broadcast.broadcast_service.cancel()
    # Pinned aiogram 3.15 keeps active update tasks here; no public drain API.
    tasks=set(dispatcher._handle_update_tasks)
    if broadcast._broadcast_task is not None:
        tasks.add(broadcast._broadcast_task)
    await drain_tasks(tasks)
    # A killed or timed-out worker is never automatically restarted/sent twice.
    await broadcast.repository.mark_broadcasts_interrupted()
