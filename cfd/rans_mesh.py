"""Smooth thin sail CAD and a conforming prism/tetrahedron fluid mesh.

Matching surface grids and explicit normal offsets form the prism layers;
Netgen meshes the hull and domain; Gmsh fills the air core with tetrahedra.
All viscous surfaces are wall resolved, including hull and mast.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

import meshio
import numpy as np

VERSION=4
FOOT=1.15
MARKERS={"farfield":10,"sea":11,"hull":12,"main":13,"jib":14}
SHAPES=[{"name":"main","camber":.08,"thickness_ratio":.001,"twist_deg":6.,"trim_height_m":4.,"head_luff_x_m":0.},
        {"name":"jib","camber":.08,"thickness_ratio":.001,"twist_deg":8.,"trim_height_m":4.,"head_luff_x_m":.5}]


def camber_profile(t, shape):
    """Chord-normalised depth and slope; old files retain their 50% draft.

    Two parabolas meet with zero slope at the requested maximum depth.
    This is the NACA mean-camber construction, not a thick NACA airfoil.
    """
    t = np.asarray(t, dtype=float)
    m, p = float(shape['camber']), float(shape.get('draft_position', .5))
    if not np.isfinite([m, p]).all() or m < 0 or not 0 < p < 1:
        raise ValueError('Require finite nonnegative camber and 0 < draft_position < 1')
    forward = t <= p
    depth = np.where(forward, m / p**2 * (2*p*t - t*t),
                     m / (1-p)**2 * (1-2*p + 2*p*t - t*t))
    slope = np.where(forward, 2*m / p**2 * (p-t),
                     2*m / (1-p)**2 * (p-t))
    return depth, slope


def _skin_points(sail,height,t,shape,side):
    leading,chord,span,trim=sail
    height,t=np.broadcast_arrays(height,t)
    f=(height-FOOT)/span
    if np.any((f< -1e-10)|(f>1+1e-10)):
        raise ValueError("Section does not intersect sail")
    c=chord*(1-.96*f)
    angle=trim+shape["twist_deg"]*(f-(shape["trim_height_m"]-FOOT)/span)
    a=np.radians(angle)
    camber=c*camber_profile(t,shape)[0]
    half=.5*shape["thickness_ratio"]*c*(.15+.85*np.sin(np.pi*t))
    u=t*c
    v=camber+side*half
    leading_x=leading*(1-f)+shape["head_luff_x_m"]*f
    return np.column_stack((leading_x-u*np.cos(a)+v*np.sin(a),
                            u*np.sin(a)+v*np.cos(a),height))


def section(sail,height,shape,n=97,cosine=True):
    t=(1-np.cos(np.linspace(0,np.pi,n)))/2 if cosine else np.linspace(0,1,n)
    return np.vstack((_skin_points(sail,height,t,shape,1),_skin_points(sail,height,t[::-1],shape,-1)))


def entry_angle(sail,height,shape,fraction=.05):
    # Exact entry secant of the selected camber line, including twist.
    if not 0 < fraction <= 1:
        raise ValueError('Entry fraction must be in (0, 1]')
    f=(height-FOOT)/sail[2]
    trim=sail[3]+shape["twist_deg"]*(f-(shape["trim_height_m"]-FOOT)/sail[2])
    return float(trim+np.degrees(np.arctan(camber_profile(fraction,shape)[0]/fraction)))


def _sail(sail,shape,surface_size):
    from netgen.occ import Pnt,Wire,SplineInterpolation,Segment,ThruSections
    wires=[]
    # Cubic CAD splines replace the old eight planar chord panels.
    for fraction in np.linspace(0,1,6):
        pts=section(sail,FOOT+fraction*sail[2],shape,n=49)
        n=len(pts)//2
        upper=[Pnt(*p) for p in pts[:n]]
        lower=[Pnt(*p) for p in pts[n:]]
        wires.append(Wire([SplineInterpolation(upper),Segment(upper[-1],lower[0]),
                           SplineInterpolation(lower),Segment(lower[-1],upper[0])]))
    body=ThruSections(wires,solid=True)
    body.faces.name=shape["name"]
    body.faces.maxh=surface_size
    return body


def _convert(ngmesh):
    # The public Netgen API avoids meshio's older .vol reader, which does not
    # recognise the new facedescriptors section. Use meshio's documented
    # Netgen node permutations to retain the standard element orientation.
    from meshio.netgen._netgen import netgen_to_meshio_pmap,netgen3d_to_meshio_type
    points=np.asarray([p.p for p in ngmesh.Points()])
    groups=defaultdict(list);tags=defaultdict(list)
    for e in ngmesh.Elements3D():
        kind=netgen3d_to_meshio_type[len(e.vertices)]
        nodes=np.array([v.nr-1 for v in e.vertices])
        groups[kind].append(nodes[netgen_to_meshio_pmap[kind]])
        tags[kind].append(1)
    for e in ngmesh.Elements2D():
        fd=ngmesh.FaceDescriptor(e.index)
        # The mapped prism/core interface is internal and must never become
        # a no-slip surface or an extra SU2 boundary marker.
        if fd.domin and fd.domout:
            continue
        name=fd.bcname
        if name not in MARKERS:
            raise RuntimeError(f"Unexpected exterior boundary {name!r}")
        kind={3:"triangle",4:"quad"}[len(e.vertices)]
        groups[kind].append([v.nr-1 for v in e.vertices])
        tags[kind].append(MARKERS[name])
    cells=[(kind,np.asarray(nodes,dtype=np.int64)) for kind,nodes in groups.items()]
    labels=[np.asarray(tags[kind]) for kind,_ in cells]
    fields={"air":np.array([1,3]),**{name:np.array([tag,2]) for name,tag in MARKERS.items()}}
    mesh=meshio.Mesh(points,cells,cell_data={"tag":labels},field_data=fields)
    return mesh


def _parametric_sail_grid(sail,size):
    """Quasi-isotropic triangles in chord-distance/height coordinates.

    The same triangles map to both skins. Head resolution follows local
    chord instead of squeezing a fixed number of stations into a tiny tip.
    """
    import gmsh
    _,chord,span,_=sail
    gmsh.initialize()
    try:
        gmsh.option.setNumber('General.Terminal',0)
        gmsh.option.setNumber('General.NumThreads',2)
        gmsh.model.add('sail_parameter_plane')
        points=[gmsh.model.geo.addPoint(x,z,0.) for x,z in
                ((0.,FOOT),(chord,FOOT),(.04*chord,FOOT+span),(0.,FOOT+span))]
        lines=[gmsh.model.geo.addLine(points[i],points[(i+1)%4]) for i in range(4)]
        loop=gmsh.model.geo.addCurveLoop(lines)
        gmsh.model.geo.addPlaneSurface([loop])
        gmsh.model.geo.synchronize()
        gmsh.option.setNumber('Mesh.Algorithm',6)
        gmsh.option.setNumber('Mesh.MeshSizeFromPoints',0)
        gmsh.option.setNumber('Mesh.MeshSizeFromCurvature',0)
        gmsh.option.setNumber('Mesh.MeshSizeExtendFromBoundary',0)
        gmsh.option.setNumber('Mesh.MeshSizeMin',min(.001,.04*chord/32))
        gmsh.option.setNumber('Mesh.MeshSizeMax',size)
        def size_at(dim,tag,x,z,unused,lc):
            local=chord*(1-.96*np.clip((z-FOOT)/span,0,1))
            return min(lc,size,local/16)
        gmsh.model.mesh.setSizeCallback(size_at)
        distance=gmsh.model.mesh.field.add('Distance')
        gmsh.model.mesh.field.setNumbers(distance,'CurvesList',[lines[3]])
        threshold=gmsh.model.mesh.field.add('Threshold')
        for key,value in {'InField':distance,'SizeMin':size/2,'SizeMax':size,
                          'DistMin':size,'DistMax':4*size}.items():
            gmsh.model.mesh.field.setNumber(threshold,key,value)
        gmsh.model.mesh.field.setAsBackgroundMesh(threshold)
        gmsh.model.mesh.generate(2)
        gmsh.model.mesh.optimize('Relocate2D',niter=3)
        tags,xyz,_=gmsh.model.mesh.getNodes()
        order=np.argsort(tags);tags=tags[order]
        uv=np.asarray(xyz).reshape(-1,3)[order,:2]
        index=np.full(int(tags.max())+1,-1,dtype=int);index[tags]=np.arange(len(tags))
        triangles=index[np.asarray(gmsh.model.mesh.getElementsByType(2)[1]).reshape(-1,3)]
        edges={name:index[np.asarray(gmsh.model.mesh.getElementsByType(1,line)[1]).reshape(-1,2)]
               for name,line in zip(('foot','trailing','head','leading'),lines)}
        return uv,triangles,edges
    finally:
        gmsh.model.mesh.removeSizeCallback()
        gmsh.finalize()


def _matched_surface(outer_mesh,sails,shapes,size,lower,upper):
    """Map matching upper/lower triangles from a graded parameter plane."""
    from netgen.meshing import Mesh,MeshPoint,Pnt,FaceDescriptor,Element2D
    m=Mesh(dim=3);m.SetMaterial(1,"air")
    descriptors={}
    def descriptor(name,patch=None):
        key=(name,patch)
        if key not in descriptors:
            bc=len(descriptors)+1
            tag=m.Add(FaceDescriptor(surfnr=bc,domin=1,domout=0,bc=bc))
            m.SetBCName(bc-1,name)
            descriptors[key]=tag
        return descriptors[key]
    coordinates=[]
    def node(p):
        coordinates.append(np.asarray(p))
        return m.Add(MeshPoint(Pnt(*p))).nr
    mapping={};outer_sign=None
    center=(np.asarray(lower)+upper)/2
    for face in outer_mesh.Elements2D():
        name=outer_mesh.FaceDescriptor(face.index).bcname
        if name in ("main","jib"):
            continue
        vertices=[]
        for v in face.vertices:
            if v.nr not in mapping:
                mapping[v.nr]=node(outer_mesh.Points()[v.nr].p)
            vertices.append(mapping[v.nr])
        m.Add(Element2D(descriptor(name,face.index if name=='hull' else None),vertices))
        if outer_sign is None and name=="farfield":
            p=np.asarray([coordinates[v-1] for v in vertices])
            outer_sign=np.sign(np.dot(np.cross(p[1]-p[0],p[2]-p[0]),p.mean(axis=0)-center))
    if not outer_sign:
        raise RuntimeError("Cannot determine fluid boundary orientation")
    body_sign=-outer_sign
    def triangle(ids,normal,fd):
        p=np.asarray([coordinates[v-1] for v in ids])
        if np.dot(np.cross(p[1]-p[0],p[2]-p[0]),normal)*body_sign<0:
            ids=(ids[0],ids[2],ids[1])
        m.Add(Element2D(fd,list(ids)))
    def quad(vertices,normal,fd):
        triangle((vertices[0],vertices[1],vertices[2]),normal,fd)
        triangle((vertices[0],vertices[2],vertices[3]),normal,fd)
    for sail,shape in zip(sails,shapes):
        uv,triangles,edges=_parametric_sail_grid(sail,size)
        z=uv[:,1];f=(z-FOOT)/sail[2]
        t=uv[:,0]/(sail[1]*(1-.96*f))
        grid=np.empty((len(uv),2),dtype=int)
        for side,sign in enumerate((1,-1)):
            xyz=_skin_points(sail,z,t,shape,sign)
            grid[:,side]=[node(p) for p in xyz]
        fds={part:descriptor(shape["name"],part) for part in
             ("upper","lower","leading","trailing","foot","head")}
        a=np.radians(sail[3]);port=np.array([np.sin(a),np.cos(a),0.])
        chord=np.array([-np.cos(a),np.sin(a),0.])
        for side,normal,part in ((0,port,"upper"),(1,-port,"lower")):
            for face in grid[triangles,side]:
                triangle(tuple(face),normal,fds[part])
        for part,normal in (('leading',-chord),('trailing',chord),
                            ('foot',np.array([0.,0.,-1.])),('head',np.array([0.,0.,1.]))):
            for i,j in edges[part]:
                quad((grid[i,0],grid[j,0],grid[j,1],grid[i,1]),normal,fds[part])
        print(f"Matched {shape['name']}: {len(uv)} nodes and {len(triangles)} graded triangles per skin",flush=True)
    return m


def _layer_envelopes(wall_mesh,total_height):
    """Offset matching sail faces with independent normals at feature edges.

    At a feature edge the growth vector satisfies n_i dot g = 1 on every
    incident patch. This prevents the opposite side normals of a thin sail
    from cancelling, and gives the requested normal spacing on both sides.
    The hull-waterline nodes slide within the sea plane. The core receives
    matching sea/interface vertices; the layer side faces close the sea.
    """
    from netgen.meshing import Mesh,MeshPoint,Pnt,FaceDescriptor,Element2D
    core=Mesh(dim=3);core.SetMaterial(1,"air")
    descriptors={};mapping={};layer_data=[]
    def descriptor(name):
        if name not in descriptors:
            bc=len(descriptors)+1
            descriptors[name]=core.Add(FaceDescriptor(surfnr=bc,domin=1,domout=0,bc=bc))
            core.SetBCName(bc-1,name)
        return descriptors[name]
    def add_point(p):
        return core.Add(MeshPoint(Pnt(*p))).nr
    old=np.asarray([p.p for p in wall_mesh.Points()])
    faces=defaultdict(list);patches=defaultdict(list);outer_faces=[]
    for face in wall_mesh.Elements2D():
        fd=wall_mesh.FaceDescriptor(face.index)
        ids=[v.nr-1 for v in face.vertices]
        if fd.bcname in ("hull","main","jib"):
            faces[fd.bcname].append(ids);patches[fd.bcname].append(face.index)
        else:
            outer_faces.append((fd.bcname,ids))
    for name,entries in faces.items():
        triangles=np.asarray(entries)
        used=np.unique(triangles)
        index=np.full(len(old),-1,dtype=int);index[used]=np.arange(len(used))
        tri=index[triangles];points=old[used]
        xyz=points[tri]
        cross=np.cross(xyz[:,1]-xyz[:,0],xyz[:,2]-xyz[:,0])
        sign=np.sign(np.sum(xyz[:,0]*np.cross(xyz[:,1],xyz[:,2])))
        if not sign:
            raise RuntimeError(f"Zero-volume wall surface: {name}")
        normals=sign*cross/np.linalg.norm(cross,axis=1)[:,None]
        accumulated=defaultdict(lambda:np.zeros(3))
        for j in range(3):
            v1=xyz[:,(j+1)%3]-xyz[:,j];v2=xyz[:,(j+2)%3]-xyz[:,j]
            cosine=np.sum(v1*v2,axis=1)/(np.linalg.norm(v1,axis=1)*np.linalg.norm(v2,axis=1))
            angle=np.arccos(np.clip(cosine,-1,1))
            for node,patch,normal,weight in zip(tri[:,j],patches[name],normals,angle):
                accumulated[(int(node),patch)]+=weight*normal
        constraints=defaultdict(list)
        for (node,_),normal in accumulated.items():
            constraints[node].append(normal/np.linalg.norm(normal))
        growth=np.empty_like(points)
        for node,ns in constraints.items():
            rhs=np.ones(len(ns))
            if name=='hull' and abs(points[node,2])<1e-10:
                ns=[*ns,np.array([0.,0.,1.])];rhs=np.r_[rhs,0.]
            growth[node]=np.linalg.lstsq(np.asarray(ns),rhs,rcond=None)[0]
        if not np.isfinite(growth).all() or np.linalg.norm(growth,axis=1).max()>10:
            raise RuntimeError(f"Invalid wall feature normals: {name}")
        top=points+total_height*growth
        top_ids=np.asarray([add_point(p) for p in top])
        mapping.update(zip(used,top_ids))
        fd=descriptor(name)
        for face in top_ids[tri]:
            core.Add(Element2D(fd,face.tolist()))
        # Standard positive meshio prism orientation points from wall into air.
        volume_tri=tri if sign>0 else tri[:,[0,2,1]]
        edges=np.vstack((tri[:,[0,1]],tri[:,[1,2]],tri[:,[2,0]]))
        unique,counts=np.unique(np.sort(edges,axis=1),axis=0,return_counts=True)
        boundary=unique[counts==1]
        if len(boundary) and (name!='hull' or not np.allclose(points[boundary,2],0.,atol=1e-10)):
            raise RuntimeError(f'Unexpected open wall edges: {name}')
        layer_data.append({"name":name,"points":points,"growth":growth,"top":top,
                           "triangles":tri,"volume_triangles":volume_tri,'sea_edges':boundary})
    for name,ids in outer_faces:
        for i in ids:
            if i not in mapping:
                mapping[i]=add_point(old[i])
        core.Add(Element2D(descriptor(name),[mapping[i] for i in ids]))
    return core,layer_data


def _join_layers(core_mesh,layer_data,heights):
    from scipy.spatial import cKDTree
    core_points=core_mesh.points
    tree=cKDTree(core_points)
    points=[core_points];cells=[];tags=[]
    for block,labels in zip(core_mesh.cells,core_mesh.cell_data["tag"]):
        keep=~np.isin(labels,[MARKERS[n] for n in ('hull','main','jib')]) if block.type=="triangle" else np.ones(len(block),dtype=bool)
        if keep.any():
            cells.append((block.type,block.data[keep]));tags.append(labels[keep])
    count=len(core_points)
    cumulative=np.r_[0.,np.cumsum(heights)]
    for data in layer_data:
        distance,top_ids=tree.query(data['top'])
        if distance.max()>1e-9:
            raise RuntimeError("Tetrahedral mesher moved the prism interface")
        actual=np.concatenate([block.data[labels==MARKERS[data['name']]]
                               for block,labels in zip(core_mesh.cells,core_mesh.cell_data['tag'])
                               if block.type=='triangle'])
        expected=top_ids[data['triangles']]
        if {tuple(sorted(t)) for t in actual}!={tuple(sorted(t)) for t in expected}:
            raise RuntimeError("Tetrahedral mesher changed the prism interface triangulation")
        n=len(data['points']);levels=[]
        for height in cumulative[:-1]:
            points.append(data['points']+height*data['growth'])
            levels.append(np.arange(count,count+n));count+=n
        levels.append(top_ids)
        wedges=[]
        tri=data['volume_triangles']
        for low,high in zip(levels[:-1],levels[1:]):
            wedges.append(np.column_stack((low[tri],high[tri])))
        wedges=np.concatenate(wedges)
        cells.append(('wedge',wedges));tags.append(np.ones(len(wedges),dtype=int))
        wall=levels[0][data['triangles']]
        cells.append(('triangle',wall));tags.append(np.full(len(wall),MARKERS[data['name']]))
        boundary=data.get('sea_edges',np.empty((0,2),dtype=int))
        if len(boundary):
            quads=np.concatenate([np.column_stack((low[boundary[:,0]],low[boundary[:,1]],
                                                  high[boundary[:,1]],high[boundary[:,0]]))
                                  for low,high in zip(levels[:-1],levels[1:])])
            cells.append(('quad',quads));tags.append(np.full(len(quads),MARKERS['sea']))
    return meshio.Mesh(np.concatenate(points),cells,cell_data={'tag':tags},field_data=core_mesh.field_data)


def _fill_core(surface_mesh,out,size,sail_size,wake_size,wind_angle,threads):
    """Mesh only the core, preserving every triangle on the prism interface."""
    import gmsh
    from scipy.spatial import cKDTree
    surface_path=out/'prism_envelopes.msh'
    meshio.write(surface_path,meshio.Mesh(surface_mesh.points,surface_mesh.cells,
                 cell_data={'gmsh:physical':surface_mesh.cell_data['tag'],
                            'gmsh:geometrical':surface_mesh.cell_data['tag']},
                 field_data=surface_mesh.field_data),file_format='gmsh22',binary=True)
    gmsh.initialize()
    try:
        gmsh.option.setNumber('General.NumThreads',threads)
        gmsh.open(str(surface_path))
        outer=gmsh.model.geo.addSurfaceLoop([MARKERS[n] for n in ('farfield','sea','hull')])
        main=gmsh.model.geo.addSurfaceLoop([MARKERS['main']])
        jib=gmsh.model.geo.addSurfaceLoop([MARKERS['jib']])
        fluid=gmsh.model.geo.addVolume([outer,main,jib])
        gmsh.model.geo.synchronize()
        gmsh.model.addPhysicalGroup(3,[fluid],1,'air')
        gmsh.option.setNumber('Mesh.MeshOnlyEmpty',1)
        gmsh.option.setNumber('Mesh.MeshSizeFromPoints',0)
        gmsh.option.setNumber('Mesh.MeshSizeFromCurvature',0)
        gmsh.option.setNumber('Mesh.MeshSizeExtendFromBoundary',0)
        gmsh.option.setNumber('Mesh.MeshSizeMin',.003)
        gmsh.option.setNumber('Mesh.MeshSizeMax',4.)
        gmsh.option.setNumber('Mesh.Algorithm3D',10)
        gmsh.option.setNumber('Mesh.Optimize',0)
        gmsh.option.setNumber('Mesh.OptimizeThreshold',.05)
        # Distance.PointsList (and its old NodesList alias) refers to CAD
        # point entities, not mesh vertices. Query the actual discrete surface
        # vertices instead; this also works without surface parametrizations.
        def surface_tree(names):
            ids=np.unique(np.concatenate([b.data[np.isin(labels,[MARKERS[n] for n in names])].reshape(-1)
                                          for b,labels in zip(surface_mesh.cells,surface_mesh.cell_data['tag'])]))
            return cKDTree(surface_mesh.points[ids])
        all_tree=surface_tree(('hull','main','jib'))
        sail_tree=surface_tree(('main','jib'))
        def size_at(dim,tag,x,y,z,lc):
            p=(x,y,z)
            d=all_tree.query(p)[0]
            h=size+(4.-size)*min(1.,max(0.,(d-.8)/11.2))
            d=sail_tree.query(p)[0]
            if d<.65:
                h=min(h,sail_size+(size-sail_size)*min(1.,max(0.,(d-.06)/.59)))
            return min(lc,h)
        gmsh.model.mesh.setSizeCallback(size_at)
        fields=[]
        a=np.radians(wind_angle);direction=np.array([-np.cos(a),np.sin(a),0.])
        origin=-4*direction+np.array([0.,0.,5.5])
        for length,scale in ((28.,1.),(104.,1.6)):
            axis=length*direction
            for radius,cell in ((4.,wake_size),(6.,2*wake_size),(8.,4*wake_size)):
                field=gmsh.model.mesh.field.add('Cylinder')
                for key,value in {'Radius':radius,'VIn':scale*cell,'VOut':4.,
                                  'XCenter':origin[0],'YCenter':origin[1],'ZCenter':origin[2],
                                  'XAxis':axis[0],'YAxis':axis[1],'ZAxis':axis[2]}.items():
                    gmsh.model.mesh.field.setNumber(field,key,value)
                fields.append(field)
        combined=gmsh.model.mesh.field.add('Min')
        gmsh.model.mesh.field.setNumbers(combined,'FieldsList',fields)
        gmsh.model.mesh.field.setAsBackgroundMesh(combined)
        gmsh.model.mesh.generate(3)
        gmsh.write(str(out/'tetra_core_initial.msh'))
        gmsh.model.mesh.optimize('',niter=3)
        gmsh.write(str(out/'tetra_core.msh'))
    finally:
        gmsh.model.mesh.removeSizeCallback()
        gmsh.finalize()
    core=meshio.read(out/'tetra_core.msh')
    core.cell_data={'tag':core.cell_data['gmsh:physical']}
    return core


def write_su2(path,mesh):
    """Write named SU2 markers; meshio 5.3's SU2 writer has a CellBlock bug."""
    volume_types={'tetra':10,'wedge':13,'hexahedron':12,'pyramid':14}
    surface_types={'triangle':5,'quad':9}
    path=Path(path);temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('wb') as f:
        count=sum(len(c) for c in mesh.cells if c.type in volume_types)
        f.write(f'NDIME= 3\nNELEM= {count}\n'.encode())
        for c in mesh.cells:
            if c.type in volume_types:
                np.savetxt(f,np.column_stack((np.full(len(c),volume_types[c.type]),c.data)),fmt='%d')
        f.write(f'NPOIN= {len(mesh.points)}\n'.encode())
        np.savetxt(f,mesh.points,fmt='%.16e')
        f.write(f'NMARK= {len(MARKERS)}\n'.encode())
        for name,tag in MARKERS.items():
            count=sum(int(np.count_nonzero(labels==tag)) for c,labels in zip(mesh.cells,mesh.cell_data['tag'])
                      if c.type in surface_types)
            if not count:
                raise RuntimeError(f'Empty boundary marker: {name}')
            f.write(f'MARKER_TAG= {name}\nMARKER_ELEMS= {count}\n'.encode())
            for c,labels in zip(mesh.cells,mesh.cell_data['tag']):
                if c.type in surface_types:
                    selected=c.data[labels==tag]
                    np.savetxt(f,np.column_stack((np.full(len(selected),surface_types[c.type]),selected)),fmt='%d')
    temporary.replace(path)


