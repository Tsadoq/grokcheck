import asyncio


async def produce(items: list[int]) -> int:
    await asyncio.sleep(0)
    items.append(1)
    await asyncio.sleep(0)
    items.append(2)
    return len(items)


async def main() -> None:
    a = asyncio.create_task(produce([]), name="A")
    b = asyncio.create_task(produce([]), name="B")
    await asyncio.gather(a, b)


asyncio.run(main())
