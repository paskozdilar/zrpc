#!/usr/bin/env python3

import datetime
from zrpc.client import Client


def main():
    client = Client()

    while True:
        timestamp = datetime.datetime.now().strftime('%F_%H-%M-%S-%f')
        response = client.call(server='test_server',
                               method='func',
                               args=('haha',),
                               kwargs={'kwarg': 'brt'})
        print('RPC response [%s]: %s' % (timestamp, response))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
