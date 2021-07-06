#!/usr/bin/env python3

import asyncio
import datetime
from zrpc.asyncio.client import Client


CLIENTS = 10


async def main():
    client = Client()

    async def call_method(*args, **kwargs):
        while True:
            timestamp = datetime.datetime.now().strftime('%F_%H-%M-%S-%f')
            print('CALL', args, kwargs, flush=True)
            response = await client.call(server='test_server',
                                         method='func',
                                         args=args,
                                         kwargs=kwargs)
            assert response[1] == args[0] and response[2] == kwargs['kwarg']
            print('RPC response [%s]: %s' % (timestamp, response))

    await asyncio.gather(*[call_method(i, kwarg=i) for i in range(CLIENTS)])


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
