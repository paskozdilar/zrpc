#!/usr/bin/env python3

import multiprocessing
import sys
import tempfile
import time

import pytest

from zrpc.server import Server, rpc_method
from zrpc.client import Client, RPCTimeoutError


def test_basic_rpc():
    """
    Run a basic mock tests.
    """
    rpc_event = multiprocessing.Event()

    with tempfile.TemporaryDirectory() as tempdir:

        def run_server():
            class MockServer(Server):
                @rpc_method
                def mock_method(self):
                    return {'success': True}
            MockServer(socket_dir=tempdir).run()

        def run_client():
            mock_client = Client(socket_dir=tempdir)
            response = mock_client.call('mock_server', 'mock_method')
            if response['success']:
                rpc_event.set()

        multiprocessing.Process(target=run_server, daemon=True).start()
        multiprocessing.Process(target=run_client, daemon=True).start()

        assert rpc_event.wait(1)


def test_reliability_client():
    """
    Test client reliability on server crash.
    """
    crash_event = multiprocessing.Event()
    rpc_event = multiprocessing.Event()

    with tempfile.TemporaryDirectory() as tempdir:

        def run_server():

            class MockServer(Server):
                @rpc_method
                def mock_method(self):
                    if not crash_event.wait(0):
                        crash_event.set()
                        sys.exit(1)
                    else:
                        return {'success': True}

            MockServer(socket_dir=tempdir).run()

        def run_client():
            client = Client(socket_dir=tempdir)
            response = client.call('mock_server', 'mock_method')
            if response['success']:
                rpc_event.set()

        multiprocessing.Process(target=run_server, daemon=True).start()
        multiprocessing.Process(target=run_client, daemon=True).start()
        assert crash_event.wait(1)
        multiprocessing.Process(target=run_server, daemon=True).start()

        # Wait 5 seconds for retry
        assert rpc_event.wait(5)


def test_reliability_server():
    """
    Test server reliability on client crash.
    """
    rpc_event = multiprocessing.Event()
    server_recv_event = multiprocessing.Event()
    client_crash_event = multiprocessing.Event()

    with tempfile.TemporaryDirectory() as tempdir:

        def run_server():

            class MockServer(Server):
                @rpc_method
                def mock_method(self):
                    server_recv_event.set()
                    client_crash_event.wait()
                    return {'success': True}

            MockServer(socket_dir=tempdir).run()

        def run_client():
            client = Client(socket_dir=tempdir)
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


def test_args_kwargs():
    """
    Test proper handling of arguments and keyword arguments.
    """
    rpc_event = multiprocessing.Event()

    with tempfile.TemporaryDirectory() as tempdir:

        def run_server():

            class MockServer(Server):
                @rpc_method
                def mock_method(self, arg1, arg2, kwarg1=None, kwarg2=None):
                    if (arg1, arg2, kwarg1, kwarg2) == ('1', '2', 3, 4):
                        return {'success': True}
                    else:
                        return {'success': False}

            MockServer(socket_dir=tempdir).run()

        def run_client():
            client = Client(socket_dir=tempdir)
            response = client.call(server='mock_server',
                                   method='mock_method',
                                   args=('1', '2'),
                                   kwargs={'kwarg1': 3, 'kwarg2': 4})
            if response['success']:
                rpc_event.set()

        multiprocessing.Process(target=run_server, daemon=True).start()
        multiprocessing.Process(target=run_client, daemon=True).start()

        assert rpc_event.wait(5)


def test_multiprocessing():
    """
    Test whether ZRPC works in multiprocessing environment.
    """
    rpc_event = multiprocessing.Event()

    with tempfile.TemporaryDirectory() as tempdir:

        class MockServer(Server):
            @rpc_method
            def mock_method(self):
                return {'success': True}

        def run_server():
            MockServer(socket_dir=tempdir).run()

        def run_client():
            mock_client = Client(socket_dir=tempdir)
            response = mock_client.call('mock_server', 'mock_method')
            if response['success']:
                rpc_event.set()

        multiprocessing.Process(target=run_server, daemon=True).start()
        multiprocessing.Process(target=run_client, daemon=True).start()

        assert rpc_event.wait(1)


def test_client_timeout():
    """
    Test whether client raises RPCTimeoutError on `call()` timeout.
    """

    client_event = multiprocessing.Event()

    def run_client():
        with tempfile.TemporaryDirectory() as tempdir:
            client = Client(socket_dir=tempdir)
            try:
                client.call('non_existant', 'non_existant', timeout=0.1)
            except RPCTimeoutError:
                client_event.set()

    multiprocessing.Process(target=run_client, daemon=True).start()

    assert client_event.wait(1)
