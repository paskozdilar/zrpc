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


logger = logging.getLogger(__name__)


class Client:
    def __init__(self, socket_dir=None, retry_timeout=None):
        socket_dir = os.path.abspath(socket_dir or '/tmp/zrpc_sockets')

        context = zmq.asyncio.Context.instance()
        sockets = {}

        self._context = context
        self._poller = zmq.asyncio.Poller()
        self._sockets = sockets

        self._socket_dir = socket_dir
        self._retry_timeout = retry_timeout or 1

        self.__response_lock = asyncio.Lock()
        self.__response_queues = collections.defaultdict(asyncio.Queue)

        self.__socket_handler = {}
        self.__socket_call_count = collections.defaultdict(int)

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

        socket = context.socket(zmq.DEALER)
        socket.connect('ipc://' + socket_path)
        sockets[socket_name] = socket

        logger.debug('Connected to "%s"', socket_name)

    def __disconnect(self, socket_name):
        sockets = self._sockets

        if socket_name not in sockets:
            raise RPCError('Service "%s" is not connected' % socket_name)

        socket = sockets.pop(socket_name)
        socket.close(linger=0)

        logger.debug('Disconnected from "%s"', socket_name)

    async def call(self, server, method, args=(), kwargs={}, timeout=None):
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

        elapsed_time = 0

        retry_timeout = self._retry_timeout

        queue = asyncio.Queue(maxsize=1)
        self.__response_queues[request_id] = queue

        self.__socket_call_count[server] += 1
        if self.__socket_call_count[server] == 1:
            socket_handler = asyncio.get_running_loop().create_task(
                self.__handle_response(timeout, socket)
            )
            self.__socket_handler[server] = socket_handler
        else:
            socket_handler = self.__socket_handler[server]

        while elapsed_time <= timeout:
            socket.send_multipart([b'', request])

            timeout_ms = 1000 * max(0, min(retry_timeout, timeout - elapsed_time))

            logger.debug('Polling sockets with %s ms timeout', timeout_ms)
            try:
                response_data = await asyncio.wait_for(queue.get(), timeout=timeout_ms/1000)

            except asyncio.TimeoutError:
                logger.error('No response from "%s" - reconnecting...' % server)
                self.__disconnect(server)
                self.__connect(server)
                socket = sockets[server]
                socket_handler.cancel()
                socket_handler = asyncio.get_running_loop().create_task(
                    self.__handle_response(timeout, socket)
                )
                self.__socket_handler[server] = socket_handler

            else:
                response_id, payload, is_exception = response_data

                if request_id != response_id:
                    self.__response_queues.pop(request_id, None)
                    raise RPCError('Internal error (request ID does not match)')

                if is_exception:
                    self.__response_queues.pop(request_id, None)
                    raise RPCError(payload)
                
                return payload

            elapsed_time = time.monotonic() - start_time

        self.__socket_call_count[server] -= 1
        if self.__socket_call_count[server] == 0:
            socket_handler.cancel()
            self.__socket_handler.pop(server)

        self.__response_queues.pop(request_id, None)
        raise RPCTimeoutError('Service "%s" not responding' % server)

    async def __handle_response(self, timeout, socket):
        try:
            while True:
                null, response_data = await socket.recv_multipart()
                response_id, payload, is_exception = deserialize(response_data)

                if response_id in self.__response_queues:
                    self.__response_queues[response_id].put_nowait(
                        (response_id, payload, is_exception)
                    )
        except asyncio.CancelledError:
            return

    def list(self):
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