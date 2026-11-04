"""Qt-free production worker; sample assets and seed module are not shipped."""
from pathlib import Path
from importlib.util import find_spec

root = Path(SPECPATH).parent
vendor = Path(find_spec('setuptools').origin).parent / '_vendor'
a = Analysis(
    [str(root / 'worker' / 'main.py')], pathex=[str(root), str(vendor)], binaries=[],
    datas=[(str(root / 'backend' / 'model_catalog.json'), 'backend'),
           (str(vendor / 'jaraco' / 'text' / 'Lorem ipsum.txt'), 'jaraco/text')],
    hiddenimports=['PIL.PngImagePlugin', 'PIL.JpegImagePlugin', 'PIL.WebPImagePlugin',
                   'PIL.TiffImagePlugin', 'PIL.BmpImagePlugin', 'PIL.IcoImagePlugin',
                   'onnxruntime', 'tokenizers', 'numpy', 'numpy._core._exceptions',
                   'jaraco.text', 'jaraco.context',
                   'jaraco.functools', 'platformdirs', 'backports.tarfile', 'more_itertools'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PySide6', 'shiboken6', 'PyQt5', 'PyQt6', 'tkinter', 'webview', 'webbrowser',
              'fastapi', 'uvicorn', 'starlette', 'httpx', 'http.server', 'socketserver',
              'backend.app', 'desktop.main', 'desktop.gallery', 'desktop.viewer', 'launcher',
              'worker.demo_catalog', 'worker.test_engine', 'worker.crash_parent_fixture',
              'huggingface_hub', 'torch', 'transformers', 'onnx', 'onnxscript',
              'onnxruntime.experimental', 'onnxruntime.training', 'matplotlib', 'pandas',
              'scipy', 'sympy', 'sklearn'],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Luma.Engine',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True,
          icon=str(root / 'public' / 'luma.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='engine')
