"""One bounded compressed multiframe container, without an original file handle."""
import io
import threading
from PIL import Image

class SourceCache:
    def __init__(self):
        self.lock=threading.RLock()
        self.key=self.owner=self.image=self.stream=None
        self.count=1;self.offset=0;self.animated=False;self.repeat_limit=1;self.loads=0
    @property
    def bytes(self):return self.stream.getbuffer().nbytes if self.stream else 0
    def clear(self):
        if self.image is not None:self.image.close()
        if self.stream is not None:self.stream.close()
        self.key=self.owner=self.image=self.stream=None
        self.count=1;self.offset=0;self.animated=False;self.repeat_limit=1
    def close(self):
        with self.lock:self.clear()
    def release(self,owner):
        with self.lock:
            if self.owner==owner:self.clear()
    def acquire(self,source,key,owner,max_bytes,max_frames):
        if self.key==key:return self.image,True
        self.clear();probe=Image.open(source.file);detached=False
        try:
            self.offset=1 if probe.format=='PNG' and probe.info.get('default_image') else 0
            self.count=getattr(probe,'n_frames',1)-self.offset
            if not 1<=self.count<=max_frames:raise ValueError('frame_count')
            self.animated=self.count>1 and probe.format in ('GIF','WEBP','PNG')
            loop=probe.info.get('loop')
            self.repeat_limit=(0 if loop==0 else loop+1 if probe.format=='GIF' else loop) if type(loop) is int and 0<=loop<=65535 else 1
            if self.count==1:return probe,False
            source.file.seek(0);raw=source.file.read(max_bytes+1)
            if len(raw)>max_bytes:raise ValueError('container_size')
            source.verify()
            self.stream=io.BytesIO(raw);self.image=Image.open(self.stream)
            self.key=key;self.owner=owner;self.loads+=1
            detached=True;return self.image,True
        except BaseException:
            detached=True;self.clear();raise
        finally:
            if detached:probe.__exit__(None,None,None)
