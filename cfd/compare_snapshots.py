"""Check iteration stability of all three maps on the same 3D case mesh.

Both complete SU2 snapshots must use identical wind and nondimensionalization.
This checks iterative changes, not mesh independence or agreement with a yacht.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

import meshio
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cfd.rans_slice import horizontal_slice
from cfd.transfer_solution import read_restart
from cfd.storage import case_file


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def physical_inputs(parameters):
    result={key:parameters.get(key) for key in ('wind_speed','wind_angle','boat_speed',
            'turbulence_intensity','turbulence_length','apparent_speed','gradient','flow_limiter')}
    if 'marine_profile' in parameters:result['marine_profile']=parameters['marine_profile']
    return result


def comparison_frames(case,started,last_iteration,window=100):
    """Select current-run frames separated by a full verification window."""
    frames=[]
    for path in case_file(case, 'restart.dat').parent.glob('restart_*.dat'):
        match=re.fullmatch(r'restart_(\d+)\.dat',path.name)
        if match and path.stat().st_mtime>=started and int(match[1])<=last_iteration:
            frames.append((int(match[1]),path))
    if not frames:return None
    after=max(frames)
    earlier=[frame for frame in frames if frame[0]<=after[0]-window]
    return (max(earlier),after) if earlier else None


def verify_saved_fields(case,height=4.):
    case=Path(case);parameters=json.loads((case/'run_parameters.json').read_text())
    saved=case/'field_stability.json';canonical=case/'restart.dat'
    inputs=physical_inputs(parameters)
    # Published datasets retain a hash-checked verification even when their
    # numbered source snapshots remain in the original case directory.
    if saved.exists() and canonical.exists():
        previous=json.loads(saved.read_text())
        if (previous.get('iteration_window',0)>=100 and previous.get('height_m')==height
            and previous.get('mesh_sha256')==parameters.get('mesh_sha256')
            and previous.get('case_inputs')==inputs
            and previous.get('after_sha256')==file_hash(canonical)):
            return previous
    if not canonical.exists() or not parameters.get('started_at_utc'):
        return {'passed':False,'reason':'No complete saved snapshots with iteration provenance'}
    history=np.genfromtxt(case/'history.csv',delimiter=',',names=True,deletechars=' "')
    last=int(np.atleast_1d(history['Inner_Iter'])[-1])
    started=datetime.fromisoformat(parameters['started_at_utc']).timestamp()
    pair=comparison_frames(case,started,last)
    if pair is None:
        return {'passed':False,'reason':'Need two current-run snapshots at least 100 iterations apart'}
    before,after=pair
    if file_hash(after[1])!=file_hash(canonical):
        return {'passed':False,'reason':'Latest snapshot does not match the current solution'}
    result=compare(case,before[1],after[1],height,saved)
    result.update(iteration_window=after[0]-before[0],
                  native_iterations=[before[0],after[0]])
    saved.write_text(json.dumps(result,indent=2),encoding='utf8')
    return result


def compare(case,before,after,height=4.,output=None):
    case=Path(case);before=Path(before);after=Path(after)
    parameters=json.loads((case/'run_parameters.json').read_text())
    speed=parameters['apparent_speed']
    old_names,old=read_restart(before);new_names,new=read_restart(after)
    expected=['x','y','z','Pressure','Velocity_x','Velocity_y','Velocity_z','Turb_Kin_Energy','Omega']
    mesh=meshio.read(case/'yacht.msh')
    for names,data in ((old_names,old),(new_names,new)):
        if names[:9]!=expected or not np.allclose(data[:,:3],mesh.points,rtol=0,atol=1e-10):
            raise ValueError('Snapshot fields or coordinates do not match this mesh')
        if not np.isfinite(data[:,4:8]).all() or (data[:,7]<0).any():
            raise ValueError('Snapshot has invalid velocity or turbulent kinetic energy')
    xy,values,triangles=horizontal_slice(mesh,np.column_stack((old[:,4:8],new[:,4:8])),height)
    u0=values[:,:3]*speed;u1=values[:,4:7]*speed
    # Same complete fluid viewport as the maps, including fluid above the hull.
    visible=(xy[:,0]>=-75)&(xy[:,0]<=15)&(xy[:,1]>=-30)&(xy[:,1]<=60)
    horizontal0=np.linalg.norm(u0[:,:2],axis=1);horizontal1=np.linalg.norm(u1[:,:2],axis=1)
    direction=visible&(horizontal0>.1*speed)&(horizontal1>.1*speed)
    angle=np.degrees(np.arctan2(u0[:,0]*u1[:,1]-u0[:,1]*u1[:,0],np.sum(u0[:,:2]*u1[:,:2],axis=1)))
    ti_scale=1.
    ti0=100*np.sqrt(2*values[:,3]/3)*ti_scale;ti1=100*np.sqrt(2*values[:,7]/3)*ti_scale
    def statistics(values,mask):
        a=abs(values[mask])
        index=np.flatnonzero(mask)[np.argmax(a)]
        return {'p50':float(np.median(a)),'p95':float(np.percentile(a,95)),
                'maximum':float(a.max()),'maximum_location_xy_m':xy[index].tolist()}
    changes={'velocity_vector_m_s':statistics(np.linalg.norm(u1-u0,axis=1),visible),
             'speed_m_s':statistics(np.linalg.norm(u1,axis=1)-np.linalg.norm(u0,axis=1),visible),
             'direction_deg':statistics(angle,direction),
             'turbulence_percentage_points':statistics(ti1-ti0,visible)}
    limits={'velocity_vector_m_s':.01,'speed_m_s':.01,'direction_deg':.1,'turbulence_percentage_points':.1}
    checks={name:changes[name]['maximum']<limit for name,limit in limits.items()}
    result={'before':str(before.resolve()),'after':str(after.resolve()),'height_m':height,
            'before_sha256':file_hash(before),'after_sha256':file_hash(after),
            'case_inputs':physical_inputs(parameters),
            'mesh_sha256':parameters['mesh_sha256'],'scope':'All fluid in the displayed viewport, including above the hull; direction excluded below 10% inflow speed',
            'points_compared':int(visible.sum()),'changes':changes,'maximum_change_limits':limits,
            'checks':checks,'passed':all(checks.values()),
            'interpretation':'Iteration stability only; neither grid independence nor validation of this idealised rig'}
    Path(output or case/'field_stability.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case',type=Path)
    parser.add_argument('before',type=Path)
    parser.add_argument('after',type=Path)
    parser.add_argument('--height',type=float,default=4.)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    compare(args.case,args.before,args.after,args.height,args.output)
