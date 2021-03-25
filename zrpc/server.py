"""
Synchronous ZRPC server.

Subclass the `Server` class and use the `rpc_method` decorator to make the
method available to ZRPC clients.
"""

import logging
import os
import re
import zmq
from zrpc.exceptions import ConnectError
from zrpc.serialization import serialize, deserialize


logger = logging.getLogger(__name__)


class Server:
    _rpc_methods: dict = None

    def __init__(self, name=None, socket_dir='/tmp/zrpc_sockets'):
        if name is None:
            # Convert CamelCase class name into snake_case
            class_name = self.__class__.__name__
            name = re.sub('([A-Z]+)', r'_\1', class_name).strip('_').lower()
            logger.warning('Service name not set -- using "%s".' % name)
        socket_dir = os.path.abspath(socket_dir)

        context = zmq.Context.instance()
        socket = context.socket(zmq.REP)
        socket_path = os.path.join(socket_dir, name)

        try:
            os.makedirs(socket_dir, exist_ok=True)
            socket.bind('ipc://' + socket_path)
        except (OSError, zmq.ZMQError) as exc:
            raise ConnectError('Server init error') from exc

        for method in self._rpc_methods.keys():
            logger.info('Registered RPC method: "%s"' % method)

        self._context = context
        self._socket = socket

    def run(self):
        socket = self._socket
        logger.info('Entering main loop...')

        while True:
            request_data = socket.recv()
            logger.debug('NEW RPC REQUEST')

            try:
                request = deserialize(request_data)
                [request_id, method_name, payload] = request
                logger.debug('Decoded request: [%s, %s, %s]'
                             % (request_id, method_name, str(payload)[:50]))
            except (SerializationError, ValueError) as exc:
                logger.error('Received malformed RPC request!')

            try:
                method = self._rpc_methods.get(method_name)
                logger.debug('Executing "%s" with payload "%s"...'
                             % (method_name, str(payload)[:50]))
                payload = method(self, payload)
                is_exception = False
            except Exception as exc:
                logger.error('--- RPC METHOD EXCEPTION ---', exc_info=True)
                payload = '%s: %s' % (type(exc).__name__, exc)
                is_exception = True

            logger.debug('Serializing RPC response "%s"...' % str(payload)[:50])
            response = [request_id, payload, is_exception]
            response_data = serialize(response)
            logger.debug('Sending RPC response "%s"...' % str(response_data)[:50])
            socket.send(response_data)


class __RPCDecoratorClass:
    """
    When we decorate a method with an ordinary function decorator, the
    decorator receives the method (function object) as an argument.
    However, the owner class cannot be determined from the function object
    alone.

    Somehow, __set_name__ method does exactly what we need:

        https://stackoverflow.com/a/54316392/8713894

    TODO:
        Spend more time understanding the __set_name__ method and write better
        documentation explaining this mechanism.
    """

    def __init__(self, func):
        self.func = func

    def __set_name__(self, owner, name):
        if owner._rpc_methods is None:
            owner._rpc_methods = {}
        owner._rpc_methods[name] = self.func
        setattr(owner, name, self.func)


rpc_method = __RPCDecoratorClass
