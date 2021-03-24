"""
This module contains ZRPC-specific exceptions.
"""


class EncodeError(ValueError):
    pass


class DecodeError(TypeError):
    pass
