#!/usr/bin/env python3

import argparse
import ast
import logging
import os
import pprint
import signal
import sys

from zrpc.server import Server, rpc_method
from zrpc.client import Client


def signal_handler(*args, **kwargs):
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, signal_handler)


def main(argv=None):
    arguments = parse_args(argv)

    try:
        logging.basicConfig(level=logging.DEBUG
                            if arguments.debug
                            else logging.INFO)
        globals()[arguments.command](arguments)
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:
        # Python flushes standard streams on exit; redirect remaining output
        # to devnull to avoid another BrokenPipeError at shutdown
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(1)
    except Exception as exc:
        logging.error(exc, exc_info=arguments.debug)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='zrpc',
                                     description='CLI interface for ZRPC',
                                     epilog='Set environment variable '
                                            'ZRPC_SOCKET_DIR to change the '
                                            'socket directory '
                                            '(default: /tmp/zrpc_sockets)')

    subparsers = parser.add_subparsers(title='command',
                                       dest='command')

    call_parser = subparsers.add_parser(name='call',
                                        description='Call an RPC method.')

    parser.add_argument('-d', '--debug',
                        help='Turn on debug logs',
                        action='store_true')
    call_parser.add_argument('-c', '--count',
                             help='Send N requests ("inf" for loop)',
                             default=1,
                             type=lambda x: float(x) if x == 'inf' else int(x))
    call_parser.add_argument('-t', '--timeout',
                             help='Time to wait for response before giving up',
                             default=float('inf'),
                             type=lambda x: float(x) if x == 'inf' else int(x))
    call_parser.add_argument('server',
                             help='Server name',
                             metavar='SERVER')
    call_parser.add_argument('method',
                             help='Method to call',
                             metavar='METHOD')

    class _Kwarg:
        def __init__(self, key, value):
            self.key = key
            self.value = value

    def arg_or_kwarg(string):
        try:
            return ast.literal_eval(string)
        except (ValueError, SyntaxError) as exc:
            pass

        if '=' not in string:
            return string

        try:
            keyword = string.split('=')[0]
            value_string = '='.join(string.split('=')[1:])
            return _Kwarg(keyword, ast.literal_eval(value_string))
        except (ValueError, SyntaxError) as exc:
            pass

        if '=' not in value_string:
            return _Kwarg(keyword, value_string)

        raise argparse.ArgumentError(exc)

    call_parser.add_argument('args',
                             help='Positional arguments (python object)',
                             metavar='ARGS',
                             nargs='*',
                             type=arg_or_kwarg,
                             default=None)

    # This one is only for aestethic purposes - args parses everything
    call_parser.add_argument('kwargs',
                             help='Keyword arguments (key=python object)',
                             metavar='KWARGS',
                             nargs='*',
                             default=None)

    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        sys.exit(1)
    # Everything is parsed through args.
    # Blame argparse writers.
    arguments.kwargs = dict((kwarg.key, kwarg.value)
                            for kwarg in arguments.args
                            if isinstance(kwarg, _Kwarg))
    arguments.args = tuple(arg
                           for arg in arguments.args
                           if not isinstance(arg, _Kwarg))

    return arguments


def call(arguments):
    server = arguments.server
    method = arguments.method
    args = arguments.args
    kwargs = dict(arguments.kwargs)
    timeout = arguments.timeout
    count = arguments.count

    print(args, kwargs)
    exit()

    client = Client(socket_dir=os.environ.get('ZRPC_SOCKET_DIR'))

    counter = 0
    while counter < count:
        response = client.call(server=server, 
                               method=method,
                               args=args, 
                               kwargs=kwargs,
                               timeout=timeout)
        pprint.pprint(response)
        counter += 1


if __name__ == '__main__':
    main()
