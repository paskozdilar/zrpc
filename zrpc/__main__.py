#!/usr/bin/env python3

import argparse
import ast
import logging
import os

from zrpc.server import Server, rpc_method
from zrpc.client import Client


def main(argv=None):
    try:
        args = parse_args(argv)
        logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
        call(args.service, args.method, args.payload)
    except Exception as exc:
        logging.error(exc)
    except KeyboardInterrupt:
        pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='zrpc',
                                     description='CLI interface for ZRPC',
                                     epilog='Set environment variable '
                                            'ZRPC_SOCKET_DIR to change the '
                                            'socket directory '
                                            '(default: /tmp/zrpc_sockets)')

    parser.add_argument('-d', '--debug',
                        help='Turn on debug logs',
                        action='store_true')
    parser.add_argument('service',
                        help='Service name',
                        metavar='SERVICE')
    parser.add_argument('method',
                        help='Method to call',
                        metavar='METHOD')
    parser.add_argument('payload',
                        help='Payload to send [python object]',
                        metavar='PAYLOAD',
                        nargs='?',
                        type=ast.literal_eval,
                        default=None)

    return parser.parse_args(argv)


def call(service, method, payload):
    if 'ZRPC_SOCKET_DIR' in os.environ:
        client = Client(socket_dir=os.environ.get('ZRPC_SOCKET_DIR'))
    else:
        client = Client()

    response = client.call(service, method, payload)
    print(response)


if __name__ == '__main__':
    main()
