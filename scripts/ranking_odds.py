"""Seasonal Plackett–Luce odds with a joint Laplace posterior.

Each completed race contributes one ranking likelihood. Rounded-time ties are
partial orders, marginalized over compatible strict orders (not broken by name).
Ranks are conditioned on the observed finishers; DNF/attendance is not modeled.
"""
from itertools import permutations, product
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logsumexp
from numpy.polynomial.hermite import hermgauss


def ranking_orders(ids, times):
    groups = {}
    for identity, time in zip(ids, times):
        groups.setdefault(float(time), []).append(int(identity))
    choices = [list(permutations(group)) for _, group in sorted(groups.items())]
    count = np.prod([len(c) for c in choices])
    if count > 720:
        raise ValueError('Too many tied orderings to marginalize exactly')
    return [np.array([i for block in order for i in block], dtype=int) for order in product(*choices)]


def objective(x, races, prior_sd, hessian=False):
    n = len(x)
    value = float(x @ x / (2 * prior_sd**2))
    grad = x / prior_sd**2
    hess = np.eye(n) / prior_sd**2 if hessian else None
    for orders in races:
        values, gradients, hessians = [], [], []
        for order in orders:
            loss = 0.
            g = np.zeros(n)
            h = np.zeros((n,n)) if hessian else None
            for k in range(len(order)-1):
                ix = order[k:]
                z = x[ix]
                norm = logsumexp(z)
                p = np.exp(z-norm)
                loss += norm-z[0]
                g[ix] += p
                g[ix[0]] -= 1
                if hessian:
                    h[np.ix_(ix,ix)] += np.diag(p)-np.outer(p,p)
            values.append(loss)
            gradients.append(g)
            if hessian: hessians.append(h)
        weights = np.exp(-np.array(values)-logsumexp(-np.array(values)))
        g = weights @ np.array(gradients)
        value -= logsumexp(-np.array(values))
        grad += g
        if hessian:
            hess += np.einsum('k,kij->ij', weights, hessians)
            centered = np.array(gradients)-g
            hess -= np.einsum('k,ki,kj->ij', weights, centered, centered)
    return (value, grad, hess) if hessian else (value, grad)


def fit(races, n, prior_sd, start=None):
    if not races:
        return np.zeros(n), np.eye(n)*prior_sd**2
    result = minimize(lambda x: objective(x,races,prior_sd),
                      np.zeros(n) if start is None else start,
                      method='BFGS', jac=True, options={'gtol':1e-6, 'maxiter':300})
    if not result.success and np.linalg.norm(result.jac,ord=np.inf) > 2e-5:
        raise RuntimeError(f'Ranking fit failed: {result.message}')
    hessian = objective(result.x,races,prior_sd,hessian=True)[2]
    if np.linalg.eigvalsh(hessian).min() <= 0:
        raise RuntimeError('Nonpositive posterior precision')
    return result.x, np.linalg.inv(hessian)


def probabilities(mean, covariance):
    """E[logistic(strength_i - strength_j)] including joint uncertainty."""
    nodes, weights = hermgauss(32)
    difference = mean[:,None]-mean[None,:]
    variance = np.diag(covariance)[:,None]+np.diag(covariance)[None,:]-2*covariance
    return np.sum(expit(difference[:,:,None]+np.sqrt(2*np.maximum(variance,0))[:,:,None]*nodes)*weights,axis=2)/np.sqrt(np.pi)


def probability_interval(mean, covariance):
    difference = mean[:,None]-mean[None,:]
    variance = np.diag(covariance)[:,None]+np.diag(covariance)[None,:]-2*covariance
    # Central 90% approximate posterior interval for the underlying pair chance.
    delta = 1.6448536269514722*np.sqrt(np.maximum(variance,0))
    return expit(difference-delta), expit(difference+delta)
