#!/usr/bin/env python3
"""
PROTOTYPE (throwaway) — which term of the v0.1 spec moves the T1 ladders? Ablations on the 120 charts.
Reads the fitted constants from prototype_spec_v01_calibrate.full.json. Usage:
  PYTHONPATH=src python3 prototypes/prototype_spec_v01_ablation.py
Result (D_k own-pool adjacent inversions of 112 / total-D inversions of 112):
  spec 22/30 | eta~0 24/30 | no cognition 46/23 | no x,o 54/27 | j only 82/27 | eps .01 31/30 | eps .10 21/25
  | N0=0 26/30 | beta 20 28/29 | fitted 25/28.   No single term is the cause; every variant stays at 23-30
  total-D inversions.
"""
from multiprocessing import Pool
import numpy as np, prototype_spec_v01_engine as E
from scipy import stats
G={}
def init():
    G['ch']=E.load_corpus(); G['pre']=[E.preprocess(c['notes'],E.P0) for c in G['ch']]
def one(a):
    i,over=a; P=dict(E.P0,**over); c=G['ch'][i]
    r=E.evaluate(c['notes'],P,pre=G['pre'][i]); return r['skills'][E.POOL_OF[c['pool']]]['D'], r['total']['D']
FIT={k:v['fitted'] for k,v in json.load(open('prototypes/prototype_spec_v01_calibrate.full.json'))['params'].items()}
V={
 'spec':{},
 'no fatigue (eta=0)':dict(eta_f=1e-9,eta_h=1e-9,eta_g=1e-9),
 'no cognition (mu,nu,lambda_R=0)':dict(mu_p=0,mu_r=0,nu=0,lambda_R=0),
 'no x/o (same-finger only)':dict(k_ring_mid=0,k_mid_idx=0,k_ring_idx=0,k_thumb_idx=0,k_thumb_other=0,k_cross=0),
 'j only (no x/o, fatigue, cognition)':dict(k_ring_mid=0,k_mid_idx=0,k_ring_idx=0,k_thumb_idx=0,k_thumb_other=0,k_cross=0,eta_f=1e-9,eta_h=1e-9,eta_g=1e-9,mu_p=0,mu_r=0,nu=0,lambda_R=0),
 'eps 0.01':dict(eps_rc=0.01,eps_ln=0.01),
 'eps 0.10':dict(eps_rc=0.10,eps_ln=0.10),
 'N0=0':dict(N0=1e-6),
 'beta 20':dict(beta=20.0),
 'fitted':FIT,
}
if __name__=='__main__':
    ch=E.load_corpus()
    with Pool(12,initializer=init) as pool:
        for name,over in V.items():
            out=pool.map(one,[(i,over) for i in range(120)],chunksize=10)
            D=np.array([o[0] for o in out]).reshape(8,15); T=np.array([o[1] for o in out]).reshape(8,15)
            g=np.diff(np.log(np.maximum(D,0.01)),axis=1); gt=np.diff(np.log(np.maximum(T,0.01)),axis=1)
            tau=np.mean([stats.kendalltau(range(15),D[k])[0] for k in range(8)])
            tauT=np.mean([stats.kendalltau(range(15),T[k])[0] for k in range(8)])
            print(f"{name:38s} D_k inv {int((g<=0).sum()):2d} tau {tau:.3f} min-gap {g.min():+.3f} | total-D inv {int((gt<=0).sum()):2d} tau {tauT:.3f}",flush=True)
