"""Bounded, same-name model descriptions; never evidence of verified weight bytes."""
import json
from pathlib import Path
from .civitai import _BASES, _plain_text, _text, normalize_trigger_words
from .duplicates import VerifiedFile
from .organizer import check_directory_chain
from .model_registry import FAMILIES

MAX_BYTES = 1024 * 1024


def _fields(value):
    return {key.casefold(): item for key, item in value.items() if isinstance(key, str)} if isinstance(value, dict) else {}


def _number(value):
    return value if type(value) is int and 0 < value <= 2**63-1 else None


def parse(value, model_path, format_name):
    info = _fields(value)
    if not info: return {}
    model = _fields(info.get('model'))
    connected = format_name == 'matrix'
    name = _text(info.get('modelname', model.get('name')))
    if not name: return {}
    base = _text(info.get('basemodel'), 100)
    family = _BASES.get(base, ('Unknown', ''))[0]
    role = _text(info.get('modeltype', model.get('type', info.get('type'))), 100)
    # A version may bundle an auxiliary file such as VAE. Prefer its explicit
    # file role when the local name identifies exactly that entry.
    files = info.get('files', [])
    matches = [f for f in files[:200] if isinstance(f, dict) and isinstance(f.get('name'), str)
               and f['name'].casefold() == Path(model_path).name.casefold()] if isinstance(files, list) else []
    if len(matches) == 1 and isinstance(matches[0].get('type'), str) and matches[0]['type'].casefold() != 'model':
        role = _text(matches[0]['type'], 100)
    elif len(matches) > 1:
        role = 'Unknown'
    role = {'lora':'LORA', 'locon':'LoCon', 'checkpoint':'Checkpoint', 'vae':'VAE',
            'controlnet':'Controlnet', 'textualinversion':'TextualInversion'}.get(role.casefold(), role or 'Unknown')
    model_id = _number(info.get('modelid'))
    version_id = _number(info.get('versionid', info.get('modelversionid', info.get('id'))))
    source = info.get('source', 'Civitai')
    is_civitai = source == 'Civitai' or (connected and type(source) is int and source == 0)
    source_url = f'https://civitai.com/models/{model_id}?modelVersionId={version_id}' if model_id and version_id and is_civitai else ''
    return {
        'model_name':name, 'version_name':_text(info.get('versionname' if connected else 'modelversionname', info.get('name') if not connected else '')),
        'type':role, 'base_model':base, 'family':family if family in FAMILIES else 'Unknown',
        'description':_plain_text(info.get('modeldescription', model.get('description', info.get('description') if connected else ''))),
        'version_description':_plain_text(info.get('versiondescription', info.get('description') if not connected else '')),
        'trigger_words':normalize_trigger_words(info.get('trainedwords', [])),
        'creator':_text(_fields(model.get('creator')).get('username', info.get('creatorname'))),
        'source_url':source_url,
    }


def read(model_path):
    path = Path(model_path)
    for base in dict.fromkeys((path.stem, path.name)):
        for suffix, format_name in (('.cm-info.json', 'matrix'), ('.civitai.info', 'civitai')):
            candidate = path.with_name(base + suffix)
            try:
                check_directory_chain(candidate.parent)
                with VerifiedFile(candidate) as source:
                    if not 0 < source.info.st_size <= MAX_BYTES: continue
                    raw = source.file.read(MAX_BYTES+1); source.verify()
                    if len(raw) > MAX_BYTES: continue
                    result = parse(json.loads(raw.decode('utf-8-sig')), path, format_name)
                if result: return result
            except (OSError, ValueError, UnicodeError, RecursionError):
                continue
    return {}
