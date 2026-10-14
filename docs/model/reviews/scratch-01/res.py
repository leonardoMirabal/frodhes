import math, numpy as np
fs=44100
def run(kind,f,T60,amp,n):
    th=2*math.pi*f/fs; r=math.exp(-6.907755/(T60*fs)); Q=1<<30
    out=np.zeros(n)
    if kind=='DF':
        a1=round(2*r*math.cos(th)*Q); a2=round(-r*r*Q)
        y1=amp; y2=round(amp*r*math.cos(th))  # start ~ cos phase
        for i in range(n):
            y=(a1*y1+a2*y2)>>30; y2=y1; y1=y; out[i]=y
    elif kind=='MC3':   # x+=e*y ; y=d*y-e*x   poles r e^{jth}
        d=r*r; e=math.sqrt(1+d-2*r*math.cos(th)); E=round(e*Q); D=round(d*Q)
        x=0; y=amp
        for i in range(n):
            x=x+((E*y)>>30); y=((D*y)>>30)-((E*x)>>30); out[i]=x
    elif kind=='MCB':   # undamped magic circle + per-16 block decay
        e=2*math.sin(th/2); E=round(e*Q); G=round(r**16*Q); x=0; y=amp
        for i in range(n):
            x=x+((E*y)>>30); y=y-((E*x)>>30); out[i]=x
            if i%16==15: x=(G*x)>>30; y=(G*y)>>30
    return out
def analyse(o,f):
    n=len(o); t=np.arange(n)/fs
    # analytic signal via FFT
    X=np.fft.fft(o); X[n//2+1:]=0; X[1:n//2]*=2; a=np.fft.ifft(X)
    sl=slice(int(0.05*fs),int(0.6*fs)); ph=np.unwrap(np.angle(a[sl])); env=20*np.log10(np.abs(a[sl]))
    fe=np.polyfit(t[sl],ph,1)[0]/(2*np.pi); sl2=np.polyfit(t[sl],env,1)[0]
    return 1200*math.log2(fe/f), -60/sl2
for f,T in ((41.2,20.0),(2637.0,1.0)):
  for kind in ('DF','MC3','MCB'):
    for amp,lab in ((1<<28,'0 dB'),(1<<28>>10,'-60 dB')):
        n=int(fs*2.0); o=run(kind,f,T,amp,n)
        # float reference with same structure-free ideal: fit sinusoid; noise = residual after removing ideal decaying sinusoid fit
        c,t60=analyse(o,f)
        tt=np.arange(n); r=math.exp(-6.907755/(T*fs)); th=2*math.pi*f/fs
        B=np.stack([r**tt*np.cos(th*tt), r**tt*np.sin(th*tt)],1); co,_,_,_=np.linalg.lstsq(B,o,rcond=None); res=o-B@co
        snr=10*np.log10(np.sum((B@co)**2)/np.sum(res**2))
        print("%7.1f Hz %-4s %-7s cents=%+.4f T60=%.3f s (target %.1f)  SNR=%.1f dB"%(f,kind,lab,c,t60,T,snr))
