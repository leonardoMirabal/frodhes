import numpy as np
exec(open('pick.py').read().split("N=1024")[0])
fs=44100
def alias_ratio(y,f0):
    n=len(y); t=np.arange(n)/fs; K=int((fs/2)//f0)
    B=np.concatenate([np.stack([np.cos(2*np.pi*k*f0*t),np.sin(2*np.pi*k*f0*t)],1) for k in range(1,K+1)],1)
    c,_,_,_=np.linalg.lstsq(B,y,rcond=None); r=y-B@c
    return 10*np.log10(np.sum(r**2)/np.sum((B@c)**2))
for f0 in (523.25,1046.5,2093.0,2637.0):
  for A in (0.5,1.0,1.5):
    n=np.arange(4410); x=A*np.sin(2*np.pi*f0*n/fs)
    adaa=np.diff(phi_ref(x)); 
    xm=(x[1:]+x[:-1])/2; naive=g(x[1:])*np.diff(x)
    # 2x oversampled ADAA then halfband-ish decimation (FIR windowed sinc 63 taps)
    n2=np.arange(8820); x2=A*np.sin(2*np.pi*f0*n2/(2*fs)); y2=np.diff(phi_ref(x2))
    h=np.sinc(np.arange(-31,32)/2)/2*np.hanning(63); y2f=np.convolve(y2,h,'same')[::2]
    print("f0=%6.1f A=%.1fmm  naive G*dx: %6.1f dB   ADAA(Phi diff): %6.1f dB   2x ADAA+FIR: %6.1f dB"%(f0,A,alias_ratio(naive[100:-100],f0),alias_ratio(adaa[100:-100],f0),alias_ratio(y2f[100:-100],f0)))
print("--- sampled true derivative G(x)*xdot/fs and midpoint G(xm)*dx")
for f0 in (523.25,1046.5,2093.0):
  for A in (0.5,1.5):
    n=np.arange(4410); x=A*np.sin(2*np.pi*f0*n/fs); xd=A*2*np.pi*f0/fs*np.cos(2*np.pi*f0*n/fs)
    tru=g(x)*xd; xm=(x[1:]+x[:-1])/2; mid=g(xm)*np.diff(x)
    print("f0=%6.1f A=%.1f true-deriv: %6.1f dB  midpoint: %6.1f dB"%(f0,A,alias_ratio(tru[100:-100],f0),alias_ratio(mid[100:-100],f0)))