def check_mesh(mesh,out):
    import gmsh
    boundary_edges=np.concatenate([np.stack((c.data,np.roll(c.data,-1,axis=1)),axis=2).reshape(-1,2)
                                   for c in mesh.cells if c.type in ('triangle','quad')])
    _,edge_counts=np.unique(np.sort(boundary_edges,axis=1),axis=0,return_counts=True)
    nonmanifold=int(np.count_nonzero(edge_counts!=2))
    if nonmanifold:
        raise RuntimeError(f'Fluid boundary is not closed: {nonmanifold} nonmanifold edges')
    gmsh.initialize()
    try:
        # minDetJac checks the sign, including prisms. A small condition
        # number is expected for anisotropic layers and is not a reason to
        # discard correctly oriented wall cells.
        gmsh.option.setNumber("General.Terminal",0)
        gmsh.open(str(out/"yacht.msh"))
        _,ids,_=gmsh.model.mesh.getElements(3)
        det=gmsh.model.mesh.getElementQualities(np.concatenate(ids),"minDetJac")
        if not np.isfinite(det).all() or det.min()<=0:
            raise RuntimeError("Invalid fluid mesh: nonpositive element Jacobian")
        minimum=float(det.min())
        ids=gmsh.model.mesh.getElementsByType(4)[0]
        quality=gmsh.model.mesh.getElementQualities(ids,'minSICN')
        tetra_quality={'minimum':float(quality.min()),'p1':float(np.percentile(quality,1)),
                       'p50':float(np.median(quality)),'nonpositive':int((quality<=0).sum()),
                       'below_1e_4':int((quality<1e-4).sum()),'minimum_required':.001}
        if quality.min()<.001:
            raise RuntimeError('Unacceptable tetrahedral slivers: minimum minSICN below 0.001')
    finally:
        gmsh.finalize()
    wedges=np.concatenate([c.data for c in mesh.cells if c.type=="wedge"])
    bases=np.concatenate((wedges[:,:3],wedges[:,3:]))
    opposite=np.concatenate((wedges[:,3:],wedges[:,:3]))
    # Only first prisms touching the actual sail wall contribute here.
    lookup={tuple(sorted(face)):i for i,face in enumerate(bases)}
    layer_checks={}
    for name in ("main","jib","hull"):
        wall=np.concatenate([c.data[labels==MARKERS[name]]
                             for c,labels in zip(mesh.cells,mesh.cell_data["tag"])
                             if c.type=="triangle"])
        indices=[lookup.get(tuple(sorted(face))) for face in wall]
        if any(i is None for i in indices):
            raise RuntimeError(f"Missing first prism layer on {name}")
        indices=np.asarray(indices)
        p=mesh.points[bases[indices]]
        q=mesh.points[opposite[indices]]
        normal=np.cross(p[:,1]-p[:,0],p[:,2]-p[:,0])
        normal/=np.linalg.norm(normal,axis=1)[:,None]
        heights=abs(np.sum((q-p).mean(axis=1)*normal,axis=1))
        if heights.min()<=0 or not np.isfinite(heights).all():
            raise RuntimeError(f"Collapsed first prism layer on {name}")
        layer_checks[name]={"wall_triangles":len(wall),"first_prism_coverage":1.,
                            "first_node_normal_spacing_m":{"min":float(heights.min()),
                             "p50":float(np.median(heights)),"p95":float(np.percentile(heights,95)),
                             "max":float(heights.max())}}
        wall_points=mesh.points[wall]
        max_edge=np.linalg.norm(wall_points-np.roll(wall_points,1,axis=1),axis=2).max(axis=1)
        layer_checks[name]['surface_triangle_max_edge_m']={
            'p50':float(np.median(max_edge)),'p95':float(np.percentile(max_edge,95)),'max':float(max_edge.max())}
    return {"minimum_element_jacobian":minimum,"boundary_nonmanifold_edges":nonmanifold,
            "tetrahedral_minSICN":tetra_quality,"sail_layers":layer_checks}


