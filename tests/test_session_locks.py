import asyncio
import unittest

from session_locks import get_session_lock


class SessionLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_phone_reuses_lock(self):
        self.assertIs(get_session_lock("+10000000001"), get_session_lock("+10000000001"))

    async def test_different_phones_have_independent_locks(self):
        first = get_session_lock("+10000000002")
        second = get_session_lock("+10000000003")
        self.assertIsNot(first, second)

    async def test_same_session_operations_are_serialized(self):
        lock = get_session_lock("+10000000004")
        events = []

        async def operation(name):
            async with lock:
                events.append(f"{name}:start")
                await asyncio.sleep(0.02)
                events.append(f"{name}:end")

        await asyncio.gather(operation("one"), operation("two"))
        self.assertEqual(
            events,
            ["one:start", "one:end", "two:start", "two:end"],
        )

    async def test_different_sessions_can_run_concurrently(self):
        first = get_session_lock("+10000000005")
        second = get_session_lock("+10000000006")
        events = []

        async def operation(name, lock):
            async with lock:
                events.append(f"{name}:start")
                await asyncio.sleep(0.02)
                events.append(f"{name}:end")

        await asyncio.gather(operation("one", first), operation("two", second))
        self.assertEqual(set(events), {"one:start", "one:end", "two:start", "two:end"})
        self.assertLess(events.index("one:end"), 4)
        self.assertLess(events.index("two:end"), 4)


if __name__ == "__main__":
    unittest.main()
