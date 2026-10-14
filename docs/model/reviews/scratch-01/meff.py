import numpy as np
exec(open('fe.py').read().split("L=0.1524")[0])
def meff(L,ms,s,xs,ne=60):
    h=L/ne; n=2*(ne+1)
    K=np.zeros((n,n)); M=np.zeros((n,n))
    k=E*I/h**3*np.array([[12,6*h,-12,6*h],[6*h,4*h*h,-6*h,2*h*h],[-12,-6*h,12,-6*h],[6*h,2*h*h,-6*h,4*h*h]])
    m=rho*A*h/420*np.array([[156,22*h,54,-13*h],[22*h,4*h*h,13*h,-3*h*h],[54,13*h,156,-22*h],[-13*h,-3*h*h,-22*h,4*h*h]])
    for e in range(ne):
        i=2*e; K[i:i+4,i:i+4]+=k; M[i:i+4,i:i+4]+=m
    M[2*int(round(s*ne)),2*int(round(s*ne))]+=ms
    K=K[2:,2:]; M=M[2:,2:]
    w,v=np.linalg.eig(np.linalg.solve(M,K)); o=np.argsort(w.real); v=v.real[:,o]; w=w.real[o]
    out=[]
    for j in range(3):
        phi=v[:,j]; mm=phi@M@phi; js=2*int(round(xs*ne))-2; jt=2*ne-2
        out.append((np.sqrt(w[j])/2/np.pi, mm/phi[js]**2*1e3, mm/phi[jt]**2*1e3))
    return out
for (lab,L,ms,s,xs) in (("E1",0.1564,2.45e-3,0.65,0.36),("E1 spring@0.95",0.1564,0.78e-3,0.95,0.36),("A4",0.0508,0.41e-3,0.65,0.3)):
    print(lab, ["f=%.0fHz m_eff(strike)=%.1fg m_eff(tip)=%.2fg"%t for t in meff(L,ms,s,xs)])
