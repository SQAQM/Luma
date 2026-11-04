"""Run against an explicitly supplied frozen binary, never a user catalog."""
import ctypes
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from worker import test_engine as base
ROOT = base.ROOT
from worker.ownership import Ownership

ENGINE = str(Path(os.environ['LUMA_TEST_ENGINE']).resolve())


class FrozenEngineTests(base.EngineTests):
    def start(self):
        child = subprocess.Popen([ENGINE, '--data-dir', str(self.data)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.children.append(child); return child

    def test_old_launcher_mutex_both_directions(self):
        from launcher import InstanceLock
        old=InstanceLock(self.data); self.assertTrue(old.acquire())
        try:
            child=self.start(); self.assertEqual(child.wait(timeout=10),73)
            self.assertEqual(child.stderr.read().strip(),b'engine_busy'); self.assertFalse((self.data/'library.sqlite3').exists())
        finally: old.close()
        child=self.start();self.hello(child)
        old=InstanceLock(self.data)
        try: self.assertFalse(old.acquire())
        finally: old.close()

    def test_real_parent_abrupt_exit_closes_pipes_and_releases_lock(self):
        result=Path(self.temp.name)/'child-pid.txt'
        parent=subprocess.Popen([sys.executable,'-m','worker.crash_parent_fixture',ENGINE,str(self.data),str(result)],cwd=ROOT)
        self.assertEqual(parent.wait(timeout=10),37)
        pid=int(result.read_text(encoding='ascii'))
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x100000,False,pid)
        if handle:
            try: self.assertEqual(kernel.WaitForSingleObject(handle,10000),0)
            finally: kernel.CloseHandle(handle)
        with Ownership(self.data): pass

    def test_server_admission_and_refill(self):
        from worker.test_engine import request
        from worker.protocol import read_frame
        child=self.start();self.hello(child)
        for i in range(6): self.send(child,request('diagnostics.wait',{'milliseconds':700},'capacity'+str(i)))
        replies=[read_frame(child.stdout) for _ in range(6)]
        self.assertEqual(sum(r['payload'].get('code')=='busy' for r in replies),2)
        self.send(child,request('diagnostics.echo',{'refill':True},'refill'))
        self.assertTrue(read_frame(child.stdout)['payload']['refill'])


if __name__ == '__main__': unittest.main()
