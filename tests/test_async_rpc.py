import asyncio
import logging
import multiprocessing
import tempfile
import time

import pytest

from zrpc.asyncio.server import Server, rpc_method
from zrpc.asyncio.client import Client, RPCTimeoutError


@pytest.fixture
def socket_dir():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir


def test_client_multicall_reliability(socket_dir):
    number_of_clients = 10

    crash_signal = multiprocessing.Event()
    crashed_signal = multiprocessing.Event()
    success_signal = multiprocessing.Event()

    def run_server():
        async def coro():
            class MockServer(Server):
                @rpc_method
                async def mock_method(self, *_, **__):
                    await asyncio.sleep(0.5)
                    return {"success": True}
            await MockServer(name="mock_server", socket_dir=socket_dir).run()
        asyncio.run(coro())

    def run_client():
        client = Client(socket_dir=socket_dir, retry_timeout=0.1)
        async def coro():
            async def subcoro():
                result = await client.call('mock_server',
                                           'mock_method')
                assert result['success']
                crash_signal.set()
                await asyncio.get_event_loop().run_in_executor(None, crashed_signal.wait)

                result = await client.call('mock_server',
                                           'mock_method')
                assert result['success']
                success_signal.set()
            await asyncio.gather(*[subcoro() for _ in range(number_of_clients)])
        asyncio.run(coro())

    server_process = multiprocessing.Process(target=run_server)
    server_process.start()
    client_process = multiprocessing.Process(target=run_client)
    client_process.start()
    assert crash_signal.wait(1)

    server_process.terminate()
    server_process.join()
    crashed_signal.set()

    server_process = multiprocessing.Process(target=run_server)
    server_process.start()

    try:
        assert success_signal.wait(1)
    finally:
        server_process.terminate()
        client_process.terminate()
        server_process.join()
        client_process.join()

