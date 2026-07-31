"""Runnable demo of the ScorpioWatch ingestion slice.

    FilesystemAdapter  ->  EventBus  ->  a subscriber that prints each Event

Usage:
    uv run python examples/local-dev/watch_demo.py [DIR]

Watches DIR (default: the current directory) and prints a line for every filesystem
change until you press Ctrl+C. Edit, create, or delete a file under DIR in another
terminal to see events arrive.
"""

import asyncio
import sys
from pathlib import Path

from swatch.adapters.filesystem import FilesystemAdapter
from swatch.core.events import BackpressureStrategy, EventBus


async def main(root: Path) -> None:
    bus = EventBus(maxsize=256, backpressure=BackpressureStrategy.BLOCK)
    adapter = FilesystemAdapter(root, debounce_ms=200, step_ms=50)

    async def subscriber() -> None:
        async for event in bus.subscribe():
            print(f"{event.timestamp:%H:%M:%S}  {event.type:<9}  {event.payload['path']}")

    async def pump() -> None:
        async for event in adapter.events():
            await bus.publish(event)

    print(f"watching {root} — edit a file under it, or press Ctrl+C to stop")
    await adapter.start()
    async with asyncio.TaskGroup() as tg:
        tg.create_task(subscriber())
        tg.create_task(pump())


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    try:
        asyncio.run(main(target))
    except KeyboardInterrupt:
        print("\nstopped")
