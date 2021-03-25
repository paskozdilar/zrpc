#!/usr/bin/env python3

import multiprocessing
import sys
import tempfile
import time
from zrpc.server import Server, rpc_method
from zrpc.client import Client


def test_basic_rpc():
    """
    Run a basic mock tests inside temporary directory.
    """
    rpc_event = multiprocessing.Event()

    with tempfile.TemporaryDirectory() as tempdir:

        def run_server():
            print('server tempdir', tempdir, file=sys.stderr, flush=True)
            class MockServer(Server):
                @rpc_method
                def mock_method(self, payload):
                    return {'success': True}
            MockServer(socket_dir=tempdir).run()

        def run_client():
            print('client tempdir', tempdir, file=sys.stderr, flush=True)
            mock_client = Client(socket_dir=tempdir)
            response = mock_client.call('mock_server', 'mock_method')
            if response['success']:
                rpc_event.set()

        multiprocessing.Process(target=run_server, daemon=True).start()
        multiprocessing.Process(target=run_client, daemon=True).start()

        assert rpc_event.wait(timeout=1)


def test_reliability_client():
    """
    Test client reliability on server crash.
    """
    connect_event = multiprocessing.Event()
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
            connect_event.set()
            MockServer(socket_dir=tempdir).run()

        def run_client():
            client = Client(socket_dir=tempdir)
            response = client.call('mock_server', 'mock_method')
            if response['success']:
                rpc_event.set()
        
        multiprocessing.Process(target=run_server, daemon=True).start()
        assert connect_event.wait(1)
        multiprocessing.Process(target=run_client, daemon=True).start()
        assert crash_event.wait(1)
        multiprocessing.Process(target=run_server, daemon=True).start()

        # Wait 5 seconds for retry
        assert rpc_event.wait(5)
