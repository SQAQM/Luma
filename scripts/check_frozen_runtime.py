"""Check shipped numerical dependencies with a fresh project-local test profile."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    engine = Path(args.engine).absolute()
    report = Path(args.report).absolute()
    if not engine.is_relative_to(ROOT / 'release') or not report.is_relative_to(ROOT / 'build') or report.exists():
        raise ValueError('fresh project-local release and build paths required')
    report.parent.mkdir(parents=True, exist_ok=True)
    temporary = report.parent / 'runtime-temp'
    temporary.mkdir(exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(temporary)
    import tempfile
    tempfile.tempdir = str(temporary)
    os.environ['LUMA_TEST_ENGINE'] = str(engine)
    from worker.test_frozen import FrozenEngineTests
    from worker.test_engine import request
    from worker.protocol import read_frame
    case = FrozenEngineTests('test_handshake_read_allowlist_and_caps')
    case.setUp()
    try:
        child = case.start()
        case.hello(child)
        case.send(child, request('manage.read', {'section': 'runtime', 'args': {}}, 'runtime-check'))
        reply = read_frame(child.stdout)
        assert reply['kind'] == 'result', 'runtime method unavailable'
        dependencies = reply['payload']
        passed = all(dependencies.get(name) is True for name in ('numpy', 'onnxruntime', 'tokenizers'))
        result = {'result': 'passed_frozen_runtime_selfcheck' if passed else 'failed_frozen_runtime_selfcheck',
                  'dependencies': dependencies,
                  'scope': 'fresh project-local test profile; no weights, images, real Data or network'}
        report.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result))
        if not passed:
            raise SystemExit(1)
    finally:
        case.tearDown()


if __name__ == '__main__':
    main()
