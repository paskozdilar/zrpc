import asyncio
import concurrent.futures
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
    """
    1. Run async server that sleeps for 0.5 seconds before returning
    2. Run 10 tasks using the same async client
    3. Crash server and restart
    4. Run 10 tasks using the same async client again
    """
    number_of_clients = 20

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=number_of_clients + 2)

    server_start = multiprocessing.Barrier(parties=number_of_clients + 2)
    client_success = multiprocessing.Barrier(parties=number_of_clients + 1)

    def run_server():
        async def coro():
            class MockServer(Server):
                @rpc_method
                async def mock_method(self, n):
                    await asyncio.sleep(0.5)
                    return {"success": True}
            logging.info('Starting server')
            await asyncio.get_event_loop().run_in_executor(executor, server_start.wait, 1)
            logging.info('Running server')
            await MockServer(name="mock_server", socket_dir=socket_dir).run()
        asyncio.run(coro())

    def run_client():
        async def coro():
            client = Client(socket_dir=socket_dir, retry_timeout=1)
            async def subcoro(n):
                await asyncio.get_event_loop().run_in_executor(executor, server_start.wait, 1)

                logging.info('[%d] Call mock server', n)
                result = await client.call('mock_server',
                                           'mock_method',
                                           args=[n])
                logging.info('[%d] Call mock server success', n)
                assert result['success']

                await asyncio.get_event_loop().run_in_executor(executor, client_success.wait, 1)
                await asyncio.get_event_loop().run_in_executor(executor, server_start.wait, 1)

                logging.info('[%d] Call mock server again', n)
                result = await client.call('mock_server',
                                           'mock_method',
                                           args=[n])
                logging.info('[%d] Call mock server again success', n)
                assert result['success']

                await asyncio.get_event_loop().run_in_executor(executor, client_success.wait, 1)

            await asyncio.gather(*[subcoro(n) for n in range(number_of_clients)])
        asyncio.run(coro())

    server_process = multiprocessing.Process(target=run_server)
    client_process = multiprocessing.Process(target=run_client)
    server_process.start()
    client_process.start()

    try:
        server_start.wait(3)
        client_success.wait(3)
    except:
        server_process.terminate()
        client_process.terminate()
        server_process.join()
        client_process.join()
        raise

    server_process.terminate()
    server_process.join()

    server_process = multiprocessing.Process(target=run_server)
    server_process.start()

    server_start.wait(3)

    try:
        client_success.wait(3)
    finally:
        server_process.terminate()
        client_process.terminate()
        server_process.join()
        client_process.join()

