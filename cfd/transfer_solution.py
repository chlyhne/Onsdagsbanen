"""Read SU2 8.5 binary restart fields without losing mesh precision."""
from pathlib import Path
import numpy as np

def read_restart(path):
    path=Path(path)
    with path.open('rb') as stream:
        header=np.fromfile(stream,dtype=np.int32,count=5)
        if len(header)!=5 or header[0]!=535532 or not 1<=header[1]<=64 or header[2]<=0:
            raise ValueError('Not a supported SU2 compact binary restart')
        fields=[stream.read(33).split(b'\0')[0].decode('ascii') for _ in range(header[1])]
    offset=20+33*int(header[1])
    expected=offset+8*int(header[1])*int(header[2])
    if path.stat().st_size!=expected or len(set(fields))!=len(fields):
        raise ValueError('Unexpected restart size or duplicate field names')
    values=np.memmap(path,dtype=np.float64,mode='r',offset=offset,shape=(int(header[2]),int(header[1])))
    return fields,values
