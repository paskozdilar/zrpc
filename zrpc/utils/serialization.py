"""
This module defines a thin wrapper over `msgpack` serialization package.

The rest of the ZRPC shall *only* use `serializer.dumps()` and
`serializer.loads()` for serialization.

---

Module API:

    - serialize(obj) -> bytes
        Serialize JSON-serializable object into bytes.

    - deserialize(bytes) -> obj
        Deserialize bytes into JSON-serializable object.
"""

import typing
import msgpack

from zrpc.utils.exceptions import SerializationError


JSON = typing.Union[
        str,
        int,
        float,
        bool,
        None,
        typing.Mapping[str, 'JSON'],
        typing.List['JSON']
]


def serialize(obj: JSON) -> bytes:
    try:
        return msgpack.dumps(obj)
    except (TypeError, ValueError) as exc:
        raise SerializationError('Cannot encode object') from exc


def deserialize(data: bytes) -> JSON:
    try:
        return msgpack.loads(data)
    except (UnicodeDecodeError,
            ValueError,
            msgpack.exceptions.ExtraData,
            msgpack.exceptions.FormatError,
            msgpack.exceptions.StackError) as exc:
        raise SerializationError('Cannot decode object') from exc
