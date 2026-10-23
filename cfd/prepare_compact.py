"""Prepare a marine mesh with upstream padding; never starts SU2."""
import argparse
import json
from pathlib import Path
import shutil
from scipy.optimize import brentq

from cfd.marine_rans import prepare
from cfd.rans_mesh import make_mesh


def prepare_compact(source, case, threads=8):
    source, case = Path(source).resolve(), Path(case).resolve()
    base = case.parent / (case.name + '_base')
    if case.exists() or base.exists():
        raise ValueError('Choose a new case directory; existing meshes/results are preserved')
    base.mkdir(parents=True)
    metadata=json.loads((source/'mesh_metadata.json').read_text(encoding='utf8'))
    geometry=json.loads((source/'geometry.json').read_text(encoding='utf8'))
    layers=metadata['boundary_layers']
    # Leave room between the prism envelopes near the mast and sail head.
    # Retain all 30 layers and the 12 micrometre first spacing, with gentler growth.
    count=layers['layers'];first=layers['first_node_spacing_m']
    total=min(sum(first*layers['growth']**i for i in range(count)),.01772822353498272)
    growth=brentq(lambda g:sum(first*g**i for i in range(count))-total,1.,layers['growth'])
    # Plot coordinates are (-physical y, physical x): add 20 m at the
    # top and right of the existing viewport. Keep the 84 m atmosphere top.
    make_mesh(base,metadata['mesh_size'],metadata['wind_angle'],metadata['wake_size'],
              metadata['sail_size'],geometry['sail_definitions'],shapes=geometry['sail_shapes'],
              first_layer=first,layers=count,growth=growth,
              threads=threads,domain_min=[-50.,-50.,0.],domain_max=[35.,40.,28.])
    for name in ('yacht.cfg','airfoil_validation.json'):
        shutil.copy2(source/name,base/name)
    source_manifest=json.loads((source/'marine_case.json').read_text(encoding='utf8'))
    profile=source_manifest['profile']
    manifest=prepare(base,case,wind=profile['reference_speed_m_s'],angle=profile['true_angle_deg'],
                     boat_speed=profile['boat_speed_knots'],top=source_manifest['coefficient_reference_height_m'],
                     roughness_length=profile['roughness_length_m'])
    manifest['background_profile_centers_xy_m']=[[8.,-22.],[0.,-22.],[-35.,-22.],[-42.,5.]]
    manifest['domain_change']={'source_case':str(source),'plot_bounds_m':{'x':[-40.,30.],'y':[-50.,15.]},
        'computational_plot_bounds_m':{'x':[-40.,50.],'y':[-50.,35.]},
        'padding_m':{'top':20.,'right':20.},
        'note':'New mesh with the same sail geometry and requested wall/wake resolution; no solver run or convergence claim.'}
    (case/'marine_case.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    print(f'Padded marine case ready: {case}; no CFD started',flush=True)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parent/'reference')
    parser.add_argument('--case',type=Path,default=Path('cfd/output/marine_3d_v1'))
    parser.add_argument('--threads',type=int,default=8)
    args=parser.parse_args()
    if args.threads<1:parser.error('threads must be positive')
    prepare_compact(args.source,args.case,args.threads)


if __name__=='__main__':
    main()
