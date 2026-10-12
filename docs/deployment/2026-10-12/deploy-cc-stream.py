import json, subprocess, pathlib, shutil, datetime, time, urllib.request, urllib.error, hashlib, os
os.umask(0o077)
root=pathlib.Path('/home/deploy/kiro-billing-test-20261010')
prod=pathlib.Path('/home/deploy/kiro-zyphrzero-test/data/credentials.json')
name='kiro-rs-billing-test-20261010'
def run(*args): return subprocess.check_output(args,text=True).strip()
def image(n): return run('docker','inspect',n,'--format','{{.Image}}')
old=image(name); production=image('kiro-rs-zyphrzero-test')
assert old=='sha256:ba74e53a06cf097c587f658ae6222047b404f9045f1c9e08f67c55bdbfdc359a', 'Unexpected test image'
archive=root/'cc-stream-20261012.tar'
assert hashlib.sha256(archive.read_bytes()).hexdigest()==(root/'cc-stream-20261012.sha256').read_text().strip()
run('docker','load','-i',str(archive))
new=image_id=run('docker','image','inspect','kiro-rs:cc-stream-20261012','--format','{{.Id}}')
connections=run('docker','exec',name,'cat','/proc/net/tcp','/proc/net/tcp6')
active=sum(1 for line in connections.splitlines() if len(line.split())>=4 and not line.split()[1].endswith(':231E') and line.split()[3]=='01')
assert active==0, 'Active upstream connections; not switching'
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup=root/('rollback-cc-'+stamp); backup.mkdir(mode=0o700)
base=root/'compose.billing-test.yaml'
rollback=backup/'compose.rollback.yaml'; rollback.write_text('services:\n  billing-test:\n    image: '+old+'\n    environment:\n      KIRO_BILLING_DIAGNOSTICS: "1"\n')
overlay=root/'compose.cc-stream.yaml'; overlay.write_text('services:\n  billing-test:\n    image: '+new+'\n    environment:\n      KIRO_BILLING_DIAGNOSTICS: "1"\n')
def up(file): subprocess.run(['docker','compose','-f',str(base),'-f',str(file),'up','-d','--no-deps','billing-test'],check=True,stdout=subprocess.DEVNULL)
def health():
 for _ in range(30):
  try:
   with urllib.request.urlopen('http://127.0.0.1:8993/admin',timeout=2) as r:
    if r.status==200: return True
  except (OSError,urllib.error.URLError): pass
  time.sleep(1)
 return False
subprocess.run(['docker','stop',name],check=True,stdout=subprocess.DEVNULL)
shutil.copytree(root/'data',backup/'data')
try:
 source=json.loads(prod.read_text()); target=json.loads((root/'data/credentials.json').read_text())
 assert isinstance(source,list) and isinstance(target,list)
 added=updated=0
 for row in source:
  candidates=[x for x in target if (row.get('email') and x.get('email','').lower()==row['email'].lower()) or (row.get('refreshToken') and row.get('refreshToken')==x.get('refreshToken'))]
  assert len(candidates)<=1, 'Ambiguous account identity'
  if candidates:
   match=candidates[0]; ident=match.get('id'); match.update(row); match['id']=ident; updated+=1
  else:
   copy=dict(row); copy['id']=max([x.get('id',0) or 0 for x in target]+[0])+1; target.append(copy); added+=1
 tmp=root/'data/credentials.json.next'; tmp.write_text(json.dumps(target,indent=2)); tmp.replace(root/'data/credentials.json')
 up(overlay)
 assert health(), 'Admin health failed'
 assert image(name)==new
 assert image('kiro-rs-zyphrzero-test')==production
 try: urllib.request.urlopen('http://127.0.0.1:8993/v1/models',timeout=5); raise RuntimeError('API authentication missing')
 except urllib.error.HTTPError as e: assert e.code==401
except Exception:
 subprocess.run(['docker','stop',name],stdout=subprocess.DEVNULL,check=False)
 shutil.copy2(backup/'data/credentials.json',root/'data/credentials.json')
 up(rollback)
 print(json.dumps({'rolled_back':True,'healthy':health(),'backup':str(backup)}))
 raise
print(json.dumps({'time_utc':stamp,'image':new,'previous_image':old,'production_unchanged':True,'admin_status':200,'unauth_models_status':401,'accounts_added':added,'accounts_updated':updated,'accounts_total':len(target),'backup':str(backup),'inference_tested':False},indent=2))
