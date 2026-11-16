"""Owned GIF/WebP/TIFF fixtures for the native media regression."""
from pathlib import Path
import sys
from PIL import Image

workspace=Path(__file__).resolve().parents[3]
target=Path(sys.argv[1]).resolve()
if not target.is_relative_to(workspace/'build') or target.exists():
    raise ValueError('Fresh media fixture inside project build required')
target.mkdir(parents=True)
frames=[Image.new('RGB',(600,400),color) for color in ('#d64850','#38a278','#428bd0')]
pages=[Image.new('RGB',(1200,800),'#496f99'),Image.new('RGB',(800,800),'#49a68a')]
try:
    for name,fmt in (('animated.gif','GIF'),('animated.webp','WEBP')):
        frames[0].save(target/name,format=fmt,save_all=True,append_images=frames[1:],duration=[80,140,200],loop=0,lossless=True)
    pages[0].save(target/'pages.tiff',format='TIFF',save_all=True,append_images=pages[1:],compression='tiff_lzw')
finally:
    for frame in (*frames,*pages):frame.close()
(target/'generated-media-fixture.txt').write_text('generated GIF3 WebP3 TIFF2; no user data',encoding='utf-8')
