import numpy as np
exec(open('pick.py').read().split("N=1024")[0])   # g, phi_ref with Lh=1,Rp=1,Lv=-0.2 (gap units)
Lv0=-0.2
# radial flux: Phi_r(rho)=Phi(u=rho) with Lv removed (Phi even in u=q+Lv)
def g0(u):
    f1=(u-Rp)**2+Lh**2; f2=(u+Rp)**2+Lh**2
    return (f1-2*Lh**2)/f1**2-(f2-2*Lh**2)/f2**2
SMAX=64.0; N=1024
s_t=np.linspace(0,SMAX,N+1); rho=np.sqrt(s_t)
D=np.where(rho>1e-9, g0(rho)/(2*np.maximum(rho,1e-9)), 0); 
eps=1e-6; D[0]=(g0(eps)/(2*eps))
Dq=np.round(D/np.abs(D).max()*32767).astype(np.int64); Ds=np.abs(D).max()/32767
print("D(end)/max = %.2e"%(abs(D[-1])/np.abs(D).max()))
Q=2**24
def run(x,h=None):
    xi=np.round(x*Q).astype(np.int64); hi=np.zeros_like(xi) if h is None else np.round(h*Q).astype(np.int64)
    off=int(round(Lv0*Q)); u=xi+off
    s=(u*u+hi*hi)>>24
    ds=((u[1:]-u[:-1])*(u[1:]+u[:-1])+(hi[1:]-hi[:-1])*(hi[1:]+hi[:-1]))>>24
    sm=(s[1:]+s[:-1])>>1; idx=np.minimum(sm>>20,N-1); fr=sm&((1<<20)-1)
    Dl=Dq[idx]+(((Dq[idx+1]-Dq[idx])*fr)>>20)
    return Dl*ds/Q*Ds
fs=44100
for f0 in (41.2,440.0,2093.0):
  for A,lab in ((1.5,'hard'),(1.5*10**(-30/20),'soft')):
    n=np.arange(fs); x=A*np.sin(2*np.pi*f0*n/fs)*np.exp(-n/fs)
    ref=np.diff(phi_ref(x)); y=run(x)
    # reference here uses Phi(q) whose g has Lv baked: same geometry
    print("s-form f0=%7.1f %s SNR vs ADAA ref = %5.1f dB"%(f0,lab,10*np.log10(np.sum(ref**2)/np.sum((y-ref)**2))))
