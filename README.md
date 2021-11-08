# zrpc

- [Introduction](#introduction)
  * [Pros](#pros)
  * [Cons](#cons)
- [Usage](#usage)
  * [Example server](#example-server)
  * [Example client](#example-client)
  * [Example asyncio server](#example-asyncio-server)
  * [Example asyncio client](#example-asyncio-client)
  * [Example CLI client](#example-cli-client)
- [API](#api)
  * [Server](#server)
  * [Client](#client)
  * [CLI](#cli)


## Introduction

Fast and reliable single-machine RPC library.

**ZRPC** uses [ZeroMQ](https://zeromq.org/) for UNIX-socket based inter-process
communication, and [MessagePack](https://msgpack.org/) for serialization.

Unlike many network-oriented RPC frameworks, ZRPC is optimized for
single-machine usage by completely bypassing TCP/IP and using UNIX-sockets
instead.


### Pros:

- minimal setup - *server name* is used as identifier
- minimal serialization/transport/deserialization overhead
- asynchronous RPC calls via `zrpc.asyncio`
- CLI interface for simple testing


### Cons:

- not usable in networking environment (at least without `socat` hacks)
- RPC-only - no support for Publish-Subscribe or any other network architecture


## Usage:

See `example/` directory for runnable example.


### Example server:

```python
    from zrpc.server import Server, rpc_method

    class TestServer(Server):
        @rpc_method
        def func(self, arg, kwarg=None):
            """ Return tuple containing string 'func' and arguments 'arg' and 'kwarg' """
            print('RPC request: arg %s, kwarg %s', (arg, kwarg))
            return 'func', arg, kwarg

    TestServer(name='test_server').run()
```


### Example client:

```python
    from zrpc.client import Client

    client = Client()
    response = client.call(server='test_server',
                           method='func',
                           args=('haha',),
                           kwargs={'kwarg': 'brt'})
    print('RPC response [%s]: %s' % (timestamp, response))
```

---

`zrpc.asyncio` equivalent:

### Example asyncio server:

```python
    import asyncio
    from zrpc.asyncio.server import Server, rpc_method

    class TestServer(Server):
        @rpc_method
        async def func(self, arg, kwarg=None):
            """ Return tuple containing string 'func' and arguments 'arg' and 'kwarg' """
            print('RPC request: arg %s, kwarg %s', (arg, kwarg))
            return 'func', arg, kwarg

    asyncio.run(TestServer(name='test_server').run())
```

### Example asyncio client:

```python
    from zrpc.client import Client

    client = Client()
    response = asyncio.run(client.call(server='test_server',
                                       method='func',
                                       args=('haha',),
                                       kwargs={'kwarg': 'brt'}))
    print('RPC response [%s]: %s' % (timestamp, response))
```


### Example CLI client:

Assuming we are running a `test_server` from one of server examples above:

```bash
    ~$ zrpc list
    ['test_server']

    ~$ zrpc list test_server
    func(arg, kwarg=None):
            Return tuple containing string 'func' and arguments 'arg' and 'kwarg'

    ~$ zrpc call test_server func True kwarg=None
    ['func', False, None]
```


## API:

### Server:

    class Server:
        __init__(name=None, socket_dir='/tmp/zrpc_sockets')
            Create ZRPC server.

            name:
                ZRPC server name, specified in client `call()` method.
                If None, then server name is derived from class name,
                converted from CamelCase to snake_case (i.e. if class name
                is `MyClass`, then server name will be `my_class` and clients
                shall call it with `call(server='my_class', ...)` ).

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

        call(server, method, args=None, kwargs=None, timeout=None)
            Call an RPC method of a server with specified args and kwargs.

            If `timeout` is None, blocks indefinitely.
            If `timeout` is a number, blocks `timeout` seconds and raises
            RPCTimeoutError on timeout.

            server:
                Name of the server to be called.

            method:
                Name of the method to be called.

            args:
                Tuple of Python objects to be sent as positional arguments.

                Since ZRPC uses `msgpack`, it supports all types that msgpack
                does:
                    - dict
                    - list
                    - int
                    - float (including 'inf' and '-inf')
                    - str
                    - bytes
                    - None

            kwargs:
                Dict of Python objects to be sent as keyword arguments.

            timeout:
                Number of seconds to wait for an RPC method to complete.
                If exceeded, raises `RPCTimeoutError`.
                If None, waits indefinitely.

        list(server=None, timeout=None):
            If `server` is `None`, return list of RPC servers currently online.
            If `server` is not `None`, return dictionary:

            {
                <FUNCNAME>: {
                    "docstring": <DOCSTRING>,
                    "args_required": <POSITIONAL>,
                    "args_optional": <KEYWORD>,
                },
                ...
            }


### CLI:

    ### zrpc --help:

    usage: zrpc [-h] [-d] {call,list} ...

    CLI interface for ZRPC

    optional arguments:
      -h, --help   show this help message and exit
      -d, --debug  Turn on debug logs


    ### zrpc list --help:

    usage: zrpc list [-h] [SERVER]

    List available servers.

    positional arguments:
      SERVER      List methods of a server


    ### zrpc call --help:

    usage: zrpc call [-h] [-c COUNT] [-t TIMEOUT] SERVER METHOD [ARGS [ARGS ...]] [KWARGS [KWARGS ...]]

    Call an RPC method.

    positional arguments:
      SERVER                Server name
      METHOD                Method to call
      ARGS                  Positional arguments (python object)
      KWARGS                Keyword arguments (key=python object)

    optional arguments:
      -h, --help            show this help message and exit
      -c COUNT, --count COUNT
                            Send N requests ("inf" for loop)
      -t TIMEOUT, --timeout TIMEOUT
                            Time to wait for response before giving up
