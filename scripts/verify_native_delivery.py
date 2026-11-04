"""Verify immutable native payload, published ZIP and evidence; never opens a user library.

This verifier lives with source, accepts manifests rather than hardcoded release paths,
and does not edit build-info. Historical source snapshots may be supplied via --source-root.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024):digest.update(block)
    return digest.hexdigest()


def relative(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '\\' in name or ':' in name or str(path)!=name or '..' in path.parts:
        raise ValueError('unsafe manifest path')
    return name


def verify(bundle,delivery,source,evidence):
    info=json.loads((bundle/'build-info.json').read_text(encoding='utf-8-sig'))
    for name,checksum in info['sourceInputs'].items():
        assert sha(source/relative(name))==checksum, f'source drift: {name}'
    expected=set(info['payload'])|{'build-info.json'}
    actual={path.relative_to(bundle).as_posix() for path in bundle.rglob('*') if path.is_file()}
    assert actual==expected,'unexpected or missing bundle files'
    hashes={}
    for name in sorted(expected):
        checksum=sha(bundle/relative(name));hashes[name]=checksum
        if name in info['payload']:
            assert checksum==info['payload'][name]['sha256']
            assert (bundle/name).stat().st_size==info['payload'][name]['bytes']
    engine=json.loads((bundle/'engine/engine-build-info.json').read_text(encoding='utf-8'))
    for name,checksum in engine['sourceInputs'].items():assert sha(source/relative(name))==checksum
    for name,item in engine['payload'].items():assert sha(bundle/'engine'/relative(name))==item['sha256']
    manifest=json.loads((delivery/'delivery-manifest.json').read_text(encoding='utf-8'))
    assert manifest['fileHashes']==hashes,'package payload differs from audited native bundle'
    for kind in ('setup','portable'):
        item=manifest[kind];path=delivery/relative(item['name'])
        assert path.stat().st_size==item['bytes'] and sha(path)==item['sha256']
    with zipfile.ZipFile(delivery/manifest['portable']['name']) as archive:
        assert archive.testzip() is None
        members={item.filename:item for item in archive.infolist() if not item.is_dir()}
        assert set(members)=={'Luma/'+name for name in expected}
        for name,checksum in hashes.items():
            digest=hashlib.sha256()
            with archive.open(members['Luma/'+name]) as stream:
                while block:=stream.read(1024*1024):digest.update(block)
            assert digest.hexdigest()==checksum
    reports={}
    for name in evidence:
        path=source/relative(name)
        value=json.loads(path.read_text(encoding='utf-8-sig'))
        assert value.get('result','').startswith('passed'),f'evidence not passing: {name}'
        reports[name]={'sha256':sha(path),'result':value['result'],'checks':value.get('checks',value.get('count'))}
    return {'result':'passed_local_native_candidate_only','version':info['version'],'sourceInputs':len(info['sourceInputs']),'engineInputs':len(engine['sourceInputs']),'payloadFiles':len(hashes),'engineModules':len(engine['modules']),'setup':manifest['setup'],'portable':manifest['portable'],'evidence':reports,'verifierSha256':sha(Path(__file__)),'scope':'immutable payload and supplied local synthetic evidence; no production-capacity or real-library claim'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bundle',required=True);parser.add_argument('--delivery',required=True)
    parser.add_argument('--source-root',default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--evidence',action='append',default=[]);parser.add_argument('--report',required=True)
    args=parser.parse_args()
    result=verify(Path(args.bundle).absolute(),Path(args.delivery).absolute(),Path(args.source_root).absolute(),args.evidence)
    path=Path(args.report).absolute()
    if path.exists():raise ValueError('fresh verification report required')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('NATIVE_DELIVERY_VERIFIED',result['payloadFiles'],result['sourceInputs'])


if __name__=='__main__':main()
