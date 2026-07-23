from __future__ import annotations

import asyncio


class JobQueue:
    def __init__(self, *, max_size: int = 100) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max_size)

    async def put(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def get(self) -> str:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    async def join(self) -> None:
        await self._queue.join()

    @property
    def size(self) -> int:
        return self._queue.qsize()

