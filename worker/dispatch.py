"""Explicit stage3 capabilities; no scan/network/file-operation dispatch."""

import threading
import time



from .protocol import FrameError, MAX_FRAME

METHODS = ('catalog.query', 'catalog.stats', 'catalog.facets', 'catalog.roots', 'catalog.detail', 'catalog.neighbors',
           'catalog.thumbnail', 'catalog.preview.v2', 'catalog.preview.release', 'job.get', 'job.cancel', 'engine.status', 'diagnostics.echo', 'diagnostics.wait', 'manage.read', 'manage.write', 'manage.status')


class DispatchError(ValueError):
    def __init__(self, code='invalid_request'):
        self.code = code
        super().__init__(code)


def fields(payload, allowed):
    if set(payload) - set(allowed): raise DispatchError()


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum: raise DispatchError()
    return value


def dispatch(service, method, payload, cancelled):
    if method not in METHODS: raise DispatchError('method_not_allowed')
    if cancelled.is_set(): raise DispatchError('cancelled')
    if method.startswith('manage.'):
        if method=='manage.write' and getattr(service,'single_view',False):raise DispatchError('method_not_allowed')
        from . import management
        if method=='manage.status':
            fields(payload,('operationId',));return management.status(service,payload.get('operationId'))
        fields(payload,('section','args') if method=='manage.read' else ('operationId','action','args'))
        args=payload.get('args',{})
        if not isinstance(args,dict):raise DispatchError()
        if method=='manage.read':return management.read(service,payload.get('section'),args,cancelled)
        return management.write(service,payload.get('action'),args,payload.get('operationId'),cancelled)
    if method == 'catalog.query':
        fields(payload, ('filters', 'cursor', 'limit'))
        if not isinstance(payload.get('filters', {}), dict): raise DispatchError()
        from .read_catalog import query
        return query(service,payload.get('filters'),payload.get('cursor'),integer(payload.get('limit',120),1,120))
    if method == 'catalog.stats': fields(payload, ()); return service.stats()
    if method == 'catalog.facets':
        fields(payload, ())
        with service.store.connect() as db:
            result={name:[dict(r) for r in db.execute("SELECT name,count FROM catalog_counts WHERE kind=? AND count>0 ORDER BY count DESC,name LIMIT 100",(kind,))] for name,kind in [('model','model'),('format','format'),('prompt','tag')]}
            result['model']=[dict(r) for r in db.execute("SELECT name FROM (SELECT name FROM catalog_counts WHERE kind='model' AND count>0 UNION SELECT model AS name FROM (SELECT model FROM images ORDER BY id DESC LIMIT 1000) WHERE model<>'') ORDER BY name LIMIT 100")]
            result['artist']=[dict(r) for r in db.execute("SELECT DISTINCT artist AS name FROM (SELECT artist FROM images ORDER BY id DESC LIMIT 1000) WHERE artist<>'' ORDER BY artist LIMIT 100")]
            result['content']=[dict(r) for r in db.execute('SELECT id,name FROM content_tags WHERE enabled=1 ORDER BY id LIMIT 128')]
            return result
    if method == 'catalog.roots':
        fields(payload, ())
        with service.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT r.id,r.name,COALESCE(c.count,0) AS count FROM roots r LEFT JOIN catalog_counts c ON c.kind='root' AND c.name=CAST(r.id AS TEXT) ORDER BY r.id LIMIT 200")]
    if method in ('catalog.detail', 'catalog.neighbors', 'catalog.thumbnail','catalog.preview.v2','catalog.preview.release'):
        identity=payload.get('identity')
        if identity is not None and (not isinstance(identity,str) or len(identity)!=64 or any(c not in '0123456789abcdef' for c in identity)):raise DispatchError()
        image_id = integer(payload.get('imageId'), 1, 2**63-1)
        if method=='catalog.preview.release':
            fields(payload,('imageId','identity'))
            if identity is None:raise DispatchError()
            from .preview_assets import release
            return release(service,image_id,identity)
        if method=='catalog.preview.v2':
            fields(payload,('imageId','identity','mode','frame'))
            if identity is None or payload.get('mode','standard') not in ('standard','original'):raise DispatchError()
            from .preview_assets import preview
            return preview(service,image_id,identity,payload.get('mode','standard'),cancelled,integer(payload.get('frame',0),0,4095))
        if method == 'catalog.detail':
            fields(payload, ('imageId','identity'))
            from .read_catalog import detail
            return detail(service,image_id,payload.get('identity'))
        if method == 'catalog.neighbors':
            fields(payload, ('imageId', 'filters', 'before', 'after'))
            if not isinstance(payload.get('filters', {}), dict): raise DispatchError()
            from .read_catalog import stamp
            result=service.browse_neighbors(image_id, payload.get('filters'), integer(payload.get('before', 4), 0, 4), integer(payload.get('after', 4), 0, 4))
            stamp(service,result['items']);return result
        fields(payload, ('imageId', 'size','identity'))
        from .read_catalog import thumbnail
        return thumbnail(service,image_id,payload.get('identity'),integer(payload.get('size',400),64,1200))
    if method in ('job.get', 'job.cancel'):
        fields(payload, ('jobId',))
        value = payload.get('jobId')
        if not isinstance(value, str) or not 1 <= len(value) <= 100: raise DispatchError()
        return service.job(value) if method == 'job.get' else service.cancel(value)
    if method == 'engine.status':
        fields(payload, ('operationId',))
        value = payload.get('operationId', '')
        if not isinstance(value, str) or len(value) > 128: raise DispatchError()
        return {'outcome': 'not_enabled', 'writesEnabled': False, 'operationId': value}
    if method == 'diagnostics.echo': return payload
    if method == 'diagnostics.wait':
        fields(payload, ('milliseconds', 'safeMilliseconds'))
        duration = integer(payload.get('milliseconds', 1000), 0, 10000)
        safe = integer(payload.get('safeMilliseconds', 0), 0, min(250, duration))
        time.sleep(safe/1000)  # Synthetic non-interruptible checkpoint, never file work.
        if cancelled.wait((duration-safe)/1000): raise DispatchError('cancelled')
        return {'completed': True}


def hello():
    return {'protocol': 1, 'engineVersion':'1.3.0', 'preview': True, 'writesEnabled': False, 'managementWrites': True, 'destructiveWrites': True, 'fileOperations': True, 'maxFrameBytes': MAX_FRAME,
            'maxQuery': 120, 'maxNeighbors': 9, 'capabilities': list(METHODS), 'workers': 2, 'admitted': 4}
