"""
Synchronous ZRPC client.

Instantiate this class and use the `.call` method to call an RPC method.

User API:

  Client:
    |- call(server, method, args=(), kwargs={}, timeout=None)
    |- list(server=None, timeout=None)
    |- get_proxy(timeout=None)

  Proxy:

    >> Proxy().example_server.example_method[3](*args, **kwargs)
    is equivalent to:
    >> Client().call("example_server", "example_method", args, kwargs, timeout=3)

    The "[3]" can be ommited, in which case, timeout is None.
"""

import logging
import os
import time
import uuid
import zmq
from zrpc.exceptions import (
        ConnectError,
        RPCError,
        RPCTimeoutError,
)
from zrpc.serialization import serialize, deserialize


logger = logging.getLogger(__name__)


class Client:
    def __init__(self, socket_dir=None, retry_timeout=None):
        socket_dir = os.path.abspath(socket_dir or '/tmp/zrpc_sockets')

        context = zmq.Context.instance()
        sockets = {}

        self._context = context
        self._poller = zmq.Poller()
        self._sockets = sockets

        self._socket_dir = socket_dir
        self._retry_timeout = retry_timeout or 3

        try:
            os.makedirs(socket_dir, exist_ok=True)
            for socket_name in os.listdir(socket_dir):
                self.__connect(socket_name)
        except (OSError, zmq.ZMQError) as exc:
            raise ConnectError('Cannot connect client') from exc

    def __del__(self):
        try:
            for socket_name in list(self._sockets.keys()):
                self.__disconnect(socket_name)
        except AttributeError:
            pass

    def __connect(self, socket_name):
        context = self._context
        sockets = self._sockets
        socket_dir = self._socket_dir

        if socket_name in sockets:
            raise RPCError('Service "%s" already connected' % socket_name)

        socket_path = os.path.join(socket_dir, socket_name)

        socket = context.socket(zmq.REQ)
        socket.connect('ipc://' + socket_path)
        sockets[socket_name] = socket

        self._poller.register(socket, zmq.POLLIN)
        logger.debug('Connected to "%s"', socket_name)

    def __disconnect(self, socket_name):
        sockets = self._sockets

        if socket_name not in sockets:
            raise RPCError('Service "%s" is not connected' % socket_name)

        socket = sockets.pop(socket_name)
        socket.close(linger=0)

        self._poller.unregister(socket)
        logger.debug('Disconnected from "%s"', socket_name)

    def call(self, server, method, args=(), kwargs={}, timeout=None):
        """
        Call an RPC method of a server with args and kwargs.
        If `timeout` is None, blocks indefinitely.
        If `timeout` is a number, blocks `timeout` seconds and raises
        RPCTimeoutError on timeout.
        """
        if timeout is None or timeout < 0:
            timeout = float('inf')

        sockets = self._sockets
        if server not in sockets:
            self.__connect(server)
        socket = sockets[server]

        request_id = str(uuid.uuid4())
        request = serialize([request_id, method, args, kwargs])

        start_time = time.monotonic()
        events = {}

        current_time = start_time
        elapsed_time = current_time - start_time

        retry_timeout = self._retry_timeout
        while elapsed_time <= timeout:
            socket.send(request)
            timeout_ms = 1000 * max(0, min(retry_timeout, timeout - elapsed_time))
            events = dict(self._poller.poll(timeout=timeout_ms))

            if socket in events:
                break

            logger.debug('No response from "%s" - reconnecting...' % server)
            self.__disconnect(server)
            self.__connect(server)
            socket = sockets[server]

            current_time = time.monotonic()
            elapsed_time = current_time - start_time

        if socket not in events:
            raise RPCTimeoutError('Service "%s" not responding' % server)

        response_data = socket.recv()
        response = deserialize(response_data)
        [response_id, payload, is_exception] = response

        if request_id != response_id:
            raise RPCError('Internal error (request ID does not match)')

        if is_exception:
            raise RPCError(payload)

        return payload

    def list(self, server = None, timeout = None):
        if server is not None:
            return self.call(server, method=None, timeout=timeout)
        return os.listdir(self._socket_dir)

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
