#!/usr/bin/env python3

import asyncio
import sys
import multiprocessing

from zrpc.asyncio.server import Server


queue = multiprocessing.Queue()


class TestServer(Server):

    async def run(self):
        self.register(queue._reader.fileno(), self.queue_event)
        await super().run()

    async def queue_event(self):
        value = queue.get()
        print('Got value:', value)
        raise KeyboardInterrupt


queue.put('foo')

try:
    asyncio.run(TestServer().run(), debug=True)
except KeyboardInterrupt:
    pass
