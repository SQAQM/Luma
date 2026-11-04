"""Private stdio executable. Import the facade only after owning its data lock."""
import argparse
import concurrent.futures
import io
import queue
import sys
import threading

from worker.protocol import FrameError, encode, read_frame, validate_request
from worker.ownership import EngineStartError, Ownership
from worker.dispatch import DispatchError, dispatch, fields, hello
from worker.shutdown import Shutdown
from worker.pipe_input import PipeInput


class SilentText(io.TextIOBase):
    def write(self, text): return len(text)
    def flush(self): pass


class QuietParser(argparse.ArgumentParser):
    def error(self, message): raise EngineStartError('invalid_arguments')


def reply(request, value, error=False):
    return {key: request[key] for key in ('version', 'requestId', 'generation', 'identity')} | {'kind': 'error' if error else 'result', 'payload': value}


class Writer:
    def __init__(self, stream, shutdown):
        self.stream = stream
        self.shutdown = shutdown
        self.failed = threading.Event()
        self.queue = queue.Queue(2)
        self.thread = threading.Thread(target=self.run, name='engine-stdout', daemon=True)
        self.thread.start()

    def send(self, value):
        while not self.failed.is_set():
            try: self.queue.put(value, timeout=.1); return
            except queue.Full: continue

    def run(self):
        try:
            while True:
                value = self.queue.get()
                if value is None: return
                try: data = encode(value)
                except FrameError as error:
                    data = encode({**value, 'kind': 'error', 'payload': {'code': error.code}})
                self.stream.write(data); self.stream.flush()
        except (OSError, ValueError):
            self.failed.set(); self.shutdown.begin()

    def close(self):
        self.send(None)
        self.thread.join()


def serve(service, input_stream, output_stream):
    shutdown = Shutdown(); writer = Writer(output_stream, shutdown)
    input_stream = PipeInput(input_stream, shutdown.closing)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='engine-call')
    greeted = False; stopping = None; failure = None

    def perform(request, event):
        try:
            value = dispatch(service, request['method'], request['payload'], event)
            if event.is_set(): raise DispatchError('cancelled')
            writer.send(reply(request, value))
        except Exception as error:
            code = getattr(error, 'code', 'operation_failed')
            if code not in ('invalid_request', 'method_not_allowed', 'cancelled', 'asset_boundary', 'not_found', 'busy', 'recovering', 'closed','stale_identity','stale_cursor','unsupported','conflict','not_ready','invalid_token','model_hash_missing','stale_review','confirmation_required','recovery_required','duplicate_recovery'):
                code = 'operation_failed'
            writer.send(reply(request, {'code': code}, True))
        finally:
            with shutdown.lock: shutdown.active.pop(request['requestId'], None)

    try:
        while not shutdown.closing.is_set():
            message = read_frame(input_stream)
            if message is None: break
            request = validate_request(message)
            method, payload, rid = request['method'], request['payload'], request['requestId']
            with shutdown.lock:
                if rid in shutdown.active: raise FrameError('protocol_error')
            if not greeted:
                if method != 'engine.hello' or set(payload) != {'protocol'} or type(payload['protocol']) is not int or payload['protocol'] != 1: raise FrameError('protocol_mismatch')
                greeted = True; writer.send(reply(request, hello())); continue
            if method == 'engine.hello':
                writer.send(reply(request, {'code': 'invalid_request'}, True)); continue
            if method == 'engine.shutdown':
                fields(payload, ()); stopping = request; break
            if method == 'request.cancel':
                fields(payload, ('requestId',))
                target = payload.get('requestId')
                if not isinstance(target, str) or len(target) > 64: raise FrameError('invalid_request')
                with shutdown.lock:
                    event = shutdown.active.get(target)
                    if event: event.set()
                writer.send(reply(request, {'cancelRequested': event is not None})); continue
            with shutdown.lock:
                if len(shutdown.active) >= 4:
                    writer.send(reply(request, {'code': 'busy'}, True)); continue
                event = threading.Event(); shutdown.active[rid] = event
            executor.submit(perform, request, event)
    except (FrameError, DispatchError) as error:
        failure = error.code
    except (OSError, ValueError):
        failure = 'protocol_error'
    finally:
        shutdown.finish(executor, service)
        if stopping is not None: writer.send(reply(stopping, {'closed': True}))
        writer.close()
    return failure


def main():
    parser = QuietParser(add_help=False)
    parser.add_argument('--data-dir', required=True)
    if not getattr(sys, 'frozen', False):
        parser.add_argument('--seed-demo', action='store_true')
    else:
        parser.set_defaults(seed_demo=False)
    parser.add_argument('--seed-file')
    try: args = parser.parse_args()
    except (SystemExit, EngineStartError):
        sys.stderr.write('invalid_arguments\n'); return 64
    raw_input, raw_output, raw_error = sys.stdin.buffer.raw, sys.stdout.buffer, sys.stderr
    sys.stdout = SilentText(); sys.stderr = SilentText()
    try:
        if args.seed_file:
            if args.seed_demo:raise EngineStartError('invalid_arguments')
            from worker.single_file import prepare_single
            prepare_single(args.data_dir,args.seed_file)
            return 0
        if args.seed_demo:
            from worker.demo_catalog import seed
            seed(args.data_dir)
            return 0
        with Ownership(args.data_dir) as owner:
            owner.preflight(allow_recovery=True)
            from desktop.service import LibraryService
            service = LibraryService(owner.directory,automatic_recovery=False)
            service.single_view=(owner.directory/'.luma-single-file.json').exists()
            from worker.file_ops import get
            get(service)
            failure = serve(service, raw_input, raw_output)
        if failure:
            raw_error.write(failure+'\n'); raw_error.flush(); return 65
        return 0
    except EngineStartError as error:
        raw_error.write(error.code+'\n'); raw_error.flush()
        return 73 if error.code == 'engine_busy' else 74
    except BaseException:
        raw_error.write('engine_failed\n'); raw_error.flush(); return 70


if __name__ == '__main__':
    raise SystemExit(main())
