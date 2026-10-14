import numpy as np
Lh,Lv,Rp=1.0,-0.2,1.0   # mm, R03 code geometry for A4
def g(q):
    u=q+Lv; f1=(u-Rp)**2+Lh**2; f2=(u+Rp)**2+Lh**2
    return (f1-2*Lh**2)/f1**2-(f2-2*Lh**2)/f2**2
W=4.0
qq=np.linspace(-W,W,2**20+1); G=g(qq); Phi=np.concatenate([[0],np.cumsum((G[1:]+G[:-1])/2*np.diff(qq))])
def phi_ref(x): return np.interp(x,qq,Phi)
N=1024; xt=np.linspace(-W,W,N+1)
Pt=phi_ref(xt); Gt=g(xt)
Pq15=np.round(Pt/np.abs(Pt).max()*32767); Pscale=np.abs(Pt).max()/32767
Gq15=np.round(Gt/np.abs(Gt).max()*32767); Gscale=np.abs(Gt).max()/32767
Pi32=np.round(Pt/np.abs(Pt).max()*(2**30)); P32s=np.abs(Pt).max()/2**30
def lerp(tab,x):
    p=(x+W)/(2*W)*N; i=np.clip(np.floor(p).astype(int),0,N-1); f=p-i
    return tab[i]+(tab[i+1]-tab[i])*f
fs=44100
for f0 in (41.2,440.0,2093.0):
  for A,lab in ((1.5,'hard'),(1.5*10**(-30/20),'soft')):
    n=np.arange(int(fs*1.0)); x=A*np.sin(2*np.pi*f0*n/fs)*np.exp(-n/fs)
    ref=np.diff(phi_ref(x))
    # A: plan: x in Q15 over window, Phi Q15 table lerp with the fraction Q15 x gives
    xq=np.round(x/W*32767)/32767*W
    yA=np.diff(np.round(lerp(Pq15,xq)))*Pscale
    yB=np.diff(np.round(lerp(Pi32,x)))*P32s
    xm=(x[1:]+x[:-1])/2; yC=np.round(lerp(Gq15,xm))*Gscale*np.diff(x)
    snr=lambda y:10*np.log10(np.sum(ref**2)/np.sum((y-ref)**2))
    print("f0=%7.1f %s  A(Q15 x,Q15 Phi)=%5.1f dB  B(int32 Phi lerp)=%5.1f dB  C(G-table midpoint)=%5.1f dB"%(f0,lab,snr(yA),snr(yB),snr(yC)))
# shape check: two spikes
x=1.5*np.sin(2*np.pi*np.arange(4410)/441.0); y=np.diff(phi_ref(x))
print("peaks/period (>30% max):", np.sum((y[1:-1]>y[:-2])&(y[1:-1]>y[2:])&(np.abs(y[1:-1])>0.3*np.abs(y).max()))/10, "max/min ratio", y.max()/-y.min())
print("--- Hermite Phi (int32 Phi + Q15 G tables)")
h=2*W/N
def herm(x):
    p=(x+W)/(2*W)*N; i=np.clip(np.floor(p).astype(int),0,N-1); t=p-i
    P0=Pi32[i]*P32s; P1=Pi32[i+1]*P32s; G0=Gq15[i]*Gscale*h; G1=Gq15[i+1]*Gscale*h
    return (2*t**3-3*t**2+1)*P0+(t**3-2*t**2+t)*G0+(-2*t**3+3*t**2)*P1+(t**3-t**2)*G1
for f0 in (41.2,440.0,2093.0):
  for A,lab in ((1.5,'hard'),(1.5*10**(-30/20),'soft')):
    n=np.arange(int(fs*1.0)); x=A*np.sin(2*np.pi*f0*n/fs)*np.exp(-n/fs)
    ref=np.diff(phi_ref(x)); yD=np.diff(herm(x))
    print("f0=%7.1f %s D(Hermite)=%5.1f dB"%(f0,lab,10*np.log10(np.sum(ref**2)/np.sum((yD-ref)**2))))
for lv in (-0.2,0.0,-0.5):
    Lv=lv; x=1.5*np.sin(2*np.pi*np.arange(441)/441.0); xx=np.concatenate([x,x[:2]])
    y=g(xx[1:-1])*np.cos(2*np.pi*np.arange(1,442)/441.0)
    pk=[y[i] for i in range(1,len(y)-1) if y[i]>y[i-1] and y[i]>y[i+1] and y[i]>0.2*y.max()]
    sp=np.abs(np.fft.rfft(y)); print("Lv=%.1f positive peaks/period:"%lv, np.round(np.array(pk)/max(pk),2), " H1/H2 (dB) %.1f"%(20*np.log10(sp[1]/sp[2])))
