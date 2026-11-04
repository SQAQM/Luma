"""Read-only image handle. Hardlinks are safe to read, never to mutate here."""
import ctypes, os
from pathlib import Path
from backend.duplicates import VerifiedFile, _fingerprint, _windows_change
from backend.organizer import signature, check_directory_chain


class ReadImage(VerifiedFile):
    def __init__(self,path):super().__init__(path,mutable=False)
    def __enter__(self):
        if not self.path.is_absolute() or str(self.path).startswith('\\\\'):raise ValueError('unsupported')
        before=signature(self.path)
        if os.name!='nt':return super().__enter__()  # Desktop target is Windows.
        import msvcrt
        from ctypes import wintypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateFileW.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
        api.CreateFileW.restype=wintypes.HANDLE;api.CloseHandle.argtypes=(wintypes.HANDLE,)
        try:
            current=self.path.parent
            while current!=current.parent:
                handle=api.CreateFileW(str(current),0x80,1,None,3,0x02200000,None)
                if handle==ctypes.c_void_p(-1).value:raise OSError('directory_busy')
                self.directories.append((api,handle));current=current.parent
            check_directory_chain(self.path.parent)
            handle=api.CreateFileW(str(self.path),0x80000000,1,None,3,0x00200000,None)
            if handle==ctypes.c_void_p(-1).value:raise OSError('file_busy')
            try:descriptor=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
            except BaseException:api.CloseHandle(handle);raise
            self.file=os.fdopen(descriptor,'rb');self.handle,self.api=handle,api
            self.info=os.fstat(self.file.fileno());self.change_ns=_windows_change(handle)
            if _fingerprint(before)!=_fingerprint(self.info):raise ValueError('stale_identity')
            self.verify();return self
        except BaseException:
            if self.file:self.file.close()
            self._close_directories();raise
