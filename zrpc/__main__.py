#!/usr/bin/env python3

import argparse
import ast
import logging
import os
import signal

from zrpc.server import Server, rpc_method
from zrpc.client import Client


def signal_handler(*args, **kwargs):
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, signal_handler)


def main(argv=None):
    args = parse_args(argv)

    try:
        logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
        call(args)
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:
        # Python flushes standard streams on exit; redirect remaining output
        # to devnull to avoid another BrokenPipeError at shutdown
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(1)
    except Exception as exc:
        logging.error(exc, exc_info=args.DEBUG)


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
    parser.add_argument('-c', '--count',
                        help='Send N requests ("inf" for loop)',
                        default=1,
                        type=lambda x: float(x) if x == 'inf' else int(x))
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


def call(args):
    service = args.service
    method = args.method
    payload = args.payload
    count = args.count

    if 'ZRPC_SOCKET_DIR' in os.environ:
        client = Client(socket_dir=os.environ.get('ZRPC_SOCKET_DIR'))
    else:
        client = Client()

    counter = 0
    while counter < count:
        response = client.call(service, method, payload)
        print(response)
        counter += 1


if __name__ == '__main__':
    main()
