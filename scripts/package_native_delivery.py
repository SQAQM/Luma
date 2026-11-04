"""Freeze an audited native bundle into a per-user installer and portable ZIP."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def application_version(info):
    label=info.get('version','')
    version=label.split('-')[0]
    if not re.fullmatch(r'(?:0\.(?:8|9)\.\d+|[1-9]\d*\.\d+\.\d+)',version) or info.get('fileVersion')!=version+'.0':
        raise ValueError('Invalid application release version')
    if not version.startswith('0.') and label!=version:
        raise ValueError('Stable application version required')
    return version

def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024):value.update(block)
    return value.hexdigest()

def relative_file(name):
    p=PurePosixPath(name)
    reserved={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
    if not name or p.is_absolute() or str(p)!=name or '\\' in name or any(part in ('.','..') or part.endswith(('.', ' ')) or part.split('.')[0].upper() in reserved for part in p.parts) or re.search(r'[\x00-\x1f<>:"|?*$]',name):
        raise ValueError('Unsafe package-relative path')
    if name.lower() in ('uninstall.exe','luma-install.identity'):raise ValueError('Installer-reserved name')
    return name

def literal(value):
    return str(value).replace('$','$$').replace('"','$\\"')

def file_macros(files):
    directories={''}
    for name in files:
        relative_file(name)
        parent=PurePosixPath(name).parent
        while str(parent)!='.':directories.add(str(parent));parent=parent.parent
    result=['!macro CheckPayloadDirectories PREFIX']
    for directory in sorted(directories):
        path='$INSTDIR'+('\\'+directory.replace('/','\\') if directory else '')
        result += [f'  Push "{path}"','  Call ${PREFIX}CheckDirectory']
    result+=['!macroend','','!macro CheckPayloadFiles PREFIX']
    for name in sorted(set(files)|{'luma-install.identity','Uninstall.exe'}):
        result += ['  Push "$INSTDIR\\'+name.replace('/','\\')+'"','  Call ${PREFIX}CheckFile']
    result+=['!macroend','','!macro InstallPayload']
    last=None
    for name,source in sorted(files.items()):
        parent=str(PurePosixPath(name).parent)
        if parent!=last:
            path='$INSTDIR'+('\\'+parent.replace('/','\\') if parent!='.' else '')
            result += ['  ClearErrors',f'  SetOutPath "{path}"','  IfErrors 0 +2','  Call InstallFailed'];last=parent
        result += ['  Push "$INSTDIR\\'+name.replace('/','\\')+'"','  Call PreparePayloadFile','  ClearErrors']
        result.append(f'  File "/oname={PurePosixPath(name).name}" "{literal(source)}"')
        result += ['  IfErrors 0 +2','  Call InstallFailed']
    result+=['!macroend','','!macro RemovePayload']
    for name in sorted(files):result += ['  Push "$INSTDIR\\'+name.replace('/','\\')+'"','  Call un.DeletePayloadFile']
    for directory in sorted(directories,key=lambda x:(x.count('/'),len(x)),reverse=True):
        if directory:result.append('  RMDir "$INSTDIR\\'+directory.replace('/','\\')+'"')
    result+=['!macroend','']
    return '\n'.join(result)

def owned_path(path,base):
    sys.path.insert(0,str(ROOT))
    from worker.ownership import no_reparse
    value=Path(path).absolute()
    no_reparse(value)
    value=value.resolve()
    if value==base or not value.is_relative_to(base):raise ValueError('Path must stay in dedicated project area')
    return value

def validate_installer_version(pe,version,file_version):
    fixed=pe.VS_FIXEDFILEINFO[0]
    actual=(fixed.FileVersionMS>>16,fixed.FileVersionMS&65535,fixed.FileVersionLS>>16,fixed.FileVersionLS&65535)
    if actual!=tuple(map(int,file_version.split('.'))):raise ValueError('Installer numeric version mismatch')
    labels=[]
    for group in pe.FileInfo:
        for entry in group:
            for table in getattr(entry,'StringTable',[]):
                if b'FileVersion' in table.entries:labels.append(table.entries[b'FileVersion'].decode('utf-8'))
    if labels!=[version]:raise ValueError('Installer application label mismatch')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',required=True)
    parser.add_argument('--compiler',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    bundle=owned_path(args.bundle,ROOT/'release')
    output=owned_path(args.output,ROOT/'release')
    if output.exists():raise ValueError('Fresh output required')
    info=json.loads((bundle/'build-info.json').read_text(encoding='utf-8-sig'))
    version=application_version(info)
    if not info['dotnetSelfContained'] or not info['windowsAppSdkSelfContained'] or info['webFrontend']:raise ValueError('Native self-contained bundle required')
    actual=json.loads(subprocess.check_output(['pwsh','-NoProfile','-File',str(ROOT/'winui/Get-Stage1Inputs.ps1')],cwd=ROOT,encoding='utf-8-sig'))
    if actual!=info['sourceInputs']:raise ValueError('Source drift; rebuild before delivery')
    for name,item in info['payload'].items():
        relative_file(name)
        if digest(bundle/name)!=item['sha256']:raise ValueError('Payload drift')
    files={p.relative_to(bundle).as_posix():p for p in bundle.rglob('*') if p.is_file()}
    if set(files)!=set(info['payload'])|{'build-info.json'}:raise ValueError('Unexpected bundle file')
    compiler=Path(args.compiler).resolve()
    toolchain=json.loads((ROOT/'packaging/nsis-toolchain.json').read_text(encoding='utf-8'))
    if digest(compiler)!=toolchain['compiler_sha256']:raise ValueError('Compiler hash differs from pinned toolchain')
    compiler_version=subprocess.check_output([str(compiler),'/VERSION'],encoding='utf-8').strip()
    if compiler_version!='v3.13':raise ValueError('Pinned NSIS3.13 required')
    output.mkdir()
    work=ROOT/'build/winui-stage8-release'/('delivery-'+output.name)
    if work.exists():raise ValueError('Fresh package work required')
    snapshot=work/'Luma';snapshot.mkdir(parents=True)
    hashes={}
    for name,source in files.items():
        destination=snapshot/name;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination);hashes[name]=digest(source)
        if digest(destination)!=hashes[name]:raise ValueError('Snapshot changed during copy')
    header=work/'payload.nsh'
    header.write_text(file_macros({p:str(snapshot/p) for p in hashes}),encoding='utf-8-sig')
    setup=output/f'Luma-{version}-Setup-x64.exe'
    command=[str(compiler),'/V3','/DLUMA_VERSION='+version,'/DLUMA_FILE_VERSION='+info['fileVersion'],
             '/DGENERATED_INPUT='+str(header),'/DLUMA_BUNDLE='+str(snapshot),
             '/DLUMA_OUTPUT='+str(setup),'/DLUMA_SIZE_KIB='+str(sum(p.stat().st_size for p in files.values())//1024),str(ROOT/'packaging/luma.nsi')]
    result=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (work/'nsis-compile.log').write_bytes(result.stdout)
    if result.returncode:raise RuntimeError('NSIS compilation failed; see package work log')
    import pefile
    import xml.etree.ElementTree as ET
    pe=pefile.PE(str(setup))
    validate_installer_version(pe,version,info['fileVersion'])
    levels=set()
    for kind in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if kind.id==24:
            for name in kind.directory.entries:
                for language in name.directory.entries:
                    item=language.data.struct
                    tree=ET.fromstring(pe.get_data(item.OffsetToData,item.Size))
                    levels.update(e.attrib.get('level') for e in tree.iter() if e.tag.endswith('requestedExecutionLevel'))
    pe.close()
    if levels!={'asInvoker'}:raise ValueError('Installer manifest must not require elevation')
    portable=output/f'Luma-{version}-Portable-x64.zip'
    with zipfile.ZipFile(portable,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name in sorted(hashes):archive.write(snapshot/name,'Luma/'+name)
    with zipfile.ZipFile(portable) as archive:
        if archive.testzip() is not None:raise ValueError('ZIP CRC failed')
        for name,expected in hashes.items():
            with archive.open('Luma/'+name) as stream:
                value=hashlib.file_digest(stream,'sha256').hexdigest()
            if value!=expected:raise ValueError('ZIP entry mismatch')
    for name,expected in hashes.items():
        if digest(snapshot/name)!=expected or digest(bundle/name)!=expected:raise ValueError('Package source changed')
    report=dict(version=version,bundle=str(bundle.relative_to(ROOT)),files=len(hashes),fileHashes=hashes,
                setup=dict(requestedExecutionLevel='asInvoker',name=setup.name,bytes=setup.stat().st_size,sha256=digest(setup)),
                portable=dict(name=portable.name,bytes=portable.stat().st_size,sha256=digest(portable),crcAndEntriesVerified=True),
                compiler=dict(version=compiler_version,sha256=digest(compiler)),signed=False,scope='per-user native desktop release; verification recorded separately')
    (output/'delivery-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'README.txt').write_text(f'Luma {version}\nWindows x64\n程序：Luma.exe\n数据：程序旁 Data\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('version','files','setup','portable')},ensure_ascii=False))

if __name__=='__main__':main()
