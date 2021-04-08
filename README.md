# zrpc

Fast and reliable single-machine RPC library.


## Usage:

See `example/` directory for runnable example.


### Example server:

    from zrpc.server import Server, rpc_method

    class TestServer(Server):
        @rpc_method
        def func(self, payload):
            print('RPC request [%s]: %s' % (self.counter, payload))
            return 'func', payload

    TestServer(name='test_server').run()


### Example client:

    from zrpc.client import Client

    client = Client()
    response = client.call(service='test_server',
                           method='func',
                           payload={'haha': 'brt'})
    print('RPC response [%s]: %s' % (timestamp, response))


## API:

### Server:

    class Server:
        __init__(name=None, socket_dir='/tmp/zrpc_sockets')
            Create ZRPC server.

            name:
                ZRPC server name, specified in client `call()` method.
                If None, then server name is derived from class name,
                converted from CamelCase to snake_case (i.e. if class name
                is `MyClass`, then service name will be `my_class` and clients
                shall call it with `call(service='my_class', ...)` ).

                default: None

            socket_dir:
                Directory where ZRPC UNIX sockets will be placed.
                Server must use the same `socket_dir` as Client in order to
                be reachable.

                default: '/tmp/zrpc_sockets'

        register(fd, callback)
            Register file-like object `fd` with `callback` callable.

            The `callback` callable will be called when data is READY TO BE READ
            from `fd` file descriptor.

            ZRPC SERVER WILL *NOT* READ ANY DATA FROM THE FILE DESCRIPTOR.
            That is the responsibility of the `callback` callable.

            callback:
                Method (with no arguments) to be called on `fd` read event.

        unregister(self, fd)
            Unregister file-like object `fd`.

        run()
            Run ZRPC server forever.

        run_once(timeout=None)
            Wait for a single ZRPC event (request or file descriptor) and call
            the appropriate method (RPC method or file descriptor callback).

            If `timeout` is not None, wait `timeout` seconds for an event
            before returning.

            timeout:
                Number of seconds to wait for an event before returning,
                whether any event happened or not.

     rpc_method:
        Decorator for specifying RPC methods.

        Should only be used on class methods.


### Client:

    class Client:
        __init__(socket_dir='/tmp/zrpc_sockets/')
            socket_dir:
                Directory where ZRPC UNIX sockets will be placed.
                Server must use the same `socket_dir` as Client in order to
                be reachable.

                default: '/tmp/zrpc_sockets'

        call(service, method, payload=None, timeout=None)
            Call an RPC method of a service with a payload.

            If `timeout` is None, blocks indefinitely.
            If `timeout` is a number, blocks `timeout` seconds and raises
            RPCTimeoutError on timeout.

            service:
                Name of the service to be called.

            method:
                Name of the method to be called.

            payload:
                Python object to be sent as payload.

                Since ZRPC uses `msgpack`, it supports all types that msgpack
                does:
                    - dict
                    - list
                    - int
                    - float (including 'inf' and '-inf')
                    - str
                    - bytes

            timeout:
                Number of seconds to wait for an RPC method to complete.
                If exceeded, raises `RPCTimeoutError`.
                If None, waits indefinitely.
