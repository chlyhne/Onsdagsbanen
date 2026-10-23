"""Intersect conforming linear tetrahedra/prisms/pyramids/hexahedra with z."""
from __future__ import annotations

import numpy as np

EDGES={
    "tetra":((0,1),(0,2),(0,3),(1,2),(1,3),(2,3)),
    "wedge":((0,1),(1,2),(2,0),(3,4),(4,5),(5,3),(0,3),(1,4),(2,5)),
    "pyramid":((0,1),(1,2),(2,3),(3,0),(0,4),(1,4),(2,4),(3,4)),
    "hexahedron":((0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)),
}


def horizontal_slice(mesh,fields,height):
    """Return shared intersection vertices, linearly sampled fields and triangles.

    Reuse edge intersections across cells; never fill solid holes with a fresh
    Delaunay triangulation. Include nodes on the plane and remove coincident
    cell faces, including when the plane lies exactly on a layer interface.
    """
    coordinates=[];values=[];triangles=[]
    edge_points={};seen_triangles=set()
    z=mesh.points[:,2]
    fields=np.asarray(fields)
    def point(ia,ib):
        if z[ia]==height:
            key=(ia,ia)
        elif z[ib]==height:
            key=(ib,ib)
        else:
            key=tuple(sorted((int(ia),int(ib))))
        if key not in edge_points:
            index=len(coordinates)
            edge_points[key]=index
            i,j=key
            f=0 if i==j else (height-z[i])/(z[j]-z[i])
            coordinates.append(mesh.points[i,:2]+f*(mesh.points[j,:2]-mesh.points[i,:2]))
            values.append(fields[i]+f*(fields[j]-fields[i]))
        return edge_points[key]
    for block in mesh.cells:
        if block.type not in EDGES:
            continue
        cells=block.data
        on_plane=(z[cells].min(axis=1)<=height)&(z[cells].max(axis=1)>=height)
        for cell in cells[on_plane]:
            local=set()
            for i,j in EDGES[block.type]:
                ia,ib=cell[i],cell[j]
                za,zb=z[ia]-height,z[ib]-height
                if za==0:
                    local.add(point(ia,ia))
                if zb==0:
                    local.add(point(ib,ib))
                if za*zb<0:
                    local.add(point(ia,ib))
            if len(local)<3:
                continue
            local=np.array(sorted(local))
            xy=np.asarray([coordinates[i] for i in local])
            d=xy-xy.mean(axis=0)
            ordered=local[np.argsort(np.arctan2(d[:,1],d[:,0]))].tolist()
            # Deterministic fan diagonal for a face shared by two cells.
            start=ordered.index(min(ordered))
            ordered=ordered[start:]+ordered[:start]
            for i in range(1,len(ordered)-1):
                tri=(ordered[0],ordered[i],ordered[i+1])
                key=tuple(sorted(tri))
                if key in seen_triangles:
                    continue
                p=np.asarray([coordinates[j] for j in tri])
                ab,ac=p[1]-p[0],p[2]-p[0]
                if abs(ab[0]*ac[1]-ab[1]*ac[0])<1e-20:
                    continue
                seen_triangles.add(key)
                triangles.append(tri)
    if not triangles:
        raise ValueError("No fluid cells intersect the requested height")
    return np.asarray(coordinates),np.asarray(values),np.asarray(triangles,dtype=np.int64)
