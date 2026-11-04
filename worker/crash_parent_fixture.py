"""Test-only parent: intentionally exits without disposing the child's pipe handles."""
import os
import subprocess
import sys
import time
from pathlib import Path
from worker.protocol import encode, read_frame


def run(executable, data, result):
    child = subprocess.Popen([executable, '--data-dir', data], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    def send(method, payload, rid):
        child.stdin.write(encode(dict(version=1, requestId=rid, generation=0, identity='', method=method, payload=payload))); child.stdin.flush()
    send('engine.hello', {'protocol':1}, 'hello')
    assert read_frame(child.stdout)['kind'] == 'result'
    send('diagnostics.wait', {'milliseconds':1000,'safeMilliseconds':250}, 'safe')
    time.sleep(.08)
    Path(result).write_text(str(child.pid), encoding='ascii')
    os._exit(37)


if __name__ == '__main__': run(*sys.argv[1:])
