"""
Asynchronous ZRPC server.

Subclass the `Server` class and use the `rpc_method` decorator to make the
async method available to ZRPC clients.

NOTE: `register`-ed methods must be async methods.

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


import asyncio
import collections
import inspect
import logging
import os
import re
import time
import zmq
import zmq.asyncio
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

        self.__request_lock = None
        self.__cache_lock = None

    def __del__(self):
        try:
            if self.__started:
                Server.stop(self)
        except AttributeError:
            pass

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

        context = zmq.asyncio.Context()
        socket = context.socket(zmq.ROUTER)
        poller = zmq.asyncio.Poller()

        socket_path = os.path.join(socket_dir, name)
        if os.path.exists(socket_path):
            os.remove(socket_path)

        try:
            os.makedirs(socket_dir, exist_ok=True)
            socket.bind('ipc://' + socket_path)
        except (OSError, zmq.ZMQError) as exc:
            raise ConnectError('Server init error') from exc

        while not os.path.exists(socket_path):
            self.__logger.info('Waiting for bind to complete...')
            time.sleep(0.025)

        try:
            os.chmod(socket_dir, 0o777)
        except OSError:
            pass

        try:
            os.chmod(socket_path, 0o777)
        except OSError:
            pass

        self.__logger.debug('Success. %s', os.listdir(socket_dir))

        poller.register(socket, zmq.POLLIN)
        for fd in self.__fd_callbacks:
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

        self.__request_lock = asyncio.Lock()
        self.__cache_lock = asyncio.Lock()

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

    async def run(self):
        """ Run service forever.  """
        self.__logger.info('Running "%s" forever...', self.__name)

        needs_stop = not self.__started
        if not self.__started:
            self.start()

        socket = self.__socket
        poller = self.__poller

        try:
            while True:
                for ready_socket, _ in await poller.poll():
                    if ready_socket is socket:
                        data = await socket.recv_multipart()
                        asyncio.create_task(
                            self.__handle_request(socket, data)
                        )
                    else:
                        asyncio.create_task(
                            self.__fd_callbacks[ready_socket]()
                        )
        finally:
            if needs_stop:
                self.stop()

    async def run_once(self):
        """ Handle a single round of requests. """
        tasks = []
        poller = self.__poller
        for socket, _ in await poller.poll():
            data = await socket.recv_multipart()
            task = asyncio.create_task(self.__handle_request(socket, data))
            tasks.append(task)
        for task in tasks:
            await task

    async def __handle_request(self, socket: zmq.Socket, data: bytes):
        token, null, request_data = data

        try:
            request = deserialize(request_data)
            [request_id, method_name, args, kwargs] = request

        except (SerializationError, ValueError) as exc:
            self.__logger.error('Received malformed RPC request!')
            response_data = str(exc)
            is_exception = True

        else:
            if request_id in self.__cache:
                self.__logger.debug('Returning request from cache')
                token, null, response_data = await self.__cache[request_id]

            else:
                self.__cache[request_id] = asyncio.Future()

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
                        payload = await method(self, *args, **kwargs)
                    except Exception as exc:
                        self.__logger.error('--- RPC METHOD EXCEPTION ---',
                                           exc_info=True)
                        payload = '%s: %s' % (type(exc).__name__, exc)
                        is_exception = True
                    else:
                        is_exception = False

                self.__logger.debug('Serializing RPC response "%s"...',
                                    str(payload)[:50])
                response = [request_id, payload, is_exception]
                response_data = serialize(response)
                self.__logger.debug('Sending RPC response "%s"...',
                                    str(response_data)[:50])

                if request_id in self.__cache:
                    self.__cache[request_id].set_result([token, null, response_data])
        await socket.send_multipart([token, null, response_data])

    async def __list(self):
        method_list = {}
        for funcname in filter(bool, self._rpc_methods):
            if funcname is not None:
                method = getattr(self, funcname)
                params = inspect.signature(method).parameters
                method_list[funcname] = {
                    'docstring': method.__doc__,
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
