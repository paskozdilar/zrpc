#!/usr/bin/env python3

import datetime
import logging
from zrpc.client import Client


logging.basicConfig(level=logging.INFO)


def main():
    client = Client()

    while True:
        timestamp = datetime.datetime.now().strftime('%F_%H-%M-%S-%f')
        response = client.call(service='test_server',
                               method='func',
                               payload={'haha': 'brt'})
        print('RPC response [%s]: %s' % (timestamp, response))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
