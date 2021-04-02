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

---

TODO: API
