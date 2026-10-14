import re,numpy as np
exec(open('fe.py').read().split("L=0.1524")[0])
Ls=[float(x) for x in re.findall(r"([0-9.]+)f",open('/Users/leo/Desktop/dev/mwave-f1/frodhes/docs/model/research/sources/02-sonderbo-thesis/repo/EPiano/JUCE/Source/TineLengths.h').read())]
print(len(Ls))
def solve(L,f0,s):
    fb,_=modes(L,0,s,ne=40)
    if fb[0]<f0: return None
    lo,hi=0,0.1
    for it in range(36):
        mid=(lo+hi)/2; fm,_=modes(L,mid,s,ne=40)
        if fm[0]>f0: lo=mid
        else: hi=mid
    fm,v=modes(L,mid,s,ne=40); return mid,fm/fm[0]
for midi in [28,29,40,52,64,69,76,81,88,96,100]:
    L=Ls[midi-28]; f0=440*2**((midi-69)/12)
    out=[]
    for s in (0.65,0.95):
        r=solve(L,f0,s)
        out.append("s=%.2f: %s"%(s,"bare too low" if r is None else "m=%.2fg r=%.2f/%.2f"%(r[0]*1e3,r[1][1],r[1][2])))
    fb,_=modes(L,0,1,ne=40)
    print(midi,"L=%.4f f0=%.1f bare=%.1f |"%(L,f0,fb[0])," | ".join(out))
