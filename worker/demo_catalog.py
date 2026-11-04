"""Explicit preparation of a fresh synthetic catalog. Never scans a directory."""
import hashlib
import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw
from worker.ownership import Ownership, EngineStartError, no_reparse


def seed(directory):
    directory=Path(directory)
    with Ownership(directory) as owner:
        owner.preflight()
        if (directory/'library.sqlite3').exists() or (directory/'demo-images').exists():raise EngineStartError('preview_not_fresh')
        from backend.store import Store
        store=Store(directory);images=directory/'demo-images';images.mkdir()
        with store.connect() as db:db.execute("UPDATE content_tags SET name='建筑',description='Synthetic architecture fixture' WHERE id=2")
        root=store.add_root(images);manifest=[]
        artroot=Path(sys._MEIPASS)/'demo-art' if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]/'winui/src/Luma.App/Assets/Stage2'
        names=['alpine.png','architecture.png','dunes.png']
        templates=[]
        try:
            for name in names:
                with Image.open(artroot/name) as source:
                    template=source.convert('RGB')
                templates.append(template);template.thumbnail((480,320))
            for index in range(131):
                suffix='.jpg' if index%4==1 else '.webp' if index%4==2 else '.png'
                path=images/f'Fixture-{index+1:03d}{suffix}';no_reparse(path)
                canvas=templates[index%3].copy()
                try:
                    if index==0:
                        larger=canvas.resize((1600,1067),Image.Resampling.LANCZOS);canvas.close();canvas=larger
                    ImageDraw.Draw(canvas).rectangle((0,canvas.height-6,canvas.width,canvas.height),fill=(80+index,110,160))
                    canvas.save(path)
                finally:canvas.close()
                if index==130:
                    with store.connect() as db:db.execute("INSERT INTO images(id,path,name,size,mtime_ns,created_at,indexed_at) VALUES(1000000,'fixture-reserved','fixture-reserved',0,0,0,0)")
                image_id=store.upsert(path,root,path.stat(),{'width':canvas.width,'height':canvas.height,'format':suffix[1:].upper(),'family':'Stable Diffusion' if index%2==0 else 'Unknown','model':'合成模型 SDXL' if index%2==0 else '未知（合成）','prompt':'合成提示词 雪😀 '*3000 if index==0 else f'Synthetic prompt {index+1}','negative_prompt':'合成负面 😀 '*1000 if index==0 else '', 'artist':'合成画师 A' if index%2==0 else '合成画师 B','tags':['synthetic-prompt-token']})
                if index==130:
                    with store.connect() as db:db.execute('DELETE FROM images WHERE id=1000000')
                tag_id=2 if index%3==1 else 1
                with store.connect() as db:
                    db.execute("INSERT INTO image_content_overrides(image_id,tag_id,action,updated_at) VALUES(?,?,'include',0)",(image_id,tag_id))
                manifest.append({'name':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'id':image_id})
        finally:
            for template in templates:template.close()
        (directory/'synthetic-manifest.json').write_text(json.dumps({'syntheticOnly':True,'images':manifest}),encoding='utf-8')
        with store.connect() as db:db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
