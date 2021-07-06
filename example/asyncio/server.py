#!/usr/bin/env python3

import asyncio
from zrpc.asyncio.server import Server, rpc_method


class TestServer(Server):
    counter = 0

    @rpc_method
    async def func(self, arg, kwarg=None):
        self.counter += 1
        print('RPC request [%s]: arg %s, kwarg %s' % (self.counter, arg, kwarg))
        await asyncio.sleep(0.1)
        return 'func', arg, kwarg


async def main():
    await TestServer(name='test_server').run()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