def make_mesh(out,size,wind_angle,wake_size,sail_size,sails,*,first_layer=1.2e-5,layers=30,growth=1.2455979758326061,threads=4,shapes=None,domain_min=None,domain_max=None):
    from netgen.occ import Box,Pnt,Cylinder,Z,OCCGeometry,WorkPlane
    from netgen.meshing import SetMessageImportance,MeshingStep
    from pyngcore import TaskManager,SetNumThreads
    SetMessageImportance(1)
    SetNumThreads(threads)
    shapes=SHAPES if shapes is None else shapes
    lower=list(domain_min if domain_min is not None else [-95.,-45.,0.])
    upper=list(domain_max if domain_max is not None else [35.,80.,28.])
    if len(lower)!=3 or len(upper)!=3 or not np.all(np.isfinite([lower,upper])) or np.any(np.asarray(lower)>=upper):
        raise ValueError('Domain bounds must be finite, ordered 3D coordinates')
    box=Box(Pnt(*lower),Pnt(*upper));box.faces.name="farfield"
    box.faces.Min(Z).name="sea"
    stations=[(-5,.50),(-4,1.15),(-2,1.50),(0,1.42),(2,1.05),(4,.40),(5,.025)]
    outline=[(x,-b) for x,b in stations]+[(x,b) for x,b in stations[::-1]]
    wp=WorkPlane().MoveTo(*outline[0])
    for point in outline[1:]:
        wp=wp.LineTo(*point)
    hull=wp.Close().Face().Extrude(1.2).Move((0,0,-.35))
    mast=Cylinder(Pnt(.15,0,.5),Z,r=.075,h=11.5)
    body=hull+mast;body.faces.name="hull";body.faces.maxh=size
    sail_bodies=[_sail(sail,shape,sail_size) for sail,shape in zip(sails,shapes)]
    for sail_body,shape in zip(sail_bodies,shapes):
        if (sail_body*body).mass>1e-10:
            raise RuntimeError(f"{shape['name']} intersects the hull or mast")
    if (sail_bodies[0]*sail_bodies[1]).mass>1e-10:
        raise RuntimeError("The two sails intersect")
    fluid=box-body-sail_bodies[0]-sail_bodies[1]
    if len(fluid.solids)!=1:
        raise RuntimeError("CAD boolean did not produce one connected fluid domain")
    fluid.solids.name="air"
    # The sail surfaces are supplied as matching meshes below. Mesh only the
    # remaining CAD boundaries here, so narrow sail caps are not remeshed.
    geo=OCCGeometry(box-body)
    heights=[first_layer*growth**i for i in range(layers)]
    print(f"Generating smooth thin sails and {layers} prism layers; first {first_layer:g} m, total {sum(heights):.4f} m",flush=True)
    with TaskManager():
        ngmesh=geo.GenerateMesh(maxh=4.,minh=.005,grading=.3,closeedgefac=.001,
                               curvaturesafety=1,segmentsperedge=1,perfstepsend=MeshingStep.MESHSURFACE)
        ngmesh=_matched_surface(ngmesh,sails,shapes,sail_size,lower,upper)
        ngmesh.Save(str(out/"surface_before_layers.vol"))
        ngmesh,layer_data=_layer_envelopes(ngmesh,sum(heights))
        ngmesh.Save(str(out/"prism_envelopes.vol"))
        print(f"Prism envelopes ready: {len(ngmesh.Points())} nodes; filling tetrahedral air core",flush=True)
    fluid.WriteStep(str(out/"geometry.step"))
    core=_fill_core(_convert(ngmesh),out,size,sail_size,wake_size,wind_angle,threads)
    mesh=_join_layers(core,layer_data,heights)
    write_su2(out/"yacht.su2",mesh)
    # Keep a mesh with names and both prism/tet types for independent viewing.
    gm=meshio.Mesh(mesh.points,mesh.cells,
                  cell_data={"gmsh:physical":mesh.cell_data["tag"],"gmsh:geometrical":mesh.cell_data["tag"]},
                  field_data=mesh.field_data)
    meshio.write(out/"yacht.msh",gm,file_format="gmsh22",binary=True)
    counts=defaultdict(int)
    for c in mesh.cells:
        counts[c.type]+=len(c)
    if not counts.get("wedge"):
        raise RuntimeError("No prism cells generated; refusing to run SST on a tetra-only mesh")
    checks=check_mesh(mesh,out)
    geometry={"geometry_version":VERSION,"hull":outline,"sail_definitions":sails,"sail_shapes":shapes,
              "model":"Original idealised smooth thin sails; cubic CAD splines; thickness scales with chord"}
    (out/"geometry.json").write_text(json.dumps(geometry,indent=2),encoding="utf8")
    metadata={"geometry_version":VERSION,"mesh_generator":"matched sail surface grids + explicit prism layers + Gmsh tetrahedral core",
              "sail_definitions":sails,"sail_shapes":shapes,"mesh_size":size,"sail_size":sail_size,
              "wake_size":wake_size,"wind_angle":wind_angle,"domain_min":lower,"domain_max":upper,
              "nodes":len(mesh.points),"elements":counts,"tetrahedra":counts.get("tetra",0),
              "boundary_layers":{"first_node_spacing_m":first_layer,"layers":layers,"growth":growth,
                                 "requested_total_thickness_m":sum(heights)},
              "wall_treatment":{name:"resolved SST, target y+ about 1" for name in ('main','jib','hull')},"checks":checks}
    (out/"mesh_metadata.json").write_text(json.dumps(metadata,indent=2),encoding="utf8")
    print(f"Mesh passed orientation and sail-prism coverage checks: {counts}",flush=True)
