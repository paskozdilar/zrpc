#!/usr/bin/env python3

from zrpc.server import Server, rpc_method


class TestServer(Server):
    counter = 0

    @rpc_method
    def func(self, arg, kwarg=None):
        self.counter += 1
        print('RPC request [%s]: arg %s, kwarg %s' % (self.counter, arg, kwarg))
        return 'func', arg, kwarg


def main():
    TestServer(name='test_server').run()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
