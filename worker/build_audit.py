"""Source and frozen-engine manifest; never starts or opens a library."""
import argparse
import hashlib
import json
import platform
import shutil
import sys
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inputs():
    files = list((ROOT/'worker').glob('*.py')) + list((ROOT/'backend').glob('*.py'))
    files += [ROOT/p for p in ['desktop/__init__.py','desktop/service.py','desktop/model_service.py','backend/model_catalog.json','scripts/luma-engine.spec','scripts/Build-Engine.ps1','requirements-engine-build.txt']]
    files += list((ROOT/'contracts/engine-v1').rglob('*'))
    files += list((ROOT/'winui/src/Luma.App/Assets/Stage2').glob('*.png'))
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files)) if p.is_file()}


def notices(directory):
    import PIL
    import PyInstaller
    sources = {'CPython-LICENSE.txt':Path(sys.base_prefix)/'LICENSE.txt',
               'Pillow-LICENSE.txt':Path(PIL.__path__[0]).parent/f'pillow-{version("pillow")}.dist-info/licenses/LICENSE',
               'PyInstaller-COPYING.txt':Path(PyInstaller.__path__[0]).parent/f'pyinstaller-{version("pyinstaller")}.dist-info/licenses/COPYING.txt'}
    target=directory/'ThirdPartyNotices'; target.mkdir(exist_ok=False)
    for name, source in sources.items(): shutil.copyfile(source,target/name)
    # Newly shipped inference dependencies and setuptools' vendored resources.
    from importlib.metadata import distribution, PackageNotFoundError
    for name in ('numpy','onnxruntime','tokenizers','setuptools','packaging','flatbuffers','protobuf','coloredlogs','humanfriendly'):
        try: dist=distribution(name)
        except PackageNotFoundError: continue
        for item in dist.files or ():
            if not any(word in Path(str(item)).name.casefold() for word in ('license','copying','notice')):continue
            source=Path(dist.locate_file(item))
            if not source.is_file() or source.stat().st_size>2*1024*1024:continue
            # Exact path under a named distribution, never a user library.
            relative=Path(str(item))
            if '..' in relative.parts:continue
            destination=target/name/relative;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)


def audit(directory, before, verify=False):
    from PyInstaller.archive.readers import CArchiveReader
    if inputs() != before: raise RuntimeError('engine_source_drift')
    archive = CArchiveReader(str(directory/'Luma.Engine.exe'))
    pyz_name = next(n for n in archive.toc if n.endswith('.pyz'))
    modules = sorted(archive.open_embedded_archive(pyz_name).toc)
    forbidden = ('pyside','shiboken','pyqt','tkinter','webview','webbrowser','fastapi','uvicorn','starlette','http.server','socketserver','launcher','desktop.main','desktop.gallery','desktop.viewer','backend.app','worker.test','worker.crash_parent','worker.demo_catalog')
    bad = [n for n in modules if any(n.lower().startswith(f) for f in forbidden)]
    if bad: raise RuntimeError('engine_forbidden_modules')
    payload = {}
    for p in sorted(directory.rglob('*')):
        if not p.is_file() or p.name == 'engine-build-info.json': continue
        if any(x in p.name.lower() for x in ('pyside','shiboken','qt6','webview','chrome')) or p.suffix.lower() in ('.db','.sqlite','.sqlite3','.safetensors','.ckpt','.html','.js'): raise RuntimeError('engine_forbidden_payload')
        payload[p.relative_to(directory).as_posix()] = {'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    value = {'stage':8,'version':'1.0.0','python':platform.python_version(),'pillow':version('pillow'),'pyinstaller':version('pyinstaller'),'qtFree':True,'webUI':False,'previewOnly':False,'sourceInputs':before,'modules':modules,'payload':payload}
    if verify:
        if json.loads((directory/'engine-build-info.json').read_text(encoding='utf-8')) != value: raise RuntimeError('engine_payload_drift')
    else: (directory/'engine-build-info.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
    return {'payload':len(payload),'modules':len(modules),'inputs':len(before)}


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--snapshot');parser.add_argument('--directory');parser.add_argument('--verify',action='store_true');args=parser.parse_args()
    if args.directory:
        directory=Path(args.directory)
        before=json.loads((directory/'engine-build-info.json').read_text(encoding='utf-8'))['sourceInputs'] if args.verify else json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
        if not args.verify: notices(directory)
        print(json.dumps(audit(directory,before,args.verify)))
    else: Path(args.snapshot).write_text(json.dumps(inputs(),indent=2),encoding='utf-8')
