"""
This module contains ZRPC-specific exceptions.
"""


class ZRPCBaseError(Exception):
    """ Base class for all ZRPC exceptions """


class SerializationError(ZRPCBaseError):
    """ Raised on serialization error """


class ConnectError(ZRPCBaseError):
    """ Raised on bind/connect error """


class RPCError(ZRPCBaseError):
    """ Raised on RPC method call exception """


class RPCTimeoutError(RPCError):
    """ Raised on RPC method call timeout """
