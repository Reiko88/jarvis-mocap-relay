import os,json,uuid,time,secrets,shutil,subprocess
from fastapi import FastAPI,Request,UploadFile,File,Form,HTTPException
from fastapi.responses import HTMLResponse,FileResponse,JSONResponse,RedirectResponse,Response
KEY=os.environ.get("ACCESS_KEY","").strip(); WT=os.environ.get("WORKER_TOKEN","")
J="/tmp/jobs"; os.makedirs(J,exist_ok=True); H=os.path.dirname(os.path.abspath(__file__))
app=FastAPI(); LAST={"seen":0}
def chk(k):
    if not KEY or not secrets.compare_digest((k or "").strip(),KEY): raise HTTPException(403,"Wrong or missing access key")
def wchk(r:Request):
    if not WT or not secrets.compare_digest(r.headers.get("X-Worker",""),WT): raise HTTPException(403)
def jd(j): return os.path.join(J,os.path.basename(j))
def st(j,**k):
    p=os.path.join(jd(j),"status.json"); d=json.load(open(p)) if os.path.exists(p) else {}
    d.update(k); json.dump(d,open(p,"w")); return d
def guard(p):
    r=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=avg_frame_rate,r_frame_rate,width,height","-of","json",p],capture_output=True,text=True)
    try: s=json.loads(r.stdout)["streams"][0]
    except Exception: return "Can't read the video stream"
    def rate(x):
        n,d=(x.split('/')+['1'])[:2]
        try: return float(n)/float(d) if float(d) else 0
        except ValueError: return 0
    f=rate(s.get("avg_frame_rate","0/1")) or rate(s.get("r_frame_rate","0/1"))
    if f<59.9: return f"REJECTED: video is {f:.2f} fps. Only 60 fps (or higher) videos are allowed for mocap."
    w,h=int(s.get("width") or 0),int(s.get("height") or 0)
    if min(w,h)<1080: return f"REJECTED: video is {w}x{h}. Only 1080p (short side 1080) or higher is allowed."
@app.exception_handler(HTTPException)
async def he(r,e): return JSONResponse({"error":e.detail},status_code=e.status_code)
@app.get("/health")
def health(): return {"ok":True,"worker_seen_s_ago":round(time.time()-LAST["seen"]) if LAST["seen"] else None}
@app.get("/")
def index(request:Request,desktop:str=""):
    ua=request.headers.get("user-agent","")
    if desktop!="1" and any(t in ua for t in ("Android","Mobile","iPhone")): return RedirectResponse("/m",302)
    return HTMLResponse(open(f"{H}/index.html").read())
@app.get("/m")
def m(): return HTMLResponse(open(f"{H}/mobile.html").read())
@app.get("/manifest.webmanifest")
def mf(): return FileResponse(f"{H}/manifest.webmanifest",media_type="application/manifest+json")
@app.get("/icon.png")
def ic(): return FileResponse(f"{H}/icon.png")
@app.get("/icon192.png")
def ic2(): return FileResponse(f"{H}/icon192.png")
@app.get("/app.apk")
def apk():
    p=f"{H}/app.apk"
    if not os.path.exists(p): raise HTTPException(404,"APK not built yet")
    return FileResponse(p,media_type="application/vnd.android.package-archive",filename="jarvis-mocap.apk")
@app.get("/sw.js")
def sw(): return Response("self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('fetch',e=>{});",headers={"Service-Worker-Allowed":"/"},media_type="application/javascript")
@app.get("/characters")
def chars(key:str=""):
    chk(key); return json.load(open(f"{H}/characters.json"))
@app.post("/upload")
async def upload(key:str=Form(""),video:UploadFile=File(None),character:str=Form("none"),character_file:UploadFile=File(None)):
    chk(key)
    if not video: raise HTTPException(400,"No video file")
    j=uuid.uuid4().hex[:10]; d=jd(j); os.makedirs(d)
    ext=(os.path.splitext(video.filename or "")[1].lower() or ".mp4")[:6]; v=f"{d}/input{ext}"
    with open(v,"wb") as f: shutil.copyfileobj(video.file,f)
    e=guard(v)
    if e: shutil.rmtree(d); raise HTTPException(400,e)
    meta={"video":os.path.basename(v),"character":character}
    if character_file and character_file.filename:
        ce=os.path.splitext(character_file.filename)[1].lower()
        if ce not in (".blend",".fbx",".glb",".gltf"): shutil.rmtree(d); raise HTTPException(400,"Character must be .fbx, .glb or .blend")
        with open(f"{d}/char{ce}","wb") as f: shutil.copyfileobj(character_file.file,f)
        meta["character_file"]=f"char{ce}"
    json.dump(meta,open(f"{d}/meta.json","w"))
    st(j,state="queued",msg="queued, waiting for worker",t=time.time()); return {"job":j}
