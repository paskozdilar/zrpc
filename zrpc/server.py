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
import inspect
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
            self.__logger = logging.getLogger(__name__ + '.' + name)
            self.__logger.warning('Service name not set -- using "%s".' % name)
        else:
            self.__logger = logging.getLogger(__name__ + '.' + name)

        socket_dir = os.path.abspath(socket_dir or '/tmp/zrpc_sockets')

        self.__name = name
        self.__context = None
        self.__socket_dir = socket_dir
        self.__socket_path = None
        self.__socket = None
        self.__poller = None

        self.__cache = None
        self.__fd_callbacks = {}

        self.__started = False

    def __del__(self):
        if self.__started:
            Server.stop(self)

    def __enter__(self):
        Server.start(self)
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        try:
            Server.stop(self)
        except RuntimeError:
            # Do not raise if server has stopped itself
            pass

    def start(self):
        if self.__started:
            raise RuntimeError('Server already started')

        name = self.__name
        socket_dir = self.__socket_dir

        self.__logger.info('Starting RPC server "%s"...', name)

        context = zmq.Context()
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

        self.__logger.debug('Waiting for bind to complete...')
        while not os.path.exists(socket_path):
            time.sleep(0.5)

        try:
            os.chmod(socket_dir, 0o777)
        except OSError:
            pass

        try:
            os.chmod(socket_path, 0o777)
        except OSError:
            pass

        self.__logger.debug('Success. %s', os.listdir(socket_dir))

        poller.register(socket)
        fd_callbacks = self.__fd_callbacks
        for fd in fd_callbacks:
            poller.register(fd, zmq.POLLIN)

        if self._rpc_methods is None:
            self._rpc_methods = {}

        self.__logger.info('RPC methods: %s', list(self._rpc_methods.keys()))

        # Add special case for list(server)
        self._rpc_methods[None] = self.__class__.__list

        self.__context = context
        self.__socket_path = socket_path
        self.__socket = socket
        self.__poller = poller

        self.__cache = _RPCCache(maxsize=10)
        self.__started = True

    def stop(self):
        try:
            if not self.__started:
                raise RuntimeError('Server not started')
            self.__socket.close(linger=0)
            self.__context.destroy(linger=0)
            os.unlink(self.__socket_path)
        except (OSError, AttributeError):
            pass
        else:
            self.__context = None
            self.__socket_path = None
            self.__socket = None
            self.__poller = None
            self.__cache = _RPCCache(maxsize=10)
        finally:
            self.__started = False

    def register(self, fd, callback):
        """
        Register file-like object `fd` with `callback` callable.

        The `callback` callable will be called when data is READY TO BE READ
        from `fd` file descriptor.

        ZRPC SERVER WILL *NOT* READ ANY DATA FROM THE FILE DESCRIPTOR.
        That is the responsibility of the `callback` callable.
        """
        if not isinstance(fd, zmq.Socket) and hasattr(fd, 'fileno'):
            fd = fd.fileno()
        self.__fd_callbacks[fd] = callback
        if self.__started:
            self.__poller.register(fd, zmq.POLLIN)

    def unregister(self, fd):
        """ Unregister file-like object `fd`. """
        if hasattr(fd, 'fileno'):
            fd = fd.fileno()
        self.__fd_callbacks.pop(fd, None)
        if self.__started:
            self.__poller.unregister(fd)

    def run(self):
        """ Run service forever. """
        self.__logger.info('Running "%s" forever...', self.__name)
        if self.__started:
            while self.__started:
                Server.run_once(self)
        else:
            with self:
                while self.__started:
                    Server.run_once(self)

    def run_once(self, timeout=None):
        """ Run service once (process single event or wait for timeout) """
        if not self.__started:
            raise RuntimeError('Server not started')

        if timeout is not None:
            timeout = int(1000 * timeout)

        socket = self.__socket
        poller = self.__poller

        ready_sockets = dict(poller.poll(timeout=timeout))

        for ready_socket in ready_sockets:
            if ready_socket is socket:
                Server.__handle_request(self, ready_socket)
            else:
                try:
                    self.__fd_callbacks[ready_socket]()
                except KeyError:
                    # Ignore socket that removes itself
                    pass

    def __handle_request(self, socket):
        request_data = socket.recv()

        try:
            request = deserialize(request_data)
            [request_id, method_name, args, kwargs] = request
        except (SerializationError, ValueError) as exc:
            self.__logger.error('Received malformed RPC request!')
            # send empty message to keep REP state machine happy
            socket.send(b'')
            return

        if request_id in self.__cache:
            # resend response silently
            self.__logger.debug('Returning request from cache: %s', request_id)
            response_data = self.__cache[request_id]
            socket.send(self.__cache[request_id])
            return

        try:
            method = self._rpc_methods[method_name]
        except KeyError:
            payload = 'Invalid RPC method name: %s' % method_name
            is_exception = True
            self.__logger.error(payload)
        else:
            self.__logger.debug('Executing "%s" with args "%s" and kwargs "%s"...',
                                method_name, str(args)[:50], str(kwargs)[:50])
            try:
                payload = method(self, *args, **kwargs)
            except Exception as exc:
                self.__logger.error('--- RPC METHOD EXCEPTION ---',
                                   exc_info=True)
                payload = '%s: %s' % (type(exc).__name__, exc)
                is_exception = True
            else:
                is_exception = False

        response = [request_id, payload, is_exception]
        response_data = serialize(response)
        self.__cache[request_id] = response_data
        socket.send(response_data)

    def __list(self):
        method_list = {}
        for funcname in filter(bool, self._rpc_methods):
            if funcname is not None:
                method = getattr(self, funcname)
                params = inspect.signature(method).parameters
                method_list[funcname] = {
                    'docstring': method.__doc__ or '',
                    'args_required': [
                        arg
                        for arg, param in params.items()
                        if param.default is inspect.Parameter.empty
                    ],
                    'args_optional': {
                        arg: param.default
                        for arg, param in params.items()
                        if param.default is not inspect.Parameter.empty
                    }
                }
        return method_list



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
