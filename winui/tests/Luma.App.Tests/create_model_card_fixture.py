"""Owned native-test models and cached public-response stand-ins; no network."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
from PIL import Image, ImageDraw

root=Path(sys.argv[2]).resolve()
marker=root/'.model-card-test'
models=root/'fixtures'/'models'
if sys.argv[1]=='prepare':
    if marker.exists(): raise SystemExit('fresh owner required')
    marker.write_text('synthetic-model-cards-v1',encoding='utf-8')
    models.mkdir(parents=True)
    (models/'standalone-fixture.safetensors').write_text('inert synthetic model, never deserialize',encoding='utf-8')
    for index in range(65):
        (models/f'model-{index:03}.safetensors').write_text(f'inert synthetic fixture {index}',encoding='utf-8')
elif sys.argv[1]=='seed':
    if marker.read_text(encoding='utf-8')!='synthetic-model-cards-v1': raise SystemExit('owned fixture required')
    data=root/'isolated-data'
    pointer=json.loads((data/'winui-library-v1.json').read_text(encoding='utf-8-sig'))['directory']
    if not pointer.startswith('engine-probe-') or '/' in pointer or '\\' in pointer: raise SystemExit('invalid test slot')
    profile=data/pointer
    if json.loads((profile/'.luma-engine-preview.json').read_text(encoding='utf-8-sig'))['purpose']!='luma-stage3-preview': raise SystemExit('invalid owner')
    coverdir=profile/'model-covers';coverdir.mkdir(exist_ok=True)
    evidence={}
    with sqlite3.connect(profile/'library.sqlite3',timeout=10) as db:
        rows=db.execute('SELECT id,path,name FROM luma_model_files ORDER BY id').fetchall()
        if len(rows)!=66: raise SystemExit('expected indexed fixture inventory')
        for ident,path,name in rows:
            weight=Path(path).resolve()
            if weight.parent!=models or weight.is_symlink(): raise SystemExit('outside synthetic model directory')
            digest=hashlib.sha256(weight.read_bytes()).hexdigest()
            evidence[name]={'id':ident,'sha256':digest}
            index=int(name[6:9]) if name.startswith('model-') else -1
            if index==64: continue
            kind='LoRA' if index<30 or 50<=index<60 else 'Checkpoint'
            family='Krea 2' if index<30 or index>=60 else 'Stable Diffusion'
            record={'model_id':10000+ident,'version_id':20000+ident,'model_name':('合成模型 '+name.removesuffix('.safetensors')),'version_name':'合成缓存 v1','type':kind,'model_type':kind,'family':family,'base_model':family,'files':[{'sha256':digest,'type':'Model','resource_type':kind}],'description':'仅用于独立原生测试的合成资料，无网络请求。','creator':'Synthetic Fixture'}
            db.execute('INSERT OR REPLACE INTO luma_model_versions VALUES(?,?,?)',(20000+ident,json.dumps(record,ensure_ascii=False),0))
            db.execute("UPDATE luma_model_files SET sha256=?,version_id=?,status='synced' WHERE id=?",(digest,20000+ident,ident))
            if index in (-1,0):
                image=Image.new('RGB',(320,240),(28,43,60));draw=ImageDraw.Draw(image)
                for y in range(240):
                    draw.line((0,y,320,y),fill=(30+y//6,53+y//4,83+y//3))
                draw.ellipse((170,25,252,107),fill=(236,209,160))
                draw.polygon([(0,240),(96,91),(220,240)],fill=(73,139,143))
                draw.polygon([(106,240),(250,133),(320,210),(320,240)],fill=(38,89,113))
                image.save(coverdir/(digest+'.cover'),format='PNG')
            elif index==1:
                (coverdir/(digest+'.cover')).write_bytes(b'intentionally invalid synthetic cover')
    (root/'fixture-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False),encoding='utf-8')
else: raise SystemExit('unknown fixture operation')
