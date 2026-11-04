"""Unbuffered pipe reads that observe output failure without killing file work."""
import os


class PipeInput:
    def __init__(self, stream, closing):
        self.stream, self.closing = stream, closing
        self.fd = stream.fileno()
        if os.name == 'nt':
            import ctypes
            import msvcrt
            self.ctypes = ctypes
            self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            self.peek = self.kernel.PeekNamedPipe
            self.peek.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
            self.peek.restype = ctypes.c_int
            self.handle = msvcrt.get_osfhandle(self.fd)

    def read(self, length):
        while not self.closing.is_set():
            if os.name == 'nt':
                available = self.ctypes.c_uint32()
                if not self.peek(self.handle, None, 0, None, self.ctypes.byref(available), None):
                    if self.ctypes.get_last_error() in (109, 232): return b''
                    raise OSError('pipe_unavailable')
                if available.value: return self.stream.read(min(length, available.value))
                self.closing.wait(.02)
            else:
                import select
                if select.select([self.fd], [], [], .02)[0]: return self.stream.read(length)
        return b''
