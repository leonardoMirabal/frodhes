import numpy as np
E=2e11; rho=7850; d=0.075*0.0254; A=np.pi*d*d/4; I=np.pi*d**4/64
def modes(L, ms, s, ne=60, nm=4):
    h=L/ne; n=2*(ne+1)
    K=np.zeros((n,n)); M=np.zeros((n,n))
    k=E*I/h**3*np.array([[12,6*h,-12,6*h],[6*h,4*h*h,-6*h,2*h*h],[-12,-6*h,12,-6*h],[6*h,2*h*h,-6*h,4*h*h]])
    m=rho*A*h/420*np.array([[156,22*h,54,-13*h],[22*h,4*h*h,13*h,-3*h*h],[54,13*h,156,-22*h],[-13*h,-3*h*h,-22*h,4*h*h]])
    for e in range(ne):
        i=2*e; K[i:i+4,i:i+4]+=k; M[i:i+4,i:i+4]+=m
    node=int(round(s*ne)); M[2*node,2*node]+=ms
    K=K[2:,2:]; M=M[2:,2:]
    w,v=np.linalg.eig(np.linalg.solve(M,K)); o=np.argsort(w.real); w=w.real[o]; v=v.real[:,o]
    f=np.sqrt(w[:nm])/(2*np.pi); return f, v[:,:nm]
L=0.1524; f0=43.65
f,_=modes(L,0,1.0); print("bare L=%.4f f1=%.2f ratios"%(L,f[0]), f/f[0])
best=[]
for s in np.linspace(0.3,1.0,71):
    lo,hi=0,0.05
    fb,_=modes(L,0,s)
    if fb[0]<f0: continue
    for it in range(40):
        mid=(lo+hi)/2; fm,_=modes(L,mid,s)
        if fm[0]>f0: lo=mid
        else: hi=mid
    fm,_=modes(L,mid,s); best.append((s,mid,fm[1]/fm[0],fm[2]/fm[0],fm[3]/fm[0]))
for b in best[::5]: print("s=%.2f ms=%.2fg r2=%.2f r3=%.2f r4=%.2f"%(b[0],b[1]*1e3,b[2],b[3],b[4]))
b=min(best,key=lambda b:abs(b[2]-7.11)); print("match r2=7.11:", "s=%.3f ms=%.2fg r2=%.2f r3=%.2f"%(b[0],b[1]*1e3,b[2],b[3]))
