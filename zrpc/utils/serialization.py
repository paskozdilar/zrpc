"""
This module defines a thin wrapper over `msgpack` serialization package.

The rest of the ZRPC shall *only* use `serializer.dumps()` and
`serializer.loads()` for serialization.

---

Module API:

    - dumps(obj) -> bytes
        Serialize JSON-serializable object into bytes.

    - loads(bytes) -> obj
        Deserialize bytes into JSON-serializable object.
"""

import typing
import msgpack
from .exceptions import (
        EncodeError,
        DecodeError,
)


JSON = typing.Union[
        str,
        int,
        float,
        bool,
        None,
        typing.Mapping[str, 'JSON'],
        typing.List['JSON']
]


def dumps(obj: JSON) -> bytes:
    try:
        return msgpack.dumps(obj)
    except (TypeError, ValueError) as exc:
        raise EncodeError('Cannot encode object') from exc


def loads(data: bytes) -> JSON:
    try:
        return msgpack.loads(data)
    except (UnicodeDecodeError,
            ValueError,
            msgpack.exceptions.ExtraData,
            msgpack.exceptions.FormatError,
            msgpack.exceptions.StackError) as exc:
        raise DecodeError('Cannot decode object') from exc
