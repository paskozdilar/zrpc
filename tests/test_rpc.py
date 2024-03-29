import multiprocessing
import os
import sys
import tempfile
import time

import pytest

from zrpc.server import Server, rpc_method
from zrpc.client import Client, RPCTimeoutError


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
        mock_client = Client(socket_dir=socket_dir)
        response = mock_client.call('mock_server', 'mock_method')
        if response['success']:
            rpc_event.set()

    multiprocessing.Process(target=run_server, daemon=True).start()
    multiprocessing.Process(target=run_client, daemon=True).start()

    assert rpc_event.wait(1)


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
        client = Client(socket_dir=socket_dir, retry_timeout=0.1)
        response = client.call('mock_server', 'mock_method')
        if response['success']:
            rpc_event.set()

    multiprocessing.Process(target=run_server, daemon=True).start()
    multiprocessing.Process(target=run_client, daemon=True).start()
    assert crash_event.wait(1)
    multiprocessing.Process(target=run_server, daemon=True).start()

    # Wait 5 seconds for retry
    assert rpc_event.wait(5)


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
        client = Client(socket_dir=socket_dir)
        response = client.call('mock_server', 'mock_method')
        if response['success']:
            rpc_event.set()

    multiprocessing.Process(target=run_server, daemon=True).start()
    client_process = multiprocessing.Process(target=run_client, daemon=True)
    client_process.start()

    assert server_recv_event.wait(1)
    client_process.kill()
    client_process.join()
    client_crash_event.set()

    multiprocessing.Process(target=run_client, daemon=True).start()

    # Wait 5 seconds for retry
    assert rpc_event.wait(5)


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
        client = Client(socket_dir=socket_dir)
        response = client.call(server='mock_server',
                               method='mock_method',
                               args=('1', '2'),
                               kwargs={'kwarg1': 3, 'kwarg2': 4})
        if response['success']:
            rpc_event.set()

    multiprocessing.Process(target=run_server, daemon=True).start()
    multiprocessing.Process(target=run_client, daemon=True).start()

    assert rpc_event.wait(5)


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
        mock_client = Client(socket_dir=socket_dir)
        response = mock_client.call('mock_server', 'mock_method')
        if response['success']:
            rpc_event.set()

    multiprocessing.Process(target=run_server, daemon=True).start()
    multiprocessing.Process(target=run_client, daemon=True).start()

    assert rpc_event.wait(1)


def test_client_timeout(socket_dir):
    """
    Test whether client raises RPCTimeoutError on `call()` timeout.
    """
    client_event = multiprocessing.Event()

    def run_client():
        client = Client(socket_dir=socket_dir)
        try:
            client.call('non_existant', 'non_existant', timeout=0.1)
        except RPCTimeoutError:
            client_event.set()

    multiprocessing.Process(target=run_client, daemon=True).start()

    assert client_event.wait(1)


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

    multiprocessing.Process(target=run_server_n_times,
                            args=[number_of_restarts],
                            daemon=True).start()

    for i in range(number_of_restarts):
        client = Client(socket_dir=socket_dir)
        response = client.call('mock_server', 'mock_method', timeout=3)
        assert response['success']


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

    multiprocessing.Process(target=run_server, daemon=True).start()

    # Retry 10 times
    client = Client(socket_dir=socket_dir, retry_timeout=0.01)
    client.call(server='mock_server', method='mock_method', timeout=1)

    # Assert method executed only once
    assert counter.value == 1


def test_server_stop(socket_dir):

    event = multiprocessing.Event()

    class MockServer(Server):
        @rpc_method
        def mock_method(self):
            pass
        def __exit__(self, *args, **kwargs):
            super().__exit__(*args, **kwargs)
            event.set()

    def run_server():
        MockServer(socket_dir=socket_dir).run()

    p = multiprocessing.Process(target=run_server, daemon=True)
    p.start()

    client = Client(socket_dir=socket_dir)
    client.call(server='mock_server', method='mock_method', timeout=1)

    os.kill(p.pid, 2)
    p.join(timeout=1)
    assert event.is_set()


def test_server_register(socket_dir):

    queue = multiprocessing.Queue()
    recv_event = multiprocessing.Event()

    def run_server():
        class TestServer(Server):
            def run(self):
                self.register(queue._reader.fileno(), self.queue_event)
                super().run()
            def queue_event(self):
                assert queue.get() == 'foo'
                recv_event.set()
        TestServer().run()

    p = multiprocessing.Process(target=run_server)
    p.start()

    queue.put('foo')
    assert recv_event.wait(1)

    p.terminate()
    p.join()
