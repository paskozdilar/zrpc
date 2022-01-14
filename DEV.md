# ZRPCv2 Development

This document represents the ideas behind ZRPCv2.

The ideas are presented as a set of problems with solutions, which are
joined into a coherent whole in the end.

**GOAL:** Implement a brokerless single-machine RPC library.

## Table of contents

- [Introduction](#introduction)
- [1) Service discovery](#1-service-discovery)
- [2) Service connection](#2-service-connection)
    [Connect and bind](#connect-and-bind)
- [3) Message routing](#3-message-routing)
- [4) RPC message format](#4-rpc-message-format)
    * [Request](#request)
    * [Response](#response)
    * [Message](#message)
- [5) RPC reliability](#5-rpc-reliability)
    * [Acknowledgements](#acknowledgements)
    * [Status request](#status-request)
    * [Errors](#errors)
- [6) RPC protocol](#6-rpc-protocol)
    * [Message types](#message-types)
    * [Client protocol](#client-protocol)
        + [Polling](#polling)
        + [Client implementation](#client-implementation)
    * [Server protocol](#server-protocol)


## Introduction

[ZeroMQ](zeromq.org) provides a high-level messaging interface through PUB and
SUB sockets - those sockets work on a best-effort delivery basis and they never
block, so if a message cannot be delivered, it is silently dropped.  It works
with UNIX sockets so socket names can be used as service identifiers.

[MsgPack](msgpack.org) provides an efficient serialization mechanism for most
of python built in types, which will allow us to send objects as bytes.

ZeroMQ and MsgPack will provide basic building blocks for ZRPCv2:

```python3
# examples/01_zmq_msgpack.py

import time
import zmq
import msgpack


# create socket
ctx = zmq.Context()
send_socket = ctx.socket(zmq.PUB)
recv_socket = ctx.socket(zmq.SUB)

# subscribe to everything
recv_socket.setsockopt(zmq.SUBSCRIBE, b'')

# bind/connect
#
# NOTE: ZeroMQ sockets can bind and connect to multiple addresses at the
#       same time - the only rule is that one address can be bound by at
#       most one socket at the same time.
send_socket.bind('ipc://test.sock')
recv_socket.connect('ipc://test.sock')

# wait for sockets to establish connection in the background
time.sleep(1)

# float('nan') also works, but breaks equality comparison
send_obj = {'dict': {'int': 0,
                     'float': 3.14,
                     'string': 'foo',
                     'bytes': b'bar',
                     'bool': True,
                     'null': None,
                     'inf': float('inf'),
                     'list': ['element'], }, }
send_msg = msgpack.packb(send_obj)
send_socket.send(send_msg)

recv_msg = recv_socket.recv()
recv_obj = msgpack.unpackb(recv_msg)

assert send_obj == recv_obj
print('send/recv success')
```


## 1) Service discovery

The first problem in making a brokerless RPC is service discovery:  how does a
service know which other services are running?

We can use presence of UNIX sockets as an indicator - if a UNIX socket exists,
then service *has been alive* some time in the past.  However, we also need a
mechanism that can reliably indicate whether or not the service *is alive*.

One such tool is `filelock` - it locks a file, and blocks on `acquire()` iff
there is a *running* process already holding the lock:

```python3
# examples/02_filelock.py
import filelock

LOCK_FILE = '/tmp/service.lock'

with filelock.FileLock(LOCK_FILE):
    print('No two processes can execute this at the same time!')
```

If each service locks its own lockfile, then we can reliably detect running
services just by attempting to lock all lockfiles and checking if `acquire()`
fails.


## 2) Service connection

The second problem is the problem of service connection:  how do we connect
services to each other?

Without a broker, each service must connect to all others.  One way to do that
is to make each service `bind()` to a socket with their own name, and
`connect()` to all other [alive service](#1-service-discovery) sockets.  That
way, every service will be connected to every other, either by connecting to it
directly, or by being connected to.

While double socket connections should not be a problem for ZeroMQ, it would be
good practice to use a global `filelock` while binding/connecting:

```python3
import filelock

GLOBAL_LOCK_FILE = '/tmp/global.lock'

with filelock.FileLock(GLOBAL_LOCK_FILE):
    connect_and_bind()
```

Since we will need a path for our sockets and locks, we will first define a
paths class.

We will need:
- root ZRPC directory
- ZRPC global lock file
- ZRPC per-service lock directory
- sender (publisher) socket directory
- receiver (subscriber) socket directory

Using:
- `/tmp/zrpc/` as root ZRPC directory
- `global.lock` as lock file
- `service_lock/` as service lock directory
- `sender/` as sender socket directory
- `receiver/` as receiver socket directory

we can device the Paths class as follows:

```python3
# zrpc/paths.py
import os

class Paths:
    ZRPC_DIR = os.environ.get('ZRPC_DIR', '/tmp/zrpc')
    GLOBAL_LOCK_FILE = os.path.join(ZRPC_DIR, 'global.lock')
    SERVICE_LOCK_DIR = os.path.join(ZRPC_DIR, 'service_lock')
    SENDER_DIR = os.path.join(ZRPC_DIR, 'sender')
    RECEIVER_DIR = os.path.join(ZRPC_DIR, 'receiver')

    @classmethod
    def global_lock_file(cls) -> str:
        return self.GLOBAL_LOCK_FILE

    @classmethod
    def service_lock_dir(cls) -> str:
        return self.SERVICE_LOCK_DIR

    @classmethod
    def service_lock_file(cls, service) -> str:
        return os.path.join(cls.service_lock_dir, service)

    @classmethod
    def sender_dir(cls) -> str:
        return cls.SENDER_DIR

    @classmethod
    def receiver_dir(cls) -> str:
        return cls.RECEIVER_DIR

    @classmethod
    def sender_socket(cls, service: str) -> str:
        return os.path.join(cls.sender_dir, service)

    @classmethod
    def receiver_socket(cls, service: str) -> str:
        return os.path.join(cls.receiver_dir, service)
```

We can now create a single lock class for both global lock, service locks and
checking if service is alive:

```python3
# zrpc/lock.py
import os
import filelock

from zrpc.paths import Paths


class Lock:
    @staticmethod
    def global_lock() -> filelock.FileLock:
        return filelock.FileLock(Paths.global_lock_file())

    @staticmethod
    def service_lock(service: str) -> filelock.FileLock:
        return filelock.FileLock(Paths.service_lock_file(service))

    @classmethod
    def service_locked(cls, service: str) -> bool:
        lock = cls.service_lock(service)
        try:
            lock.acquire(timeout=0)
        except filelock.Timeout:
            return True
        else:
            lock.release()
            return False
```


### Connect and bind

Since a service needs to both send and receive messages, each service will have
a pair of sockets, PUB and SUB.  We can encapsulate the socket pair into a
single socket class:

```python3
# zrpc/socket.py
import contextlib
import zmq, zmq.asyncio

frmo zrpc.message_type import MessageType
from zrpc.paths import Paths


class Socket:
    def __init__(self, name: str):
        self.context = zmq.asyncio.Context()
        self.name = name
        self.send_socket = self.context.socket(zmq.PUB)
        self.recv_socket = self.context.socket(zmq.PUB)

    def connect_and_bind(self):
        for service in os.listdir(Paths.service_lock_dir()):
            if Lock.service_locked(service):
                self.send_socket.connect(Paths.receiver_socket(service))
                self.recv_socket.connect(Paths.sender_socket(service))
            else:
                os.remove(Paths.receiver_socket(service))
                os.remove(Paths.sender_socket(service))
                os.remove(Paths.service_lock_file(service))
        self.send_socket.bind(Paths.sender_socket(self.name))
        self.receive_socket.bind(Paths.receiver_socket(self.name))
        self.receive_socket.setsockopt(zmq.SUBSCRIBE, self.name.encode() + b'\0')

    async def send(self, message: bytes):
        await self.send_socket.send(message)

    async def recv(self) -> bytes:
        return await self.recv_socket.recv()
```
**NOTE**: meaning of the last line is explained in the following section.


## 3) Message routing

The third problem is the problem of message routing - now that we have
connected each service to every other service, how do we send a message only to
the service we want?

ZeroMQ SUB sockets implements a simple prefix-based topic mechanism:

```python3
# examples/03_topic.py
import time
import zmq

# create sockets
ctx = zmq.Context()
send_socket = ctx.socket(zmq.PUB)
recv_socket = ctx.socket(zmq.SUB)

# subscribe to a topic
topic = b'test'
recv_socket.setsockopt(zmq.SUBSCRIBE, topic)

# set receive timeout to 1s
recv_socket.setsockopt(zmq.RCVTIMEO, 1000)

# connect sockets
send_socket.bind('ipc://test.sock')
recv_socket.connect('ipc://test.sock')

# wait for bind/connect to settle
time.sleep(1)

# send messages
send_socket.send(b'message without topic')
send_socket.send(topic + b'message with topic')

# receive messages
try:
    while True:
        msg = recv_socket.recv()
        print('Got message:', msg[len(topic):])
except zmq.Again:
    pass
```

Since we identify services by their name, we can use the service name as topic.

In order to prevent prefix clashes (e.g. `test_service` and `test_service_2`
requests both received by `test_service`), we can use a zero-byte (`b'\0'`)
delimiter between topic and the rest of the message.


## 4) RPC message format

Now that we have established communication between services, we need to:
- define formats for requests and responses
- define methods for (de)serialization


### Request

Since a service can have many methods, and different methods can receive
different numbers of positional and keyword arguments, we need a way to
communicate which method we want to call and what arguments are we passing to
it.

Since requests can be lost and need to be resent, we need a way to identify
duplicated requests. We can use an UUID for identification.

We will need a method to convert request to a Python builtin type - we can use
`dataclasses.asdict` for that.

We can define a Request dataclass:

```python3
# zrpc/request.py
import dataclasses

@dataclasses.dataclass
class Request:
    method: str
    args: list
    kwargs: dict
    request_id: int = dataclasses.field(default_factory=lambda: uuid.uuid4().int)
```


### Response

Since clients may want to send multiple concurrent requests to a service, we
need to identify to which request a response is responding to.  We can use the
`request_id` from the request for that.

We also might want to communicate to clients that an exception has occurred. It
would be complex and error-prone to send exception types, so we will just send
an exception message and raise a generic `zrpc.Error` on the client side.

```python3
# zrpc/response.py
import dataclasses

@dataclasses.dataclass
class Response:
    result: object
    is_exception: bool
    request_id: int
```


### Message

Whether we are sending a request or a response, we need to:
- send service name and zero byte as message prefix to pass the service's
  `zmq.SUBSCRIBE` filter
- send sender name so service knows where to reply to

Also, we might need to send messages that are neither requests or responses -
we can send an enumerated message type with each message:

```python3
# zrpc/message_type.py
from enum import IntEnum

class MessageType(IntEnum):
    REQUEST = 1
    RESPONSE = 2
```

We can define a serializable Message dataclass using MsgPack:

```python3
# zrpc/message.py
import dataclasses
import msgpack

from zrpc.message_type import MessageType

@dataclasses.dataclass
class Message:
    receiver: str
    sender: str
    message_type: MessageType
    payload: object = dataclasses.field(default=None)

    def serialize(self):
        return b'\0'.join([self.receiver.encode(),
                           self.sender.encode(),
                           self.message_type.to_bytes(1),
                           msgpack.packb(payload)])

    @classmethod
    def deserialize(cls, b_message):
        b_receiver, b_sender, b_message_type, b_payload = b_message.split(b'\0', 3)
        return Message(receiver=b_receiver.decode(),
                       sender=b_sender.decode(),
                       message_type=MessageType(int.from_bytes(b_message_type),
                                                byteorder=sys.byteorder),
                       payload=msgpack.unpackb(b_payload))
```

NOTE: We need to make sure that payload is a Python builtin object - if it is
a Request or Response, we must remember to call the `dataclasses.asdict()`
method.


## 5) RPC reliability

The fifth problem is the problem of RPC reliability - i.e. when we send a
request to a service, how do we know that:
- the service has received our request?
- the service has not crashed while processing our request?
- reponse, if sent, was not dropped by the SUB socket?

In order to know if the service has received any of our messages, including
requests, we get pass [acknowledgements](#acknowledgements) from the service.

In order to know if the service crashes, or response gets dropped by the SUB
socket, we can poll the service for [request status](#status-request), using
request identifier.  If the service is still processing the request, it should
tell client to wait a bit longer.

If the service does crash, it will not know about the request with the given
identifier, so we also need a message to communicate that.

If a message gets dropped by the SUB socket, the service should should resend
the response when polled for request status.

Let's add the new message types to the `MessageType` class:

```python3
# zrpc/message_type.py
from enum import IntEnum

class MessageType(IntEnum):
    REQUEST = 1
    RESPONSE = 2
    ACK = 3
    STATUS_REQUEST = 4
    STATUS_RESPONSE = 5
```


### Acknowledgements

Since we may send multiple requests to a service from a single client
concurrently, we need a way to know which acknowledgement corresponds to which
request.  We can use `request_id`.

We can create an Ack dataclass:

```python3
# zrpc/ack.py
import dataclasses

@dataclasses.dataclass
class Ack:
    request_id: int
```


### Status request

Status request needs to contain `request_id`:

```python3
# zrpc/status_request.py
import dataclasses

@dataclasses.dataclass
class StatusRequest:
    request_id: int
```

When a status request arrives, there can be three scenarios:
1. service is processing a request
2. service has finished the request and the result is still in the cache
3. service has crashed and does not remember the request

If scenario 2. happens, service needs to resend the result, so no special
response message is necessary.

If scenario 3. happens, service needs to tell client to resend the request,
so we can add a flag `resend`.

Otherwise, we assume that scenario 1. is the default:

```python3
# zrpc/status_response.py
import dataclass

@dataclasses.dataclass
class StatusResponse:
    request_id: int
    resend: bool
```


### Errors

This is a good time to define exceptions for two types of events:
1. server raised an exception
2. server has not sent a response within a given timeout

```python3
# zrpc/errors.py
class RPCError(Exception):
    pass

class RPCServerError(RPCError):
    pass

class RPCTimeoutError(RPCError, TimeoutError):
    pass
```


## 6) RPC protocol

The sixth problem is constructing the RPC protocol itself.

We will implement the protocol using the reliability mechanisms explained in
[section 5](#5-rpc-reliability).


### Message types

Since this protocol will be built in a request-response manner, we will
construct a table containing all request message types, and a list of response
message types for each of request message type:

|Request message type   |Response message type  |
|-----------------------|-----------------------|
|REQUEST                |RESPONSE (*)           |
|REQUEST                |ACK                    |
|STATUS_REQUEST         |STATUS_RESPONSE        |

(*) RESPONSE message arrives asynchronously, and terminates the STATUS_REQUEST
-> STATUS_RESPONSE loop.

Each response message contains request id from its corresponding request
message.


### Client protocol

To initiate an RPC call, client polls service with REQUEST/STATUS_REQUEST
messages and starts anticipating RESPONSE message at the same time.  If
RESPONSE message arrives, the RPC call is finished and the polling stops.


#### Anticipator

Since we need to anticipate certain messages, we will first implement an
anticipator.  It will contain a context manager method
`anticipate(message_type, sender, request_id=None)` that returns an
asyncio.Future, though which it will send a message with with given parameters
if it arrives before exiting the context manager.

```python3
import asyncio
import contextlib

from zrpc.message_type import MessageType
from zrpc.message import Message
from zrpc.socket import Socket


class Anticipator:
    def __init__(self, socket: Socket):
        self.socket = socket
        self.futures_dict = None
        self.task = None
        self.anticipators = 0

    @contextlib.contextmanager
    def anticipate(self, message_type: MessageType, sender: str, request_id: int = None):
        if task is None: 
            self.futures_dict = {}
            self.task = asyncio.create_task(self.__receive())
        self.anticipators += 1
        key = (message_type, sender, request_id)
        future = asyncio.Future()
        self.futures_dict[key] = future
        try:
            yield future
        finally:
            future.cancel()
            self.anticipators -= 1
            self.futures_dict.clear()
            if self.anticipators == 0:
                self.task.cancel()

    async def __receive(self):
        while True:
            message_bytes = await self.socket.recv()
            message = Message.deserialize(message_bytes)
            key = (message.message_type, message.sender, message.request_id)
            for future in self.futures_dict.get(key, []):
                future.set_result(message.payload)
```


#### Polling

1. Send REQUEST message
2. Wait for ACK within a timeout
    - If ACK does not come within the timeout, go back to step 1
3. Wait until the timeout
4. Send STATUS_REQUEST message
5. Wait for STATUS_RESPONSE within a timeout
    - If STATUS_RESPONSE does not come within the timeout, reset the timeout and go back to step 4
    - If STATUS_RESPONSE comes within the timeout, but the `resend` flag is set, go back to step 1
6. Wait until the timeout
7. Go back to step 3


#### Client implementation

Since service might behave as both client and server, we want to use a single
ZeroMQ socket for both of them, so we will pass a Socket object as parameter to
client and server.

This is what the client implementation looks like:

```python3
# zrpc/client.py
from zrpc.ack import Ack
from zrpc.errors import RPCServerError
from zrpc.message_type import MessageType
from zrpc.anticipator import Anticipator
from zrpc.request import Request
from zrpc.response import Response
from zrpc.socket import Socket
from zrpc.status_request import StatusRequest
from zrpc.status_response import StatusResponse


class Client:
    def __init__(self, socket: Socket):
        self.name = socket.name
        self.socket = socket
        self.anticipator = Anticipator(socket)

    async def call(self, service: str, method: str, args: list, kwargs: dict, timeout: float = None):
        request = Request(method, args, kwargs)
        response: Response
        response_bytes: bytes

        with self.anticipator.anticipate(message_type=MessageType.RESPONSE,
                                         sender=service,
                                         request_id=request.request_id) as response_task:
            while True:
                # 1) send_request
                await self.__send_request(service=service, request=request)

                # 2) start poller in the background
                poll_task = asyncio.create_task(self.__poll_status_requests(self, request.request_id))

                # 3) wait for result or resend
                done, pending = await asyncio.wait([poll_task, response_task], return_when=asyncio.FIRST_COMPLETED)
                if poll_task in done:
                    continue
                else:
                    poll_task.cancel()
                    response = await response_task
                    break

        if response.is_exception:
            raise RPCServerError(response.result)
        else:
            return response.result

    async def __send_request(self, service: str, request: Request):
        timeout = 0.1
        message = Message(receiver=service,
                          sender=self.socket.name,
                          message_type=MessageType.REQUEST,
                          payload=dataclasses.asdict(request))

        with self.anticipator.anticipate(MessageType.ACK, message.request_id) as ack_task:
            while True:
                # 1) send request
                await self.sender.send(message)

                # 2) wait for ack
                deadline = asyncio.create_task(asyncio.sleep(timeout))
                try:
                    response = await asyncio.wait_for(asyncio.shield(ack_task), timeout=timeout)
                except asyncio.TimeoutError:
                    # try again
                    continue
                else:
                    # 3) if ack, return
                    return

    async def __poll_status_request(self, service: str, request_id: int):
        """ Stops on 'resend' flag """
        timeout = initial_timeout = 0.1
        status_request = StatusRequest(request_id)
        message = Message(receiver=service,
                          sender=self.name,
                          message_type=MessageType.STATUS_REQUEST,
                          payload=dataclasses.asdict(status_request))

        with self.anticipate.anticipate(message_type=MessageType.STATUS_RESPONSE,
                                        service=service,
                                        request_id=request_id) as status_response_task:
            while True:
                # 1) send status request
                await self.sender.send(message)

                # 2) wait for status response
                deadline = asyncio.create_task(asyncio.sleep(timeout))
                try:
                    status_response = await asyncio.wait_for(asyncio.shield(status_response_task), timeout=timeout)
                except asyncio.TimeoutError:
                    # try again
                    continue
                else:
                    # 3) return if 'resend' flag is set
                    if status_response.resend:
                        return

                    # 4) otherwise increase the timeout and wait until deadline to continue
                    timeout += initial_timeout
                    await deadline
```


### Server protocol

Server protocol is simple - for each RPC method, do the following:

1. Anticipate REQUEST/STATUS_REQUEST message with corresponding method name
2. If REQUEST, create Future and map it to given request_id
3. then start task handler in the background
4. If STATUS_REQUEST

Task handler protocol is this:

1. Start status request handler in the background
2. Execute given method with given arguments
3. If result is returned, send it in a Response to the sender of the Request
4. If exception is raised, send it in a Response to the sender of the request,
   with `Response.is_exception` set to `True`.

But before we implement the protocol, we need a way to determine which methods
are exposed and which are not.  One approach is to consider all methods that
don't start with an underscore (`_`) as exposed, but it is (in my opinion) more
explicit if we use a decorator `rpc_method`.


#### RPC method decorator

For implementing the RPC method decorator, we can use the
[descriptor protocol](
https://docs.python.org/3/howto/descriptor.html#automatic-name-notification
) - the `__set_name__` function will be called every time a class attribute
(in our case, method) of the descriptor type is created:

```python3
# examples/04_descriptor_protocol.py

class Descriptor:
    def __init__(self, func):
        print('Created descriptor', func)
        self.func = func
    def __set_name__(self, owner, name):
        print('Created attribute', name, 'of type Descriptor in class', owner)
    def __get__(self, obj, objtype=None):
        if obj is not None:
            print('Evaluated object of type Descriptor in class instance', obj)
            return self.func.__get__(obj, obj.__class__)
        else:
            print('Evaluated object of type Descriptor in class', objtype)
            return self.func
    def __set__(self, obj, value):
        print('Setting', obj, 'Descriptor to', value)
        self.func = value


decorator = Descriptor


class Example:
    @decorator
    def func(self):
        pass

Example.func
Example().func()
Example().func = lambda: None
```


#### Server implementation

```python3
from zrpc.socket import Socket


class Server:
    def __init__(self, socket: Socket):
        self.name = socket.name
        self.socket = socket
        self.anticipator = Anticipator(socket)
        self.methods = {}

    async def run(self):
```


---

**TODO**
- sender reveiver
- request algorithm
- response algorithm
