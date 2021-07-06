"""
Connector does the dirty job of connecting to the ZeroMQ UNIX sockets, polling,
reconnecting and resending messages after reconnect.

User API:

  Connector(context, socket_dir):
    |- get_socket(server)
    |- reconnect(server)
    |- send()
"""

import logging
import os

import zmq

from zrpc.exceptions import ConnectError


logger = logging.getLogger(__name__)


class Connector:
    def __init__(self, context: zmq.Context,
                       poller: zmq.Poller,
                       socket_dir: str):
        self.context = context
        self.socket_dir = socket_dir
        self.poller = poller

        self.sockets = {}
        self.last_request = {}

        try:
            os.makedirs(socket_dir, exist_ok=True)
        except (OSError, zmq.ZMQError) as exc:
            raise ConnectError('Cannot connect client') from exc

    def __del__(self):
        try:
            for socket in self.sockets.values():
                socket.close(linger=0)
        except AttributeError:
            pass

    def get_socket(self, server: str) -> zmq.Socket:
        """ Returns socket. If not connected, connects first. """
        try:
            return self.sockets[server]
        except KeyError:
            self.reconnect(server)
            return self.sockets[server]

    def reconnect(self, server: str):
        """ Connects to the socket. If connected, disconnects first. """
        if server in self.sockets:
            self.poller.unregister(self.sockets[server])
            self.sockets.pop(server).close(linger=0)

            logger.debug('Disconnected from "%s"', server)

        socket = self.context.socket(zmq.DEALER)
        socket.connect('ipc://' + os.path.join(self.socket_dir, server))

        self.poller.register(socket, zmq.POLLIN)
        self.sockets[server] = socket

        logger.debug('Connected to "%s"', server)

    async def send(self, socket: zmq.Socket, request: bytes):
        await socket.send_multipart([b'', request])
