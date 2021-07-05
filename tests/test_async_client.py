import asyncio
import logging
import multiprocessing
import sys
import tempfile
import time

import pytest

from zrpc.server import Server, rpc_method
from zrpc.asyncio.client import Client, RPCTimeoutError


@pytest.fixture
def socket_dir():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir


def test_basic_rpc(socket_dir):
    """
    Run a basic mock tests.
    """
    rpc_event = multiprocessing.Event()

    def run_server():
        class MockServer(Server):
            @rpc_method
            def mock_method(self):
                return {'success': True}
        MockServer(socket_dir=socket_dir).run()

    def run_client():
        async def coro():
            mock_client = Client(socket_dir=socket_dir)
            response = await mock_client.call('mock_server', 'mock_method')
            if response['success']:
                rpc_event.set()
        asyncio.run(coro())

    ps = [multiprocessing.Process(target=run_server, daemon=True),
          multiprocessing.Process(target=run_client, daemon=True)]
    [p.start() for p in ps]

    assert rpc_event.wait(1)

    [p.terminate() for p in ps]
    [p.join() for p in ps]


def test_reliability_client(socket_dir):
    """
    Test client reliability on server crash.
    """
    crash_event = multiprocessing.Event()
    rpc_event = multiprocessing.Event()

    def run_server():

        class MockServer(Server):
            @rpc_method
            def mock_method(self):
                if not crash_event.is_set():
                    crash_event.set()
                    sys.exit(1)
                else:
                    return {'success': True}

        MockServer(socket_dir=socket_dir).run()

    def run_client():
        async def coro():
            client = Client(socket_dir=socket_dir, retry_timeout=0.1)
            response = await client.call('mock_server', 'mock_method')
            if response['success']:
                rpc_event.set()
        asyncio.run(coro())

    server_process = multiprocessing.Process(target=run_server, daemon=True)
    client_process = multiprocessing.Process(target=run_client, daemon=True)
    [p.start() for p in (server_process, client_process)]

    assert crash_event.wait(1)
    server_process.join()
    server_process = multiprocessing.Process(target=run_server, daemon=True)
    server_process.start()

    # Wait 3 seconds for retry
    assert rpc_event.wait(3)

    [p.terminate() for p in (server_process, client_process)]
    [p.join() for p in (server_process, client_process)]


def test_reliability_server(socket_dir):
    """
    Test server reliability on client crash.
    """
    rpc_event = multiprocessing.Event()
    server_recv_event = multiprocessing.Event()
    client_crash_event = multiprocessing.Event()

    def run_server():

        class MockServer(Server):
            @rpc_method
            def mock_method(self):
                server_recv_event.set()
                client_crash_event.wait()
                return {'success': True}

        MockServer(socket_dir=socket_dir).run()

    def run_client():
        async def coro():
            client = Client(socket_dir=socket_dir)
            response = await client.call('mock_server', 'mock_method')
            if response['success']:
                rpc_event.set()
        asyncio.run(coro())

    server_process = multiprocessing.Process(target=run_server, daemon=True)
    server_process.start()
    client_process = multiprocessing.Process(target=run_client, daemon=True)
    client_process.start()

    assert server_recv_event.wait(1)
    client_process.kill()
    client_process.join()
    client_crash_event.set()

    client_process = multiprocessing.Process(target=run_client, daemon=True)
    client_process.start()

    # Wait 5 seconds for retry
    assert rpc_event.wait(5)

    [p.terminate() for p in (server_process, client_process)]
    [p.join() for p in (server_process, client_process)]


def test_args_kwargs(socket_dir):
    """
    Test proper handling of arguments and keyword arguments.
    """
    rpc_event = multiprocessing.Event()

    def run_server():

        class MockServer(Server):
            @rpc_method
            def mock_method(self, arg1, arg2, kwarg1=None, kwarg2=None):
                if (arg1, arg2, kwarg1, kwarg2) == ('1', '2', 3, 4):
                    return {'success': True}
                else:
                    return {'success': False}

        MockServer(socket_dir=socket_dir).run()

    def run_client():
        async def coro():
            client = Client(socket_dir=socket_dir)
            response = await client.call(server='mock_server',
                                         method='mock_method',
                                         args=('1', '2'),
                                         kwargs={'kwarg1': 3, 'kwarg2': 4})
            if response['success']:
                rpc_event.set()
        asyncio.run(coro())

    ps = [multiprocessing.Process(target=run_server, daemon=True),
          multiprocessing.Process(target=run_client, daemon=True)]
    [p.start() for p in ps]

    assert rpc_event.wait(5)

    [p.terminate() for p in ps]
    [p.join() for p in ps]


