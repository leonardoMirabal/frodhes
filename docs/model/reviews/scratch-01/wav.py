import sys, struct, cmath, math
for p in sys.argv[1:]:
    d=open(p,'rb').read(); n=len(d)//2
    x=struct.unpack('>%dh'%n,d)
    print(p,n,'min',min(x),'max',max(x),'first8',x[:8])
    mx=max(abs(v) for v in x)
    tot=sum(v*v for v in x)
    hs=[]
    for k in range(0,16):
        c=sum(x[i]*cmath.exp(-2j*math.pi*k*i/n) for i in range(n))/n
        hs.append((k,round(2*abs(c)/mx if k else abs(c)/mx,4), round(math.degrees(cmath.phase(c)),1)))
    print(hs)
    if n==256: print(list(x))
