"""
Module for converting unix signals (see: `man signal`) into zrpc events.

To use with a zrpc Server, start() the signal handler and register the socket
obtained by SignalHandler.get_socket().
The received value will be the signal name in bytes (e.g. b"SIGTERM").

User API:

  SignalHandler(*signals):
    |- start() / stop() / __enter__() / __exit__()
    |
    |- get_socket()
"""
import logging
import signal

import zmq


logger = logging.getLogger(__name__)


class SignalHandler:

    def __init__(self, *signums):
        if not signums:
            raise TypeError('Must handle at least one signum')

        context = zmq.Context.instance()
        signal_recv_socket = context.socket(zmq.PULL)
        signal_send_socket = context.socket(zmq.PUSH)

        self.__context = context
        self.__signal_recv_socket = signal_recv_socket
        self.__signal_send_socket = signal_send_socket
        self.__old_signal_handlers = {}
        self.__signums = signums

    def __enter__(self, *args, **kwargs):
        self.start(*args, **kwargs)
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.stop()

    def start(self):
        signums = self.__signums
        logger.info(f'Handling signals: {signums}')

        self.__signal_recv_socket.bind('inproc://#signal')
        self.__signal_send_socket.connect('inproc://#signal')

        for signum in signums:
            old_handler = signal.signal(signum, self.__send_signal_message)
            self.__old_signal_handlers[signum] = old_handler

    def stop(self):
        signums = self.__signums
        for signum in signums:
            old_handler = self.__old_signal_handlers[signum]
            signal.signal(signum, old_handler)
        self.__signal_recv_socket.close(linger=0)
        self.__signal_send_socket.close(linger=0)

    def get_socket(self):
        """ Return socket for Poll()-ing """
        return self.__signal_recv_socket

    def __send_signal_message(self, signum, stack_frame):
        self.__signal_send_socket.send(signal.Signals(signum).name.encode())
