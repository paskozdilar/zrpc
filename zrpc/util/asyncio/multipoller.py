"""
Abstracts async polling with a given zmq.asyncio.Poller.

User API:

  Multipoller(poller):
    |- listen(request_id)
"""
import asyncio
import collections
import logging

import zmq.asyncio

from zrpc.serialization import deserialize


logger = logging.getLogger(__name__)


class Multipoller:
    def __init__(self, poller: zmq.asyncio.Poller):
        self.poller = poller

        self.call_lock = None
        self.call_count = 0
        self.multipoller_task = None

        self.response_map = collections.defaultdict(asyncio.Queue)

    async def __aenter__(self):
        if self.call_lock is None:
            self.call_lock = asyncio.Lock()

        async with self.call_lock:
            # If first caller, create poller task
            if self.call_count == 0:
                self.multipoller_task = asyncio.create_task(self._multipoll())
            self.call_count += 1

    async def __aexit__(self, exc_type, exc_value, exc_traceback):
        async with self.call_lock:
            # If first caller, destroy poller task
            self.call_count -= 1
            if self.call_count == 0:
                self.multipoller_task.cancel()
                await self.multipoller_task
                self.response_map.clear()

    async def _multipoll(self):
        logger.info('Multipoll started.')
        try:
            while True:
                for socket, _ in await self.poller.poll():
                    null, response_data = await socket.recv_multipart()
                    [response_id, payload, is_exception] = deserialize(response_data)
                    self.response_map[response_id].put_nowait(response_data)
        except asyncio.CancelledError:
            logger.info('Multipoll stopped.')

    async def poll_and_recv(self, request_id: zmq.Socket):
        async with self:
            return await self.response_map[request_id].get()
