import numpy as np
src=open('sform.py').read()
exec(src.split("fs=44100\nfor f0")[0].replace("SMAX=64.0; N=1024","import sys\nSMAX=float(sys.argv[1]); N=int(sys.argv[2])"))
SH=int(np.log2(SMAX*2**24/N))
def run2(x):
    xi=np.round(x*Q).astype(np.int64); off=int(round(Lv0*Q)); u=xi+off
    s=(u*u)>>24; ds=((u[1:]-u[:-1])*(u[1:]+u[:-1]))>>12      # Q36
    sm=(s[1:]+s[:-1])>>1; idx=np.minimum(sm>>SH,N-1); fr=sm&((1<<SH)-1)
    Dl=Dq[idx]+(((Dq[idx+1]-Dq[idx])*fr)>>SH)
    return (Dl*ds)/2**36*Ds
fs=44100
res=[]
for f0 in (41.2,440.0,2093.0):
  for A in (1.5,1.5*10**(-30/20)):
    n=np.arange(fs); x=A*np.sin(2*np.pi*f0*n/fs)*np.exp(-n/fs)
    ref=np.diff(phi_ref(x)); y=run2(x); res.append(10*np.log10(np.sum(ref**2)/np.sum((y-ref)**2)))
print("SMAX=%g N=%d D(end)/max=%.1e SNR(41h,41s,440h,440s,2093h,2093s)="%(SMAX,N,abs(D[-1])/np.abs(D).max()),np.round(res,1))
