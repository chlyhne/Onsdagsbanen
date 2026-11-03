import sys
from pathlib import Path
import unittest
import numpy as np
from scipy.optimize import approx_fprime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ranking_odds import fit, probabilities, probability_interval, ranking_orders, objective


class RankingTests(unittest.TestCase):
    def test_prior_is_symmetric(self):
        mu,cov=fit([],4,2)
        np.testing.assert_allclose(probabilities(mu,cov),.5,atol=1e-12)

    def test_consistent_winner_and_joint_uncertainty(self):
        races=[ranking_orders([0,1,2],[1,2,3]) for _ in range(16)]
        mu,cov=fit(races,3,2)
        p=probabilities(mu,cov)
        self.assertGreater(p[0,1],.9)
        np.testing.assert_allclose(p+p.T,1,atol=1e-12)
        np.testing.assert_allclose(probabilities(mu+100,cov+5),p,atol=1e-12)
        low,high=probability_interval(mu,cov)
        self.assertTrue(np.all(low<=high))

    def test_ties_are_marginalized_and_order_independent(self):
        a=ranking_orders([0,1,2],[1,1,2]);b=ranking_orders([1,0,2],[1,1,2])
        self.assertEqual(len(a),2)
        mu,cov=fit([a]*6,3,2);mu2,cov2=fit([b]*6,3,2)
        np.testing.assert_allclose(mu,mu2,atol=1e-8)
        self.assertAlmostEqual(probabilities(mu,cov)[0,1],.5)

    def test_derivatives_with_tied_partial_order(self):
        races=[ranking_orders([0,1,2],[1,1,2]),ranking_orders([0,1,2],[2,1,3])]
        x=np.array([.2,-.5,.8])
        value,g,h=objective(x,races,2,hessian=True)
        ng=approx_fprime(x,lambda v:objective(v,races,2)[0],1e-6)
        nh=np.column_stack([(objective(x+np.eye(3)[i]*1e-5,races,2)[1]-g)/1e-5 for i in range(3)])
        np.testing.assert_allclose(g,ng,atol=1e-5)
        np.testing.assert_allclose(h,nh,atol=1e-5)

    def test_no_false_certainty_from_missing_boat(self):
        mu,cov=fit([ranking_orders([0,1],[1,2])]*8,3,2)
        self.assertAlmostEqual(mu[2],0)
        self.assertAlmostEqual(cov[2,2],4)


if __name__=='__main__':unittest.main()
