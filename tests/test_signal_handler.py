import logging
import multiprocessing
import signal
import tempfile
import time

import pytest

from zrpc.signal_handler import SignalHandler
from zrpc.server import Server, rpc_method
from zrpc.client import Client


@pytest.fixture
def socket_dir():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir


def test_sigterm(socket_dir):
    """
    Run a basic mock tests.
    """
    signal_event = multiprocessing.Event()

    def run_server():
        signal_handler = SignalHandler(signal.SIGTERM)
        signal_socket = signal_handler.get_socket()

        class MockServer(Server):
            @rpc_method
            def mock_method(self):
                return {'success': True}
            def signal_handler(self):
                signum = signal_socket.recv().decode()
                if signum == 'SIGTERM':
                    signal_event.set()
                    self.stop()

        server = MockServer(socket_dir=socket_dir)
        server.register(fd=signal_socket,
                        callback=server.signal_handler)

        with signal_handler, server:
            server.run()

    p = multiprocessing.Process(target=run_server)
    p.start()
    mock_client = Client(socket_dir=socket_dir)
    mock_client.call('mock_server', 'mock_method', timeout=3)
    p.terminate()

    assert signal_event.wait(1)


def test_sigchld(socket_dir):
    """
    Run a basic mock tests.
    """
    signal_handler = SignalHandler(signal.SIGCHLD)
    signal_socket = signal_handler.get_socket()

    class MockServer(Server):
        @rpc_method
        def mock_method(self):
            return {'success': True}
        def signal_handler(self):
            signum = signal_socket.recv().decode()
            if signum == 'SIGCHLD':
                self.stop()

    server = MockServer(socket_dir=socket_dir)
    server.register(fd=signal_socket,
                    callback=server.signal_handler)

    with signal_handler, server:
        p = multiprocessing.Process(target=lambda: None)
        p.start()
        server.run()
        p.join()
