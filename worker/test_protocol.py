import io
import json
import struct
import unittest
from pathlib import Path

from worker.protocol import FrameError, MAX_FRAME, decode, encode, read_frame, validate_request


class Chunked(io.BytesIO):
    def read(self, n=-1):
        return super().read(min(n, 3))


class ProtocolTests(unittest.TestCase):
    def test_versioned_asset_method_survives_envelope_validation(self):
        value=dict(version=1,requestId='preview',generation=1,identity='a'*64,method='catalog.preview.v2',payload={})
        self.assertEqual(validate_request(value),value)
    def test_shared_vectors(self):
        vectors = json.loads((Path(__file__).resolve().parents[1]/'contracts/engine-v1/fixtures/vectors.json').read_text(encoding='utf-8'))
        for value in vectors['valid']: self.assertIsInstance(decode(value.encode()), dict)
        for value in vectors['invalid']:
            with self.assertRaises(FrameError): decode(value.encode())
        for value in vectors['invalidUtf8Hex']:
            with self.assertRaises(FrameError): decode(bytes.fromhex(value))
    def test_fragmented_and_coalesced(self):
        values = [{"text": "雪山😀\n"}, {"x": 2}]
        stream = Chunked(b"".join(encode(v) for v in values))
        self.assertEqual([read_frame(stream), read_frame(stream)], values)
        self.assertIsNone(read_frame(stream))

    def test_invalid_frames(self):
        for data in [b'\x01', struct.pack('<I', 8)+b'{}', struct.pack('<I', 0), struct.pack('<I', MAX_FRAME+1), struct.pack('<I', 1)+b'\xff']:
            with self.subTest(data=data[:8]), self.assertRaises(FrameError):
                read_frame(io.BytesIO(data))

    def test_strict_json(self):
        for text in ['[]', '{"a":1,"a":2}', '{"x":NaN}', '{"x":"\\ud800"}', '{"x":9223372036854775808}', '['*66+'0'+']'*66]:
            with self.subTest(text=text[:40]), self.assertRaises(FrameError):
                decode(text.encode())

    def test_envelope_types(self):
        good = dict(version=1, requestId='r_1', generation=1, identity='image-v1', method='catalog.query', payload={})
        self.assertEqual(validate_request(good), good)
        for key, value in [('version', 2), ('generation', True), ('requestId', 'a'*65), ('payload', []), ('identity', 'x'*513)]:
            with self.subTest(key=key), self.assertRaises(FrameError):
                validate_request({**good, key: value})

    def test_worst_retained_unicode_not_truncated(self):
        prompt = '😀'*(512*1024)
        value = {'prompt': prompt, 'negative_prompt': prompt, 'parameters': {'raw': '界'*(1024*1024)}}
        self.assertEqual(read_frame(Chunked(encode(value))), value)
        # Default .NET JSON escaping can expand astral codepoints to12 bytes.
        self.assertLess(len(json.dumps(value, ensure_ascii=True).encode()), MAX_FRAME)


if __name__ == '__main__':
    unittest.main()