def test_multiprocessing(socket_dir):
    """
    Test whether ZRPC works in multiprocessing environment.
    """
    rpc_event = multiprocessing.Event()

    class MockServer(Server):
        @rpc_method
        def mock_method(self):
            return {'success': True}

    def run_server():
        MockServer(socket_dir=socket_dir).run()

    def run_client():
        async def coro():
            mock_client = Client(socket_dir=socket_dir)
            response = await mock_client.call('mock_server', 'mock_method')
            if response['success']:
                rpc_event.set()
        asyncio.run(coro())

    ps = [multiprocessing.Process(target=run_server, daemon=True),
          multiprocessing.Process(target=run_client, daemon=True)]
    [p.start() for p in ps]

    assert rpc_event.wait(1)

    [p.terminate() for p in ps]
    [p.join() for p in ps]


def test_client_timeout(socket_dir):
    """
    Test whether client raises RPCTimeoutError on `call()` timeout.
    """
    client_event = multiprocessing.Event()

    def run_client():
        async def coro():
            client = Client(socket_dir=socket_dir)
            try:
                await client.call('non_existant', 'non_existant', timeout=0.1)
            except RPCTimeoutError:
                client_event.set()
        asyncio.run(coro())

    p = multiprocessing.Process(target=run_client, daemon=True)
    p.start()

    assert client_event.wait(1)

    p.terminate()
    p.join()


def test_server_restart(socket_dir):
    """
    Test whether server works properly on restart.
    """
    number_of_restarts = 5

    def run_server_n_times(n):

        class MockServer(Server):
            @rpc_method
            def mock_method(self):
                return {'success': True}

        server = MockServer(socket_dir=socket_dir)
        for i in range(n):
            with server:
                server.run_once()

    p = multiprocessing.Process(target=run_server_n_times,
                                args=[number_of_restarts],
                                daemon=True)
    p.start()

    for i in range(number_of_restarts):
        async def coro():
            client = Client(socket_dir=socket_dir)
            response = await client.call('mock_server', 'mock_method', timeout=3)
            assert response['success']
        asyncio.run(coro())

    p.terminate()
    p.join()


def test_server_cache(socket_dir):

    counter = multiprocessing.Value('i')
    counter.value = 0

    class MockServer(Server):
        @rpc_method
        def mock_method(self):
            time.sleep(0.1)
            counter.value += 1
            return {'success': True}

    def run_server():
        MockServer(socket_dir=socket_dir).run()

    p = multiprocessing.Process(target=run_server, daemon=True)
    p.start()

    # Retry 10 times
    async def coro():
        client = Client(socket_dir=socket_dir, retry_timeout=0.01)
        await client.call(server='mock_server', method='mock_method', timeout=1)

        # Assert method executed only once
        assert counter.value == 1
    asyncio.run(coro())

    p.terminate()
    p.join()


def test_client_multicall(socket_dir):

    def run_server(name):

        class MockServer(Server):
            @rpc_method
            def mock_method(self):
                time.sleep(0.1)
                return {'success': True}
        MockServer(name=name, socket_dir=socket_dir).run()

    number_of_servers = 10

    server_processes = [
        multiprocessing.Process(target=run_server,
                                args=['mock_server_' + str(i)],
                                daemon=True)
        for i in range(number_of_servers)
    ]
    [p.start() for p in server_processes]

    start_event = multiprocessing.Event()
    success_event = multiprocessing.Event()

    def run_client():
        async def coro():
            client = Client(socket_dir=socket_dir)

            start_event.set()
            responses = await asyncio.gather(
                *[client.call(server='mock_server_' + str(i),
                              method='mock_method')
                  for i in range(number_of_servers)]
            )
            for response in responses:
                assert response['success']
            success_event.set()
        asyncio.run(coro(), debug=True)

    client_process = multiprocessing.Process(target=run_client, daemon=True)
    client_process.start()
    start_event.wait()
    assert success_event.wait(0.5)

    [p.terminate() for p in server_processes + [client_process]]
    [p.join() for p in server_processes + [client_process]]
