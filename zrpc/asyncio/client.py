"""
Asynchronous ZRPC client.

Instantiate this class and use the `.call` method to call an RPC method.

User API:

  Client:
    |- call(server, method, args=(), kwargs={}, timeout=None)
    |- list()
    |- get_proxy(timeout=None)

  Proxy:

    >> Proxy().example_server.example_method[3](*args, **kwargs)
    is equivalent to:
    >> Client().call("example_server", "example_method", args, kwargs, timeout=3)

    The "[3]" can be ommited, in which case, timeout is None.
"""

import asyncio
import collections
import logging
import os
import time
import uuid
import zmq
import zmq.asyncio
from zrpc.exceptions import (
        ConnectError,
        RPCError,
        RPCTimeoutError,
)
from zrpc.serialization import serialize, deserialize
from zrpc.util.connector import Connector
from zrpc.util.asyncio.multipoller import Multipoller


logger = logging.getLogger(__name__)


class Client:
    def __init__(self, socket_dir=None, retry_timeout=None):
        self.socket_dir = os.path.abspath(socket_dir or '/tmp/zrpc_sockets')
        self.retry_timeout = retry_timeout or 1

        self.context = zmq.asyncio.Context()
        self.poller = zmq.asyncio.Poller()

        self.connector = Connector(context=self.context,
                                   socket_dir=self.socket_dir,
                                   poller=self.poller)
        self.multipoller = Multipoller(self.poller)

    def __del__(self):
        self.context.destroy(linger=0)

    async def call(self, server, method, args=(), kwargs={}, timeout=None):
        """
        Call an RPC method of a server with args and kwargs.
        If `timeout` is None, blocks indefinitely.
        If `timeout` is a number, blocks `timeout` seconds and raises
        RPCTimeoutError on timeout.
        """
        if timeout is None or timeout < 0:
            timeout = float('inf')

        # Create message
        request_id = str(uuid.uuid4())
        request = serialize([request_id, method, args, kwargs])

        start_time = time.monotonic()
        elapsed_time = 0

        fail_count = 0

        while elapsed_time <= timeout:
            # Send request
            socket = self.connector.get_socket(server)
            await self.connector.send(socket, request)

            timeout_try = max(0,
                              min(self.retry_timeout,
                                  timeout - elapsed_time))
            try:
                # Wait response
                response_data = await asyncio.wait_for(
                    self.multipoller.poll_and_recv(request_id),
                    timeout=timeout_try,
                )
            except asyncio.TimeoutError:
                pass
            else:
                # Parse response
                response_id, payload, is_exception = deserialize(response_data)

                if request_id != response_id:
                    raise RPCError('Internal error (request ID does not match)')

                if is_exception:
                    raise RPCError(payload)

                return payload

            elapsed_time = time.monotonic() - start_time

        raise RPCTimeoutError('Service "%s" not responding' % server)

    def list(self, server=None, timeout=None):
        if server is not None:
            return self.call(server=server, method=None, timeout=timeout)
        return os.listdir(self.socket_dir)

    def get_proxy(self):
        return Proxy(self)


# client = Client()
# proxy = client.get_proxy():
class Proxy:
    def __init__(self, client=None):
        self.client = client or Client()
        self._cached_subproxies = {}

    def __getattr__(self, server):
        if server in self._cached_subproxies:
            return self._cached_subproxies[server]
        subproxy = _ServerProxy(server, self.client)
        self._cached_subproxies[server] = subproxy
        return subproxy

# proxy.server_name:
class _ServerProxy:
    def __init__(self, server, client):
        self.server = server
        self.client = client
        self._cached_subproxies = {}

    def __getattr__(self, method):
        if method in self._cached_subproxies:
            return self._cached_subproxies[method]
        subproxy = _MethodProxy(self.server, method, self.client)
        self._cached_subproxies[method] = subproxy
        return subproxy

# proxy.server_name.method_name:
class _MethodProxy:
    def __init__(self, server, method, client):
        self.server = server
        self.method = method
        self.client = client
        self._cached_subproxies = {}

    def __call__(self, *args, **kwargs):
        # proxy.server_name.method_name(*args, **kwargs):
        return self.client.call(server=self.server,
                                method=self.method,
                                args=args,
                                kwargs=kwargs)

    def __getitem__(self, timeout):
        if timeout in self._cached_subproxies:
            return self._cached_subproxies[timeout]
        subproxy = _MethodWithTimeoutProxy(self.server,
                                           self.method,
                                           self.client,
                                           timeout)
        self._cached_subproxies[timeout] = subproxy
        return subproxy

# proxy.service_name.method_name[timeout]:
class _MethodWithTimeoutProxy:
    def __init__(self, server, method, client, timeout):
        self.server = server
        self.method = method
        self.client = client
        self.timeout = timeout

    def __call__(self, *args, **kwargs):
        # proxy.service_name.method_name[timeout](*args, **kwargs):
        return self.client.call(server=self.server,
                                method=self.method,
                                args=args,
                                kwargs=kwargs,
                                timeout=self.timeout)
