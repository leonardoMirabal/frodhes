# SPDX-License-Identifier: GPL-3.0-only
"""the host engine's WHURLI level (the scan's LEV measure, LUFS 0..1 s) of keys 33..96 at the given velocities, to a JSON file:
tools/reed_measure_levels.py 32,40,64 OUT.json  (tools/reed_levelmap.py runs it)"""
import json
import os
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tests", "py"))
import type_scan as ts
VELS=[int(v) for v in sys.argv[1].split(',')]; OUT=sys.argv[2]
KEYS=list(range(33,97))
def one(v):
    binp=ts.build_bin("one"); ty=ts.resolve_type(6)
    spec=dict(kind="keys",label="k",ty=ty.dump(),bin=binp,vel=v,keys=KEYS,alikeys=[])
    rows,data=ts.case_keys(spec)
    return v,{k:data["keys"][k]["lufs"] for k in KEYS}
if __name__=="__main__":
    ts.build_bin("one")
    with Pool(2) as p: res=dict(p.map(one,VELS))
    json.dump({str(v):{str(k):float(x) for k,x in d.items()} for v,d in res.items()},open(OUT,'w'),indent=0)
    for v in VELS:
        d=res[v]; print('v',v,' '.join(f"{k}:{d[k]:.1f}" for k in (33,40,47,52,53,60,74,75,84,90,96)),'| max step %.2f'%max(abs(d[k+1]-d[k]) for k in KEYS[:-1]))
