"""Geometric/field conservation checks for hybrid-mesh postprocessing."""
import unittest
import json
import os
from pathlib import Path
import tempfile

import meshio
import numpy as np

from cfd.rans_mesh import SHAPES,section,entry_angle,_layer_envelopes,_convert,_join_layers
from cfd.rans_slice import horizontal_slice


class ConvergenceTests(unittest.TestCase):
    def test_snapshot_window_excludes_adjacent_and_previous_run_files(self):
        from cfd.compare_snapshots import comparison_frames
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            for iteration,modified in ((100,3000),(200,3000),(201,3000),(101,1900),(999,3000)):
                path=out/f'restart_{iteration:06d}.dat';path.write_bytes(b'frame')
                os.utime(path,(modified,modified))
            (before,_),(after,_)=comparison_frames(out,2000,201)
            self.assertEqual((before,after),(100,201))

    def test_accurate_final_field_is_not_rejected_for_a_better_initial_guess(self):
        from cfd.rans_checks import convergence
        names=['Inner_Iter','rms[P]','rms[U]','rms[V]','rms[W]',
               'rms[k]','rms[w]','CFx','CFy']
        history=np.zeros(200,dtype=[(name,float) for name in names])
        history['Inner_Iter']=np.arange(200)
        for name in names[1:5]:history[name]=-8.
        history['rms[k]']=-11.5;history['rms[w]']=-5.5
        history['CFx']=.2;history['CFy']=1.
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            for reference in ({'rms[k]':-3.,'rms[w]':2.},
                              {'rms[k]':-10.,'rms[w]':-5.}):
                (out/'residual_reference.json').write_text(json.dumps(reference))
                self.assertTrue(convergence(out,history)['passed'])
            # Absolute tolerances must not conceal a genuinely large error.
            history['rms[k]'][-1]=-7.
            self.assertFalse(convergence(out,history)['passed'])


class SliceTests(unittest.TestCase):
    points=np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],
                     [0,0,1],[1,0,1],[1,1,1],[0,1,1]],dtype=float)

    def check(self,kind,cells,height,area=1.):
        mesh=meshio.Mesh(self.points,[(kind,cells)])
        # A known affine velocity and scalar field must remain exact after
        # interpolation, independently of cell shape and slice triangulation.
        def field(p):
            x,y,z=p.T
            return np.column_stack((x+2*y+3*z,-x+y,2*x-3*z,1+2*x+4*y+8*z))
        xy,values,tri=horizontal_slice(mesh,field(self.points),height)
        expected=field(np.column_stack((xy,np.full(len(xy),height))))
        np.testing.assert_allclose(values,expected,atol=1e-12)
        p=xy[tri];ab=p[:,1]-p[:,0];ac=p[:,2]-p[:,0]
        measured=.5*abs(ab[:,0]*ac[:,1]-ab[:,1]*ac[:,0]).sum()
        self.assertAlmostEqual(measured,area,places=12)
        # Shared edges use shared vertices, allowing reliable interpolation.
        import matplotlib.tri as mtri
        mtri.Triangulation(xy[:,0],xy[:,1],tri).get_trifinder()

    def test_hexahedron(self):
        self.check("hexahedron",[list(range(8))],.25)

    def test_two_prisms(self):
        self.check("wedge",[[0,1,2,4,5,6],[0,2,3,4,6,7]],.25)

    def test_tetrahedra_and_exact_node_planes(self):
        cells=[[0,1,2,6],[0,2,3,6],[0,3,7,6],[0,7,4,6],[0,4,5,6],[0,5,1,6]]
        for height in (0.,.25,1.):
            with self.subTest(height=height):
                self.check("tetra",cells,height)

    def test_pyramid(self):
        self.points=np.r_[self.points,[[.5,.5,1.]]]
        self.check("pyramid",[[0,1,2,3,8]],.25,area=.75**2)


class SailTests(unittest.TestCase):
    def test_thickness_scales_with_chord(self):
        sail=(3.4,2.8,8.5,20.)
        shape=SHAPES[1]
        for f in (0.,.5,1.):
            p=section(sail,1.15+f*sail[2],shape,n=97)
            upper,lower=p[:97],p[97:][::-1]
            thickness=np.linalg.norm(upper-lower,axis=1).max()
            self.assertAlmostEqual(thickness/(sail[1]*(1-.96*f)),shape['thickness_ratio'],places=12)

    def test_entry_and_twist(self):
        sail=(3.4,2.8,8.5,20.)
        shape=SHAPES[1]
        expected=20+np.degrees(np.arctan(4*.08*.95))
        self.assertAlmostEqual(entry_angle(sail,4,shape),expected,places=12)
        self.assertAlmostEqual(entry_angle(sail,9.65,shape)-entry_angle(sail,1.15,shape),shape['twist_deg'],places=12)




class WaterlineLayerTests(unittest.TestCase):
    def test_box_expansion_conserves_volume_and_closes_waterline(self):
        from netgen.meshing import Mesh,MeshPoint,Pnt,FaceDescriptor,Element2D
        points=SliceTests.points*np.array([2.,2.,2.])+[-1.,-1.,0.]
        wall=Mesh(dim=3);wall.SetMaterial(1,'air')
        for p in points:wall.Add(MeshPoint(Pnt(*p)))
        # Open bottom at the sea plane; distinct flat CAD face patches.
        faces=((1,2,6,5),(0,4,7,3),(0,1,5,4),(3,7,6,2),(4,5,6,7))
        for i,face in enumerate(faces,1):
            fd=wall.Add(FaceDescriptor(surfnr=i,domin=1,domout=0,bc=i))
            wall.SetBCName(i-1,'hull')
            a,b,c,d=np.array(face)+1
            wall.Add(Element2D(fd,[a,b,c]));wall.Add(Element2D(fd,[a,c,d]))
        h=.1
        core,data=_layer_envelopes(wall,h)
        result=_join_layers(_convert(core),data,[h/3,2*h/3])
        wedges=np.concatenate([c.data for c in result.cells if c.type=='wedge'])
        volume=0.
        for indices in ((0,1,2,3),(1,2,3,4),(2,3,4,5)):
            p=result.points[wedges[:,indices]]
            determinant=np.linalg.det(p[:,1:]-p[:,0,None,:])
            self.assertTrue((determinant>0).all())
            volume+=determinant.sum()/6
        self.assertAlmostEqual(volume,(2+2*h)**2*(2+h)-8,places=12)
        sea=np.concatenate([c.data for c in result.cells if c.type=='quad'])
        p=result.points[sea]
        np.testing.assert_allclose(p[:,:,2],0.,atol=1e-14)
        area=0.
        for ids in ((0,1,2),(0,2,3)):
            q=p[:,ids];area+=np.linalg.norm(np.cross(q[:,1]-q[:,0],q[:,2]-q[:,0]),axis=1).sum()/2
        self.assertAlmostEqual(area,(2+2*h)**2-4,places=12)


if __name__=="__main__":
    unittest.main()
