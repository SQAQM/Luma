"""Bounded strict UTF-8 length-prefixed JSON; no payload-bearing exceptions."""
import json
import math
import re
import struct

MAX_FRAME = 64 * 1024 * 1024
MAX_DEPTH = 64


class FrameError(ValueError):
    def __init__(self, code='protocol_error'):
        self.code = code
        super().__init__(code)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FrameError()
        result[key] = value
    return result


def _check(value):
    stack = [(value, 0)]
    visits = 0
    while stack:
        item, depth = stack.pop()
        visits += 1
        if visits > 500_000 or depth > MAX_DEPTH:
            raise FrameError()
        if isinstance(item, str):
            item.encode('utf-8', 'strict')
        elif type(item) is int:
            if not -(2**63) <= item < 2**63:
                raise FrameError()
        elif type(item) is float:
            if not math.isfinite(item):
                raise FrameError()
        elif isinstance(item, dict):
            stack.extend((key, depth+1) for key in item)
            stack.extend((child, depth+1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth+1) for child in item)
        elif item is not None and type(item) is not bool:
            raise FrameError()


def decode(data):
    try:
        value = json.loads(data.decode('utf-8', 'strict'), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(FrameError()))
        if not isinstance(value, dict):
            raise FrameError()
        _check(value)
        return value
    except (UnicodeError, ValueError, RecursionError) as error:
        raise FrameError() from None


def encode(value):
    try:
        _check(value)
        body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (UnicodeError, ValueError, RecursionError):
        raise FrameError('response_invalid') from None
    if not 1 <= len(body) <= MAX_FRAME:
        raise FrameError('response_too_large')
    return struct.pack('<I', len(body)) + body


def _read(stream, length):
    result = bytearray()
    while len(result) < length:
        data = stream.read(length-len(result))
        if not data:
            raise FrameError()
        result.extend(data)
    return bytes(result)


def read_frame(stream):
    header = stream.read(4)
    if not header:
        return None
    if len(header) < 4:
        header += _read(stream, 4-len(header))
    length, = struct.unpack('<I', header)
    if not 1 <= length <= MAX_FRAME:
        raise FrameError()
    return decode(_read(stream, length))


def validate_request(value):
    if set(value) != {'version', 'requestId', 'generation', 'identity', 'method', 'payload'}:
        raise FrameError('invalid_request')
    if type(value['version']) is not int or value['version'] != 1:
        raise FrameError('protocol_mismatch')
    if not isinstance(value['requestId'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value['requestId']):
        raise FrameError('invalid_request')
    if type(value['generation']) is not int or not 0 <= value['generation'] < 2**63:
        raise FrameError('invalid_request')
    if not isinstance(value['identity'], str) or len(value['identity'].encode('utf-8')) > 512:
        raise FrameError('invalid_request')
    if not isinstance(value['method'], str) or not re.fullmatch(r'[a-z][a-z0-9_.]{0,63}', value['method']):
        raise FrameError('invalid_request')
    if not isinstance(value['payload'], dict):
        raise FrameError('invalid_request')
    return value