@app.get("/status/{j}")
def status(j:str,key:str=""):
    chk(key); p=os.path.join(jd(j),"status.json")
    if not os.path.exists(p): raise HTTPException(404,"no such job")
    return json.load(open(p))
@app.get("/file/{j}/{name}")
def file(j:str,name:str,key:str="",dl:str=""):
    chk(key); p=os.path.join(jd(j),"out",os.path.basename(name))
    if not os.path.exists(p): raise HTTPException(404,"not found")
    return FileResponse(p,filename=name,content_disposition_type="attachment" if dl=="1" else "inline")
# ---- worker API ----
@app.get("/w/next")
def wnext(request:Request):
    wchk(request); LAST["seen"]=time.time()
    q=[]
    for j in os.listdir(J):
        p=os.path.join(J,j,"status.json")
        if os.path.exists(p):
            s=json.load(open(p))
            if s.get("state")=="queued": q.append((s.get("t",0),j))
    if not q: return {}
    j=sorted(q)[0][1]; st(j,state="claimed",msg="worker picked up the job")
    return {"job":j,**json.load(open(os.path.join(jd(j),"meta.json")))}
@app.get("/w/input/{j}/{name}")
def winput(j:str,name:str,request:Request):
    wchk(request); return FileResponse(os.path.join(jd(j),os.path.basename(name)))
@app.post("/w/status/{j}")
async def wstatus(j:str,request:Request):
    wchk(request); LAST["seen"]=time.time(); return st(j,**(await request.json()))
@app.post("/w/result/{j}/{name}")
async def wresult(j:str,name:str,request:Request):
    wchk(request); o=os.path.join(jd(j),"out"); os.makedirs(o,exist_ok=True)
    with open(os.path.join(o,os.path.basename(name)),"wb") as f:
        async for c in request.stream(): f.write(c)
    return {"ok":True}
# ---- Scaffold live detect (runs on the phone with onnxruntime-web; server only serves static files) ----
import gzip as _gz
LIVE=f"{H}/live"; ISO={"Cross-Origin-Opener-Policy":"same-origin","Cross-Origin-Embedder-Policy":"credentialless"}
LIVE_SIZES=json.load(open(f"{LIVE}/models/sizes.json")) if os.path.exists(f"{LIVE}/models/sizes.json") else {}
def _gzfile(p,mt,req,cache,extra=None):
    if not os.path.exists(p): raise HTTPException(404,"not found")
    h={"Cache-Control":cache,"Vary":"Accept-Encoding","Cross-Origin-Resource-Policy":"same-origin",**(extra or {})}
    if "gzip" in req.headers.get("accept-encoding",""):
        h["Content-Encoding"]="gzip"; return FileResponse(p,media_type=mt,headers=h)
    return Response(_gz.decompress(open(p,"rb").read()),media_type=mt,headers=h)
@app.get("/live")
def live(): return HTMLResponse(open(f"{LIVE}/live.html").read(),headers={**ISO,"Cache-Control":"no-cache"})
@app.get("/live/check")
def live_check(key:str=""): chk(key); return {"ok":True}
@app.get("/live/ort/{name}")
def live_ort(name:str,request:Request):
    n=os.path.basename(name); mt="application/wasm" if n.endswith(".wasm") else "text/javascript"
    return _gzfile(f"{LIVE}/ort/{n}.gz",mt,request,"public, max-age=604800",ISO)
@app.get("/live/models/{name}")
def live_model(name:str,request:Request,key:str=""):
    chk(key); n=os.path.basename(name)
    return _gzfile(f"{LIVE}/models/{n}.gz","application/octet-stream",request,"private, max-age=31536000, immutable",{"X-Raw-Size":str(LIVE_SIZES.get(n,0)),"Access-Control-Expose-Headers":"X-Raw-Size"})
