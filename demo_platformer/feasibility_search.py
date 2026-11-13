import json, random, subprocess, sys, time, copy
DIR="/Users/skimrey/Documents/Local LLMs/begin/demo_platformer/v3_tiers"
SEED=int(sys.argv[1]); BUDGET=float(sys.argv[2]); RNG=random.Random(int(sys.argv[3]) if len(sys.argv)>3 else 1)
W,H=60,20
def draw(spec):
    g=[['.']*W for _ in range(H)]
    for r,a,b in spec["platforms"]:
        for c in range(max(0,min(a,b)),min(W-1,max(a,b))+1): g[r][c]='#'
    for k,ch in (("start","S"),("goal","G")):
        r,a,b=spec["platforms"][spec[k]]
        if r<1: return None
        g[r-1][(max(0,min(a,b))+min(W-1,max(a,b)))//2]=ch
    return ["".join(x) for x in g]
def judge(grids):
    r=subprocess.run(["node","check_game.js","--judge",str(SEED)],cwd=DIR,input=json.dumps(grids),capture_output=True,text=True)
    return json.loads(r.stdout)["out"]
def cost(o):
    if any("not completable" in i or "not standing" in i for i in o["issues"]): return 50+len(o["issues"])
    return len(o["issues"])
def valid(spec):
    for r,a,b in spec["platforms"]:
        if not (1<=r<=19 and 0<=a<=b<=W-1 and b-a>=2): return False
    return True
def mutate(spec):
    s=copy.deepcopy(spec); P=s["platforms"]
    for _ in range(RNG.choice([1,1,2,3])):
        k=RNG.random(); n=len(P)
        if k<0.35 and n:
            i=RNG.randrange(n); P[i][0]+=RNG.randint(-3,3); d=RNG.randint(-6,6); P[i][1]+=d; P[i][2]+=d
        elif k<0.55 and n:
            i=RNG.randrange(n); 
            if RNG.random()<0.5: P[i][1]+=RNG.randint(-2,2)
            else: P[i][2]+=RNG.randint(-2,2)
        elif k<0.75:
            r=RNG.randint(1,17); a=RNG.randint(0,52); w=RNG.randint(3,7); P.append([r,a,a+w-1])
        elif k<0.9 and n>8:
            i=RNG.randrange(n)
            if i in (s["start"],s["goal"]): continue
            P.pop(i)
            if s["start"]>i: s["start"]-=1
            if s["goal"]>i: s["goal"]-=1
        else:
            if RNG.random()<0.5: s["goal"]=RNG.randrange(len(P))
            else: s["start"]=RNG.randrange(len(P))
        if s["start"]==s["goal"]: s["goal"]=(s["goal"]+1)%len(P)
    return s
def seed_spec():
    P=[];r=17;c=RNG.randint(2,30)
    for i in range(14):
        P.append([max(1,r),max(0,c),min(59,c+RNG.randint(3,6))])
        r-=RNG.randint(0,2); c+=RNG.randint(-7,7); c=min(52,max(0,c))
    return {"platforms":P,"start":0,"goal":len(P)-1}
best=None; t0=time.time(); log=[]
cur=seed_spec(); co=judge([draw(cur)])[0]; cc=cost(co)
bestc,bests,besto=cc,cur,co
it=0; stale=0
while time.time()-t0<BUDGET:
    it+=1
    cands=[m for m in (mutate(cur) for _ in range(150)) if valid(m) and draw(m)]
    grids=[draw(m) for m in cands]
    res=judge(grids)
    scored=sorted(zip([cost(o) for o in res],range(len(res))))
    c,i=scored[0]
    if c<=cc or RNG.random()<0.05:
        cur,cc,co=cands[i],c,res[i]
    if c<bestc: bestc,bests,besto=c,cands[i],res[i]; stale=0
    else: stale+=1
    if bestc==0: break
    if stale>60:   # restart
        cur=seed_spec(); co=judge([draw(cur)])[0]; cc=cost(co); stale=0
print(json.dumps({"seed":SEED,"iters":it,"secs":round(time.time()-t0),"best_cost":bestc,"issues":[x[:90] for x in besto["issues"]],"spec":bests,"score":besto["score"]}))
