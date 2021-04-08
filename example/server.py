#!/usr/bin/env python3

from zrpc.server import Server, rpc_method


class TestServer(Server):
    counter = 0

    @rpc_method
    def func(self, payload):
        self.counter += 1
        print('RPC request [%s]: %s' % (self.counter, payload))
        return 'func', payload


def main():
    TestServer(name='test_server').run()


if __name__ == '__main__':
    try:
        TestServer().run()
    except KeyboardInterrupt:
        pass
