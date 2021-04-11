#!/usr/bin/env python3

import multiprocessing
import sys
import tempfile
import time
from zrpc.server import Server, rpc_method
from zrpc.client import Client


def test_basic_rpc():
    """
    Run a basic mock tests.
    """
    rpc_event = multiprocessing.Event()

    with tempfile.TemporaryDirectory() as tempdir:

        def run_server():
            class MockServer(Server):
                @rpc_method
                def mock_method(self, payload):
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
                def mock_method(self, payload):
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
                def mock_method(self, payload):
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
