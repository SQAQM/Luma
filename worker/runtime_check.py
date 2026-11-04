"""Fixed dependency self-check. No model/image/user input and no network."""
import importlib
import re

def check():
    result={}
    for name in ('numpy','onnxruntime','tokenizers'):
        try:
            module=importlib.import_module(name)
            if name=='numpy':valid=bool(module.zeros((1,)).size)
            elif name=='onnxruntime':module.disable_telemetry_events();valid=bool(module.get_available_providers())
            else:
                from tokenizers.models import WordLevel
                tokenizer=module.Tokenizer(WordLevel({'synthetic':0,'[UNK]':1},unk_token='[UNK]'))
                valid=tokenizer.encode('synthetic').ids==[0]
            result[name]=valid
        except Exception as error:
            result[name]=False
            # Only public dependency names/error classes; never exception text/paths.
            missing=getattr(error,'name','') or ''
            result[name+'Error']=type(error).__name__+(':'+missing if re.fullmatch('[A-Za-z0-9_.]{1,128}',missing) else '')
            causes=[];current=error
            for _ in range(5):
                if current is None:break
                module=getattr(current,'name','') or ''
                flags=[value for value in ('DLL load failed','_multiarray_umath','datetime','source directory','cannot import name','No module named','PyCapsule_Import') if value in str(current)]
                causes.append({'type':type(current).__name__,'module':module if re.fullmatch('[A-Za-z0-9_.]{1,128}',module) else '', 'flags':flags})
                current=current.__cause__ or current.__context__
            result[name+'Causes']=causes
    return result
