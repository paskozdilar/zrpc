"""
Synchronous ZRPC server.

Subclass the `Server` class and use the `rpc_method` decorator to make the
method available to ZRPC clients.

User API:

  Server:
    |- start()
    |- stop()
    |
    |- register(fd, callback)
    |- unregister(fd)
    |
    |- run()
    |- run_once(timeout=None)

  rpc_method:  <decorator for Server methods>
"""


import collections
import logging
import os
import re
import time
import zmq
from zrpc.exceptions import ConnectError
from zrpc.serialization import serialize, deserialize, SerializationError


class _RPCCache(collections.OrderedDict):

    def __init__(self, maxsize=128, *args, **kwds):
        self.maxsize = maxsize
        super().__init__(*args, **kwds)

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            oldest = next(iter(self))
            del self[oldest]


class Server:
    _rpc_methods: dict = None

    def __init__(self, name=None, socket_dir=None):
        if name is None:
            # Convert CamelCase class name into snake_case
            class_name = self.__class__.__name__
            name = re.sub('([A-Z]+)', r'_\1', class_name).strip('_').lower()
            self._logger = logging.getLogger(__name__ + '.' + name)
            self._logger.warning('Service name not set -- using "%s".' % name)
        else:
            self._logger = logging.getLogger(__name__ + '.' + name)
            self._logger.info('Starting RPC server "{}"...'.format(name))

        socket_dir = os.path.abspath(socket_dir or '/tmp/zrpc_sockets')

        self._name = name
        self._context = None
        self._socket_dir = socket_dir
        self._socket_path = None
        self._socket = None
        self._poller = None

        self._cache = None
        self._fd_callbacks = {}

        self._started = False

    def __del__(self):
        self.stop()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.stop()

    def start(self):
        if self._started:
            return

        name = self._name
        socket_dir = self._socket_dir

        context = zmq.Context.instance()
        socket = context.socket(zmq.REP)
        poller = zmq.Poller()

        socket_path = os.path.join(socket_dir, name)
        if os.path.exists(socket_path):
            os.remove(socket_path)

        try:
            os.makedirs(socket_dir, exist_ok=True)
            socket.bind('ipc://' + socket_path)
        except (OSError, zmq.ZMQError) as exc:
            raise ConnectError('Server init error') from exc

        self._logger.info('Waiting for bind to complete...')
        while not os.path.exists(socket_path):
            time.sleep(0.5)
        os.chmod(socket_path, 0o777)
        self._logger.info('Success.' + str(os.listdir(socket_dir)))

        poller.register(socket)
        fd_callbacks = self._fd_callbacks
        for fd in fd_callbacks:
            poller.register(fd, zmq.POLLIN)

        if self._rpc_methods is None:
            self._rpc_methods = {}

        self._logger.info('RPC methods: %s' % list(self._rpc_methods.keys()))

        self._context = context
        self._socket_path = socket_path
        self._socket = socket
        self._poller = poller

        self._cache = _RPCCache(maxsize=10)
        self._started = True

    def stop(self):
        try:
            if not self._started:
                return
            self._socket.close(linger=0)
            os.unlink(self._socket_path)
        except (OSError, AttributeError):
            pass
        else:
            self._context = None
            self._socket_path = None
            self._socket = None
            self._poller = None
            self._cache = _RPCCache(maxsize=10)
        finally:
            self._started = False

    def register(self, fd, callback):
        """
        Register file-like object `fd` with `callback` callable.

        The `callback` callable will be called when data is READY TO BE READ
        from `fd` file descriptor.

        ZRPC SERVER WILL *NOT* READ ANY DATA FROM THE FILE DESCRIPTOR.
        That is the responsibility of the `callback` callable.
        """
        if hasattr(fd, 'fileno'):
            fd = fd.fileno()
        self._fd_callbacks[fd] = callback
        if self._started:
            self._poller.register(fd, zmq.POLLIN)

    def unregister(self, fd):
        """ Unregister file-like object `fd`. """
        if hasattr(fd, 'fileno'):
            fd = fd.fileno()
        self._fd_callbacks.pop(fd)
        if self._started:
            self._poller.unregister(fd)

    def run(self):
        """ Run service forever. """
        self._logger.info('Running "{}" forever...'.format(self._name))
        if self._started:
            while True:
                self.run_once()
        else:
            with self:
                while True:
                    self.run_once()

    def run_once(self, timeout=None):
        """ Run service once (process single event or wait for timeout) """
        if not self._started:
            raise RuntimeError('Server not started')

        if timeout is not None:
            timeout = int(1000 * timeout)

        socket = self._socket
        poller = self._poller

        self._logger.info('Polling for requests...')
        ready_sockets = dict(poller.poll(timeout=timeout))
        self._logger.info('Ready_sockets: {}'.format(ready_sockets))

        for ready_socket in ready_sockets:
            if ready_socket is socket:
                self.__handle_request(ready_socket)
            else:
                self._fd_callbacks[ready_socket]()

    def __handle_request(self, socket):
        request_data = socket.recv()

        try:
            request = deserialize(request_data)
            [request_id, method_name, args, kwargs] = request
        except (SerializationError, ValueError) as exc:
            self._logger.error('Received malformed RPC request!')
            # send empty message to keep REP state machine happy
            socket.send(b'')
            return

        if request_id in self._cache:
            # resend response silently
            response_data = self._cache[request_id]
            socket.send(self._cache[request_id])
            return

        try:
            method = self._rpc_methods[method_name]
        except KeyError:
            payload = 'Invalid RPC method name: %s' % method_name
            is_exception = True
            self._logger.error(payload)
        else:
            self._logger.debug('Executing "%s" with args "%s" and kwargs "%s"...'
                         % (method_name, str(args)[:50], str(kwargs)[:50]))
            try:
                payload = method(self, *args, **kwargs)
            except Exception as exc:
                self._logger.error('--- RPC METHOD EXCEPTION ---',
                                   exc_info=True)
                payload = '%s: %s' % (type(exc).__name__, exc)
                is_exception = True
            else:
                is_exception = False

        self._logger.debug('Serializing RPC response "%s"...'
                           % str(payload)[:50])
        response = [request_id, payload, is_exception]
        response_data = serialize(response)
        self._logger.debug('Sending RPC response "%s"...'
                           % str(response_data)[:50])
        self._cache[request_id] = response_data
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
