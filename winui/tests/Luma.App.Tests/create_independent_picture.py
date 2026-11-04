"""Create one explicitly synthetic PNG, no source image or directory reads."""
import sys
from pathlib import Path
from PIL import Image, PngImagePlugin

destination = Path(sys.argv[1])
if not destination.is_dir() or any(destination.iterdir()):
    raise ValueError('fresh synthetic destination required')
info = PngImagePlugin.PngInfo()
info.add_text('parameters', 'synthetic fixture only\nSteps: 12, Sampler: Euler, Model: standalone-fixture, Seed: 1')
Image.new('RGB', (32, 32), (64, 96, 144)).save(destination / 'synthetic-associated.png', pnginfo=info)
