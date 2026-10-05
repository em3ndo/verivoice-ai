"""Shared duplex streaming: send audio while receiving results, then drain."""
import asyncio
import json
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

async def stream_messages(socket, chunks: AsyncIterable[bytes], finish: dict,
                          terminal_type: str | None = None,
                          drain_timeout: float = 30) -> AsyncIterator[dict[str, Any]]:
    async def send():
        async for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise TypeError("Audio chunks must be bytes.")
            if chunk:
                await socket.send(chunk)
        await socket.send(json.dumps(finish))

    sender = asyncio.create_task(send())
    receiver = None
    deadline = None
    try:
        while True:
            receiver = asyncio.create_task(socket.recv())
            if not sender.done():
                done, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
                if sender in done:
                    await sender  # Propagate source/send failure immediately.
                    deadline = asyncio.get_running_loop().time() + drain_timeout
            elif deadline is None:
                await sender
                deadline = asyncio.get_running_loop().time() + drain_timeout
            if deadline is None:
                raw = await receiver
            else:
                remaining = deadline - asyncio.get_running_loop().time()
                raw = await asyncio.wait_for(receiver, max(remaining, 0.001))
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError("Provider returned a non-object message.")
            yield message
            if terminal_type and message.get("type") == terminal_type:
                return
    finally:
        for task in (sender, receiver):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (sender, receiver) if t is not None), return_exceptions=True)
