"""Start a new wind-speed case on an existing marine mesh, without empty CFD."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import shutil

import meshio
import numpy as np

from cfd.marine_inflow import write_initial_field
from cfd.marine_rans import configuration, read_profile, run, sha, write_inlets


def prepare(source, case, speed, iterations):
    source,case=Path(source).resolve(),Path(case).resolve()
    if case.exists():
        raise ValueError('Use a new case directory; existing results are preserved')
    if not np.isfinite(speed) or speed<=0:
        raise ValueError('Wind speed must be positive and finite')
    manifest=json.loads((source/'marine_case.json').read_text(encoding='utf8'))
    if sha(source/'yacht.su2')!=manifest['mesh_sha256']:
        raise ValueError('Source mesh checksum differs from its manifest')
    validation=json.loads((source/'airfoil_validation.json').read_text(encoding='utf8'))
    if not validation.get('passed'):
        raise ValueError('Source numerical reference did not pass')
    # Only change wind strength: retain sea roughness, direction and boat speed.
    source_profile=read_profile(manifest)
    profile=replace(source_profile,reference_speed_m_s=speed)
    top=manifest['coefficient_reference_height_m']
    velocity=np.asarray(profile.at(top)['apparent_velocity_m_s'])
    scale=float(np.linalg.norm(velocity))
    case.mkdir(parents=True)
    for name in ('yacht.msh','yacht.su2','mesh_metadata.json','geometry.json','airfoil_validation.json'):
        print(f'Copying unchanged mesh/input: {name}',flush=True)
        shutil.copy2(source/name,case/name)
    mesh=meshio.read(case/'yacht.msh')
    mesh.cell_data['tag']=mesh.cell_data['gmsh:physical']
    inlet_counts=write_inlets(mesh,case/'inlet_atmosphere.dat',profile)
    del mesh
    (case/'yacht.cfg').write_text(configuration(
        (source/'yacht.cfg').read_text(),profile,top,iterations),encoding='ascii')
    print('Writing analytic shear initialization; no empty-domain solve.',flush=True)
    initial=write_initial_field(case/'yacht.msh',case/'initial_atmosphere.dat',profile,scale)
    manifest.pop('free_flow',None)
    manifest.update(status='prepared_not_simulated',solver_started=False,
        solver_execution_tested=False,source_case=str(source),profile=profile.metadata(),
        inlet_nodes=inlet_counts,initial_field=initial,
        native_reference_velocity_m_s=velocity.tolist(),native_reference_speed_m_s=scale,
        initialization='analytic neutral wind shear; no empty-domain reference',
        wind_change={'source_reference_speed_m_s':source_profile.reference_speed_m_s,
                     'reference_speed_m_s':speed,'mesh_reused':True,'roughness_unchanged':True})
    (case/'marine_case.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    print(f'Prepared {case}: true wind {speed:g} m/s; unchanged extended mesh.',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path('cfd/output/marine_3d_padded_5ms'))
    parser.add_argument('--case',type=Path,default=Path('cfd/output/marine_3d_new_wind'))
    parser.add_argument('--wind-speed',type=float,default=5.)
    parser.add_argument('--threads',type=int,default=8)
    parser.add_argument('--iterations',type=int,default=3000)
    args=parser.parse_args()
    if args.threads<1 or args.iterations<200:
        parser.error('Require positive threads and at least 200 iterations')
    prepare(args.source,args.case,args.wind_speed,args.iterations)
    run(args.case,args.threads,args.iterations)


if __name__=='__main__':
    main()
