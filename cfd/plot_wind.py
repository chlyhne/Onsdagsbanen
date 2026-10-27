"""Render sail-chord-weighted height-averaged wind maps by default.

python -m cfd.plot_wind --case cfd/results/v1_5ms
python -m cfd.plot_wind --case cfd/results/v1_5ms --field speed

Reads saved 3D results for post-processing; never runs a CFD simulation.
Use --slice for an explicitly requested single-height map.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from cfd.plot_data import load_plot_data
from cfd.storage import case_file, result_dir

DPI=240
FIGURE_SIZE=(12,11)
SCALE_CLEARANCE_M=1.
KNOTS_PER_M_S=3600/1852
VMG_FACTOR=.18
HEADER_WEIGHT_KNOTS_PER_DEG=.18
# Keep the existing white interval fixed in VMG units when weights change.
VMG_WHITE_BAND_KNOTS=.15*KNOTS_PER_M_S*.030
VMG_CONTOUR_KNOTS=.1
VMG_CONTOUR_STEP_KNOTS=.1
FIELD_FILES={'speed':'vindhastighed.png','direction':'vinddrejning.png',
             'goodness':'godhed.png'}


def top_view(points):
    points=np.asarray(points)
    return np.column_stack((-points[:,1],points[:,0]))


def delta_vmg(speed_delta_m_s,header_deg):
    """User-supplied VMG estimate in knots; a positive header is adverse."""
    return VMG_FACTOR*(speed_delta_m_s*KNOTS_PER_M_S-HEADER_WEIGHT_KNOTS_PER_DEG*header_deg)




def outside_color_limits(values, reference_mask):
    """Use only the selected reference points to determine the extrema."""
    exterior=np.ma.masked_invalid(np.ma.asarray(values))[reference_mask].compressed()
    if not exterior.size:
        raise RuntimeError("No valid wind-field values in the color-scale reference region")
    return float(exterior.min()),float(exterior.max())


def spatial_median(triangulation, values, xlim, ylim, hull, other_hulls=()):
    """Area-weight triangle-centre values so fine sail cells do not dominate."""
    cells=triangulation.get_masked_triangles()
    xy=np.column_stack((triangulation.x,triangulation.y))[cells]
    centers=xy.mean(axis=1)
    physical=np.column_stack((centers[:,1],-centers[:,0]))
    mask=(centers[:,0]>=xlim[0])&(centers[:,0]<=xlim[1]) \
        &(centers[:,1]>=ylim[0])&(centers[:,1]<=ylim[1]) \
        &(distance_from_boats(physical,[hull,*other_hulls])>=SCALE_CLEARANCE_M)
    ab=xy[:,1]-xy[:,0];ac=xy[:,2]-xy[:,0]
    weights=np.abs(ab[:,0]*ac[:,1]-ab[:,1]*ac[:,0])/2
    samples=np.asarray(values)[cells].mean(axis=1)
    mask&=np.isfinite(samples)&(weights>0)
    if not mask.any():raise ValueError('No valid exterior area for median reference')
    samples=samples[mask];weights=weights[mask]
    order=np.argsort(samples)
    cumulative=np.cumsum(weights[order])
    return float(samples[order[np.searchsorted(cumulative,cumulative[-1]/2)]])


def distance_from_boat(points, outline):
    """Horizontal Euclidean distance from a closed hull footprint; zero inside."""
    from matplotlib.path import Path as PlotPath
    points=np.asarray(points);outline=np.asarray(outline)
    squared=np.full(len(points),np.inf)
    for start,end in zip(outline,np.roll(outline,-1,axis=0)):
        edge=end-start
        fraction=np.clip(((points-start)@edge)/(edge@edge),0.,1.)
        offset=points-(start+fraction[:,None]*edge)
        squared=np.minimum(squared,np.sum(offset**2,axis=1))
    inside=PlotPath(outline).contains_points(points,radius=1e-9)
    return np.where(inside,0.,np.sqrt(squared))


def distance_from_boats(points, outlines):
    """Nearest hull footprint; retain all gaps between separate boats."""
    return np.minimum.reduce([distance_from_boat(points, outline) for outline in outlines])


def linear_color_norm(low,high,neutral):
    """One linear slope; signed fields have a symmetric range about neutral."""
    from matplotlib.colors import Normalize
    span=max(abs(low-neutral),abs(high-neutral),1e-9)
    return Normalize(vmin=neutral-span,vmax=neutral+span,clip=True)




def render(case,field='all',output_dir=None,height=None,*,averaged=True):
    if not averaged:
        return render_slice(case,field,output_dir,height)
    if height is not None:
        raise ValueError('A height applies only to a slice; use averaged=False.')
    from tempfile import TemporaryDirectory
    import shutil
    from cfd.wind_viewer_3d import prepare as prepare_volume
    from cfd.weighted_wind_2d import prepare as prepare_average, render as render_average

    case=Path(case)
    output=Path(output_dir) if output_dir is not None else result_dir(case)
    output.mkdir(parents=True,exist_ok=True)
    # Keep the single-height calibration needed by the 3D post-processor,
    # but publish only averaged PNGs at the normal result paths.
    with TemporaryDirectory(prefix='wind-calibration-') as directory:
        render_slice(case,output_dir=directory)
        scales=case_file(case,'plot_scales.json')
        scales.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(Path(directory)/'plot_scales.json',scales)
    source=result_dir(case)/'visualizations'
    prepare_volume(case,case,source)
    metadata,arrays=prepare_average(source,output)
    fields=tuple(FIELD_FILES) if field=='all' else (field,)
    files=[]
    for name in fields:
        target=output/FIELD_FILES[name]
        render_average(metadata,arrays,target,field=name)
        files.append(str(target))
    result={'files':files,'averaging':'sail-chord-weighted height average',
            'height_range_m':metadata['height_range_m'],'solver_run':False}
    print(json.dumps(result,indent=2),flush=True)
    return result


def render_slice(case,field='all',output_dir=None,height=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.tri as mtri
    import matplotlib.patheffects as path_effects
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.patches import Polygon

    started=perf_counter();case=Path(case)
    context,arrays=load_plot_data(case)
    if height is not None and height!=context['height_m']:
        raise ValueError(f'The saved slice is at z={context["height_m"]:g} m. '
                         'Prepare a different height with python -m cfd.plot_data --case '
                         f'"{case}" --height {height:g}. No simulation is needed.')
    output=Path(output_dir) if output_dir is not None else result_dir(case)
    output.mkdir(parents=True,exist_ok=True)
    selected=set(FIELD_FILES.values()) if field=='all' else {FIELD_FILES[field]}
    loaded=perf_counter()
    xy=arrays['xy'];triangles=arrays['triangles'];plot_xy=top_view(xy)
    tri=mtri.Triangulation(plot_xy[:,0],plot_xy[:,1],triangles)
    angle=np.ma.masked_invalid(arrays['angle_deg'])
    angle_tri=mtri.Triangulation(plot_xy[:,0],plot_xy[:,1],triangles)
    angle_tri.set_mask(np.ma.getmaskarray(angle)[triangles].any(axis=1))
    delta=arrays['speed_delta']
    hull_outline=np.asarray(context['hull_outline'])
    hull_outlines=context.get('hull_outlines',[hull_outline])
    color_reference=distance_from_boats(xy,hull_outlines)>=SCALE_CLEARANCE_M
    wind=context['wind']
    BOAT_LENGTH=context['boat_length_m']
    xlim=(-40,30)
    ylim=(-50,BOAT_LENGTH/2+BOAT_LENGTH)
    reference_speed_delta=spatial_median(tri,delta,xlim,ylim,hull_outline,hull_outlines[1:])
    reference_angle=spatial_median(angle_tri,angle.filled(0),xlim,ylim,hull_outline,hull_outlines[1:])
    # Positive direction is a header on this tack, hence a negative contribution.
    goodness=delta_vmg(delta,angle)
    reference_goodness=spatial_median(angle_tri,goodness.filled(0),xlim,ylim,hull_outline,hull_outlines[1:])
    goodness=goodness-reference_goodness
    reference_label='arealvægtet median i billedudsnittet, mindst 1 m fra båden'
    # Recenter only the presentation; retain the original physical slice cache.
    delta=delta-reference_speed_delta
    angle=(angle-reference_angle+180.)%360.-180.
    direction_score=delta_vmg(0.,angle)
    speed_score=delta_vmg(delta,0.)
    shared_low,shared_high=outside_color_limits(goodness,color_reference)
    offsets={'vindhastighed.png':reference_speed_delta,'vinddrejning.png':reference_angle,
             'godhed.png':reference_goodness}
    reference_values={'vindhastighed.png':reference_speed_delta,'vinddrejning.png':reference_angle,
                      'godhed.png':reference_goodness}

    def draw_boat(target):
        for outline in hull_outlines:
            target.add_patch(Polygon(top_view(outline),
                                     facecolor='#b8b8b8',edgecolor='#142b3b',
                                     linewidth=1.7,zorder=5,
                                     path_effects=[path_effects.withStroke(linewidth=3.5,foreground='white')]))
        for section in context['sail_sections']:
            target.add_patch(Polygon(top_view(section),facecolor='black',
                                     edgecolor='black',linewidth=1.3,zorder=7,
                                     path_effects=[path_effects.withStroke(linewidth=2.7,foreground='white')]))

    color_scales={}
    for filename,title,data,triang in [
        ("vinddrejning.png","Skræller og rummer",direction_score.filled(0),angle_tri),
        ("vindhastighed.png","Vindhastighed",speed_score,tri),
        ("godhed.png","Ændring i VMG",goodness.filled(0),angle_tri)]:
        if filename not in selected:
            continue
        scale_values={'vinddrejning.png':direction_score,'godhed.png':goodness}.get(filename,data)
        low,high=outside_color_limits(scale_values,color_reference)
        color_scales[filename]={"reference":"entire horizontal slice at least 1 metre from the projected hull footprint",
                                "minimum_distance_from_boat_m":SCALE_CLEARANCE_M,
                                "reference_points":int(color_reference.sum()),
                                "exterior_min":low,"exterior_max":high,
                                "normalization":"single linear slope; symmetric signed range about area-weighted median",
                                "neutral_reference":reference_label,
                                "median_method":"triangle-center field values weighted by triangle area",
                                "median_plot_bounds_m":[list(xlim),list(ylim)],
                                "reference_value_in_cached_units":reference_values[filename],
                                "subtracted_offset":offsets[filename]}
        color_scales[filename].update(shared_scale_reference='godhed.png',
                                     units='knots',vmg_factor=VMG_FACTOR,
                                     header_weight_knots_per_deg=HEADER_WEIGHT_KNOTS_PER_DEG,
                                     speed_conversion_knots_per_m_s=KNOTS_PER_M_S)
        if filename=='vinddrejning.png':
            color_scales[filename]['display_conversion']=f'-{VMG_FACTOR:g} * {HEADER_WEIGHT_KNOTS_PER_DEG:g} * (angle_deg - median_angle_deg)'
        if filename=='vindhastighed.png':
            color_scales[filename]['display_conversion']=f'{VMG_FACTOR:g} * (speed_delta_m_s - median_speed_delta_m_s) * 3600/1852'
        if filename=='godhed.png':
            color_scales[filename].update(formula=f'{VMG_FACTOR:g} * (speed_delta_m_s * 3600/1852 - {HEADER_WEIGHT_KNOTS_PER_DEG:g} * angle_deg) - median',
                                          formula_source='user supplied estimate',positive='better')
        fig,ax=plt.subplots(figsize=FIGURE_SIZE,layout="constrained")
        baseline=0.
        norm=linear_color_norm(shared_low,shared_high,baseline)
        color_scales[filename].update(vmin=norm.vmin,vmax=norm.vmax,neutral=baseline,
                                     clipped=True)
        white_band=VMG_WHITE_BAND_KNOTS
        # Keep the scalar normalization linear; add a white palette plateau.
        left=float(np.clip(norm(-white_band),0,1))
        right=float(np.clip(norm(white_band),0,1))
        if left==0 and right==1:
            cmap=LinearSegmentedColormap.from_list('field_white',['white','white'])
        else:
            cmap=LinearSegmentedColormap.from_list('field_white_band',[
                (0.,'#67001f'),(left/2,'#ef8a62'),(left,'white'),
                (right,'white'),((1+right)/2,'#67a9cf'),(1.,'#053061')],N=4097)
        color_scales[filename]['white_band_relative_to_median_knots']=[-white_band,white_band]
        im=ax.tripcolor(triang,data,shading="gouraud",cmap=cmap,norm=norm)
        # Fill the actual scalar band so interpolation leaves it pure white.
        ax.tricontourf(triang,data,levels=[-white_band,white_band],colors=['white'],zorder=2)
        bar=fig.colorbar(im,ax=ax,orientation='horizontal',
                         fraction=.04,pad=.025,shrink=.8,
                         ticks=np.linspace(norm.vmin,norm.vmax,5))
        from matplotlib.ticker import FuncFormatter
        bar.ax.xaxis.set_major_formatter(FuncFormatter(
            lambda value,pos: ('0' if abs(value)<1e-10 else f'{value:+.2f}').replace('.',',')))
        bar.ax.tick_params(labelsize=14)
        bar.set_label('Ændring i VMG [knob] · rød = dårligere · blå = bedre',fontsize=15,labelpad=10)
        bar.ax.tick_params(length=0,pad=8)
        bar.outline.set_visible(False)
        color_scales[filename].update(presentation=True,wind_arrow='true wind direction')
        magnitudes=np.arange(VMG_CONTOUR_STEP_KNOTS,norm.vmax+1e-12,VMG_CONTOUR_STEP_KNOTS)
        magnitudes=np.unique(np.r_[VMG_CONTOUR_KNOTS,
            magnitudes[magnitudes>VMG_CONTOUR_KNOTS]])
        levels=np.r_[-magnitudes[::-1],magnitudes]
        contours=ax.tricontour(triang,data,
            levels=levels,
            colors=['#a44b42' if value<0 else '#427b9b' for value in levels],
            linewidths=.7,zorder=3)
        contours.set_path_effects([path_effects.withStroke(linewidth=1.4,foreground='white')])
        def contour_label(value):
            return f'{value:+.2f} knob'.replace('.',',')
        ax.clabel(contours,fmt=contour_label,fontsize=9,inline=True,inline_spacing=5)
        color_scales[filename]['contours_delta_vmg_knots']=levels.tolist()
        color_scales[filename]['contour_step_knots']=VMG_CONTOUR_STEP_KNOTS
        # Actual sail sections at the plotted height, not their deck projections.
        draw_boat(ax)
        twa=np.deg2rad(wind['true_angle_deg'])
        wind_arrow=7*np.array([-np.sin(twa),-np.cos(twa)])
        ax.quiver(23,5,*wind_arrow,angles="xy",scale_units="xy",scale=1,color="black",zorder=6)
        ax.text(13,-4,"Sand vind",fontsize=15)
        ax.set(xlim=xlim,ylim=ylim,aspect='equal')
        ax.set_title(title+context.get('title_suffix',''),fontsize=22,pad=12)
        ax.set_axis_off()
        ax.grid(False)
        # Settle the equal-aspect/colorbar layout before the higher-DPI save;
        # otherwise some maps can lose their left axis labels on the first draw.
        fig.canvas.draw()
        fig.savefig(output/filename,dpi=DPI)
        plt.close(fig)

    scales_file=output/'plot_scales.json'
    scales=json.loads(scales_file.read_text(encoding='utf8')) if scales_file.exists() else {}
    scales.pop('turbulens.png',None)
    scales.update(color_scales)
    scales_file.write_text(json.dumps(scales,indent=2),encoding='utf8')
    # Preserve the existing report interface; only presentation metadata changes.
    if output.resolve()==result_dir(case).resolve() and case_file(case,'report.json').exists():
        report=json.loads(case_file(case,'report.json').read_text(encoding='utf8'))
        report.get('color_scales',{}).pop('turbulens.png',None)
        report.setdefault('color_scales',{}).update(color_scales)
        case_file(case,'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    (output/'samlet_vindulempe.png').unlink(missing_ok=True)
    (output/'turbulens.png').unlink(missing_ok=True)
    result={'load_seconds':loaded-started,'render_seconds':perf_counter()-loaded,
            'total_seconds':perf_counter()-started,'files':[str(output/name) for name in color_scales],
            'dpi':DPI,'pixel_size':[int(x*DPI) for x in FIGURE_SIZE]}
    print(json.dumps(result,indent=2),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',type=Path,default=Path('cfd/results/v1_5ms'))
    parser.add_argument('--field',choices=('all',*FIELD_FILES),default='all')
    parser.add_argument('--output-dir',type=Path,help='Optional destination for the PNGs')
    parser.add_argument('--slice',action='store_true',help='Explicitly render the saved single-height slice instead of the default height average')
    args=parser.parse_args()
    render(args.case,args.field,args.output_dir,averaged=not args.slice)


if __name__=='__main__':
    main()
