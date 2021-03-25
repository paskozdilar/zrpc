#!/usr/bin/env python3

from zrpc.utils.serialization import serialize, deserialize



def test_builtin():
    """
    Test serialization for builtin types:
        - null
        - bool
        - numeric (int/float)
        - strings (unicode/bytes)
    """
    dataset = (
        None,
        True, False,
        1000, 3.14, float('inf'), float('-inf'),
        'asdf', b'qwerzxcv\x0f',
    )
    
    for data in dataset:
        assert data == deserialize(serialize(data))


def test_containers():
    """
    Test serialization for container types:
        - list
        - dict
    """
    dataset = (
        [
            None,
            True, False,
            1000, 3.14, float('inf'), float('-inf'),
            'asdf', b'qwerzxcv\x0f'
        ],
        {
            'a': None,
            'b': True,
            'c': False,
            'd': 1000,
            'e': 3.14,
            'f': float('inf'),
            'g': float('-inf'),
            'h': b'qwerzxcv\x0f',
            'i': 'asdf',
        },
    )

    for data in dataset:
        assert data == deserialize(serialize(data))


def test_compount_objects():
    """
    Test serialization for compound object types.
    """
    dataset = (
        {
            'asdf': b'\x01\x23\x45\x67',
            'qwer': 1234.5678,
            'zxcv': [
                float('inf'),
                float('-inf'),
                0.,
                0.0000000000000000000001,
                {
                    'i': 3,
                    'j': 4,
                },
            ],
        },
        None,
        [
            True,
            {
                'key1': False,
                'key2': [2.71828],
            },
        ],
    )

    for data in dataset:
        assert data == deserialize(serialize(data))
