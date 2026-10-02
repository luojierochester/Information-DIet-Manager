"""Keep synchronous work owned until it finishes, even after caller cancellation."""
from __future__ import annotations

import asyncio


async def run_owned_sync(factory, *, on_cancel=None, **kwargs):
    # Cancelling a coroutine cannot stop its OS thread. Shield the worker so
    # callers retain their admission/model gates until the thread really exits.
    worker = asyncio.create_task(asyncio.to_thread(factory, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            try:
                result = worker.result()
            except Exception:
                pass  # The worker is responsible for its own failure cleanup.
            else:
                if on_cancel is not None:
                    on_cancel(result)
        raise
