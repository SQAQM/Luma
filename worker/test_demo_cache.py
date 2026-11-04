"""Synthetic fixture preparation budget and byte-compatibility, no user data."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw
from worker.demo_catalog import seed
from worker.ownership import prepare_preview


class DemoCacheTests(unittest.TestCase):
    def test_decodes_three_public_templates_once_and_preserves_all_outputs(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='luma-demo-cache-') as temporary:
            old_data = Path(temporary) / 'old'
            new_data = Path(temporary) / 'new'
            prepare_preview(new_data)
            old_data.mkdir()
            # Independent uncached rendering contract; no retained build needed in CI.
            expected=[]
            for index in range(131):
                name=('alpine.png','architecture.png','dunes.png')[index%3]
                suffix='.jpg' if index%4==1 else '.webp' if index%4==2 else '.png'
                target=old_data/f'Fixture-{index+1:03d}{suffix}'
                with Image.open(project/'winui/src/Luma.App/Assets/Stage2'/name) as source:
                    canvas=source.convert('RGB');canvas.thumbnail((480,320))
                    if index==0:canvas=canvas.resize((1600,1067),Image.Resampling.LANCZOS)
                    ImageDraw.Draw(canvas).rectangle((0,canvas.height-6,canvas.width,canvas.height),fill=(80+index,110,160))
                    canvas.save(target);canvas.close()
                expected.append(dict(name=target.name,sha256=hashlib.sha256(target.read_bytes()).hexdigest(),id=index+1 if index<130 else 1000001))
            original_open = Image.open
            with patch('worker.demo_catalog.Image.open', wraps=original_open) as opened:
                seed(new_data)
            self.assertEqual(opened.call_count, 3, 'bounded public template decodes')
            read = lambda directory: json.loads((directory / 'synthetic-manifest.json').read_text(encoding='utf-8'))
            after = read(new_data)['images']
            self.assertEqual(expected, after, '131 names, IDs and actual output SHA unchanged')
            self.assertEqual(len(after), 131)
            self.assertTrue(all(str(call.args[0]).startswith(str(project / 'winui/src/Luma.App/Assets/Stage2')) for call in opened.call_args_list))
            print(json.dumps(dict(outputs=len(after),templateDecodes=opened.call_count)))
