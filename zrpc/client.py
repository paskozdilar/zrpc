"""
Synchronous ZRPC client.

Instantiate this class and use the `.call` method to call an RPC method.
"""

import logging
import os
import stat
import time
import uuid
import zmq
from zrpc.utils.exceptions import (
        ConnectError,
        RPCError,
        RPCTimeoutError,
)
from zrpc.utils.serialization import serialize, deserialize


logger = logging.getLogger(__name__)


class Client:
    def __init__(self, socket_dir='/tmp/zrpc_sockets/'):
        socket_dir = os.path.abspath(socket_dir)

        context = zmq.Context.instance()
        sockets = {}

        self._context = context
        self._poller = zmq.Poller()
        self._sockets = sockets
        self._socket_dir = socket_dir

        try:
            os.makedirs(socket_dir, exist_ok=True)
            for socket_name in os.listdir(socket_dir):
                self.__connect(socket_name)
        except (OSError, zmq.ZMQError) as exc:
            raise ConnectError('Cannot connect client') from exc

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

        self._poller.register(socket)
        logger.debug('Connected to "%s"' % socket_name)

    def __disconnect(self, socket_name):
        sockets = self._sockets

        if socket_name not in sockets:
            raise RPCError('Service "%s" is not connected' % socket_name)

        socket = sockets.pop(socket_name)
        socket.close(linger=0)

        self._poller.unregister(socket)
        logger.debug('Disconnected from "%s"' % socket_name)

    def call(self, service, method, payload=None, timeout=None):
        """
        Call an RPC method of a service with a payload.
        If timeout is None, blocks indefinitely.
        If timeout is a number, blocks `timeout` seconds and raises
        RPCTimeoutError on timeout.
        """
        if timeout is None or timeout < 0:
            timeout = float('inf')

        sockets = self._sockets
        if service not in sockets:
            self.__connect(service)
        socket = sockets[service]

        request_id = str(uuid.uuid4())
        request = serialize([request_id, method, payload])

        start_time = time.monotonic()
        events = {}

        current_time = time.monotonic()

        iter_timeout = 1
        while (current_time - start_time) < timeout:
            socket.send(request)
            timeout_ms = 1000 * max(0, min(iter_timeout, timeout - (current_time - start_time)))
            events = dict(self._poller.poll(timeout=timeout_ms))

            if socket in events:
                break

            logger.error('Service "%s" down - reconnecting...' % service)
            self.__disconnect(service)
            self.__connect(service)
            socket = sockets[service]
            iter_timeout += 1

        if socket not in events:
            raise RPCTimeoutError('Service "%s" not responding' % service)

        response_data = socket.recv()
        response = deserialize(response_data)
        [response_id, payload, is_exception] = response

        if request_id != response_id:
            raise RPCError('Internal error (request ID does not match)')

        if is_exception:
            raise RPCError(payload)

        return payload
