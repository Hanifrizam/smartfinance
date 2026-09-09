import os, re, json, sqlite3, secrets, time, base64, hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from functools import wraps
import requests
from flask import Flask, request, session, jsonify, redirect, send_from_directory, g
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__, static_folder='public', static_url_path='/assets')
SECRET = os.getenv('SECRET_KEY', '')
if len(SECRET) < 32 or SECRET.startswith('replace-with-'): raise RuntimeError('Set SECRET_KEY minimal 32 karakter di .env')
BASE = os.getenv('BASE_URL', 'http://localhost:8000').rstrip('/')
DB = os.getenv('DATABASE_PATH', 'data/saku.db')
os.makedirs(os.path.dirname(DB) or '.', exist_ok=True)
app.config.update(SECRET_KEY=SECRET, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=BASE.startswith('https://'), PERMANENT_SESSION_LIFETIME=timedelta(days=7), MAX_CONTENT_LENGTH=32768)
cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256((SECRET+'gmail-token').encode()).digest()))
CATS = ['Makanan','Transportasi','Belanja','Tagihan','Kesehatan','Pendidikan','Hiburan','Gaji','Lainnya']
RULES = {'Makanan':['makan','kopi','nasi','resto','gofood','grabfood','ayam','bakso','roti'], 'Transportasi':['bensin','parkir','tol','gojek','grabcar','kereta','transport','ojek'], 'Belanja':['baju','sepatu','laptop','barang','shopee','tokopedia','elektronik'], 'Tagihan':['listrik','internet','wifi','pulsa','sewa','pdam'], 'Kesehatan':['obat','dokter','rumah sakit','apotek'], 'Pendidikan':['buku','kuliah','kursus','sekolah'], 'Hiburan':['bioskop','netflix','spotify','game'], 'Gaji':['gaji','salary','honor','bonus']}

def db():
    if 'db' not in g:
        g.db=sqlite3.connect(DB, timeout=20); g.db.row_factory=sqlite3.Row; g.db.execute('PRAGMA foreign_keys=ON')
    return g.db
@app.teardown_appcontext
def close(_):
    if 'db' in g: g.db.close()
with app.app_context():
    db().executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT,google_sub TEXT UNIQUE,budget INTEGER DEFAULT 0,threshold INTEGER DEFAULT 80,auto_sync INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS pockets(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),name TEXT NOT NULL,target INTEGER NOT NULL,balance INTEGER DEFAULT 0 CHECK(balance>=0));
    CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),description TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),kind TEXT NOT NULL,category TEXT NOT NULL,date TEXT NOT NULL,source TEXT DEFAULT 'manual');
    CREATE TABLE IF NOT EXISTS gmail(user_id INTEGER PRIMARY KEY REFERENCES users(id),token TEXT NOT NULL,email TEXT,last_sync TEXT);
    CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),message_id TEXT NOT NULL,description TEXT,amount INTEGER,kind TEXT,category TEXT,date TEXT,status TEXT DEFAULT 'pending',UNIQUE(user_id,message_id));
    CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY,count INTEGER,started REAL);
    '''); db().commit()

def fail(message, status=400): return jsonify(error=message),status
def auth(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        if not session.get('uid'): return fail('Silakan masuk dahulu.',401)
        return fn(*args,**kwargs)
    return wrapped
@app.before_request
def guard():
    if request.method in ['POST','PUT','DELETE','PATCH']:
        if request.headers.get('Origin') and request.headers['Origin'] != BASE: return fail('Origin tidak diizinkan.',403)
        if not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),session.get('csrf','!')): return fail('Sesi berubah. Muat ulang halaman.',403)
@app.after_request
def headers(resp):
    resp.headers['X-Content-Type-Options']='nosniff'; resp.headers['Referrer-Policy']='same-origin'
    resp.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    resp.headers['Cache-Control']='no-store'
    return resp
@app.errorhandler(500)
def server_error(_): return fail('Terjadi masalah server. Coba lagi.',500)
@app.errorhandler(ValueError)
def validation(e): return fail(str(e))

def data(): return request.get_json(silent=True) or {}
def textfield(d,k,n=160):
    v=str(d.get(k,'')).strip()
    if not v or len(v)>n: raise ValueError(f'{k} wajib diisi, maksimal {n} karakter.')
    return v
def amount(v,zero=False):
    if isinstance(v,bool) or not re.fullmatch(r'\d+',str(v)): raise ValueError('Nominal harus Rupiah bulat.')
    v=int(v)
    if v < (0 if zero else 1) or v>1_000_000_000_000: raise ValueError('Nominal di luar batas.')
    return v
def today(): return datetime.now(ZoneInfo('Asia/Jakarta')).date().isoformat()
def parsed(d):
    desc=textfield(d,'description'); a=amount(d.get('amount')); kind=d.get('kind'); cat=d.get('category'); date=d.get('date',today())
    if kind not in ['income','expense'] or cat not in CATS: raise ValueError('Jenis atau kategori tidak valid.')
    try: datetime.strptime(date,'%Y-%m-%d')
    except (TypeError,ValueError): raise ValueError('Tanggal tidak valid.')
    if date>today(): raise ValueError('Transaksi masa depan belum didukung.')
    return desc,a,kind,cat,date

def totals(uid):
    rows=db().execute('SELECT kind,SUM(amount) n FROM transactions WHERE user_id=? GROUP BY kind',(uid,)).fetchall()
    values={r['kind']:r['n'] for r in rows}; balance=values.get('income',0)-values.get('expense',0)
    reserved=db().execute('SELECT COALESCE(SUM(balance),0) FROM pockets WHERE user_id=?',(uid,)).fetchone()[0]
    return balance,reserved

def login_user(uid):
    session.clear();session['uid']=uid;session['csrf']=secrets.token_urlsafe(32);session.permanent=True
@app.get('/')
@app.get('/index.html')
def index(): return send_from_directory('public','index.html')
@app.get('/dashboard.html')
def dashboard():
    if not session.get('uid'): return redirect('/index.html')
    return send_from_directory('public','dashboard.html')
@app.get('/api/session')
def sess():
    session.setdefault('csrf',secrets.token_urlsafe(32))
    return jsonify(csrf=session['csrf'],logged_in=bool(session.get('uid')),google_ready=bool(os.getenv('GOOGLE_CLIENT_ID')),categories=CATS)
@app.post('/api/auth/<mode>')
def credentials(mode):
    if mode not in ['login','signup']: return fail('Tidak ditemukan.',404)
    d=data();email=textfield(d,'email',254).lower();password=textfield(d,'password',128)
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email): return fail('Email tidak valid.')
    key=hashlib.sha256((str(request.remote_addr)+email).encode()).hexdigest(); now=time.time()
    c=db(); c.execute('BEGIN IMMEDIATE'); r=c.execute('SELECT * FROM attempts WHERE key=?',(key,)).fetchone()
    if r and now-r['started']<900 and r['count']>=15: c.rollback();return fail('Terlalu banyak percobaan. Tunggu 15 menit.',429)
    if not r or now-r['started']>=900: c.execute('INSERT OR REPLACE INTO attempts VALUES (?,1,?)',(key,now))
    else: c.execute('UPDATE attempts SET count=count+1 WHERE key=?',(key,))
    c.execute('DELETE FROM attempts WHERE started<?',(now-86400,));c.commit()
    u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    if mode=='signup':
        if len(password)<10: return fail('Password minimal 10 karakter.')
        if u: return fail('Email sudah digunakan. Silakan masuk.')
        name=textfield(d,'name',80)
        try:
            cur=c.execute('INSERT INTO users(name,email,password) VALUES (?,?,?)',(name,email,generate_password_hash(password))); c.commit(); uid=cur.lastrowid
        except sqlite3.IntegrityError: return fail('Email sudah digunakan.')
    else:
        if not u or not u['password'] or not check_password_hash(u['password'],password): return fail('Email atau password salah.',401)
        uid=u['id']
    login_user(uid);return jsonify(ok=True)
@app.post('/api/logout')
def logout(): session.clear();return jsonify(ok=True)
@app.get('/api/dashboard')
@auth
def overview():
    uid=session['uid'];u=dict(db().execute('SELECT id,name,email,budget,threshold,auto_sync FROM users WHERE id=?',(uid,)).fetchone())
    balance,reserved=totals(uid)
    tx=[dict(r) for r in db().execute('SELECT * FROM transactions WHERE user_id=? ORDER BY date DESC,id DESC',(uid,))]
    month=today()[:7]; income=sum(t['amount'] for t in tx if t['kind']=='income' and t['date'].startswith(month));expense=sum(t['amount'] for t in tx if t['kind']=='expense' and t['date'].startswith(month))
    gm=db().execute('SELECT email,last_sync FROM gmail WHERE user_id=?',(uid,)).fetchone()
    return jsonify(user=u,balance=balance,reserved=reserved,available=balance-reserved,income=income,expense=expense,transactions=tx,pockets=[dict(r) for r in db().execute('SELECT * FROM pockets WHERE user_id=?',(uid,))],gmail=dict(gm) if gm else None,imports=[dict(r) for r in db().execute("SELECT * FROM imports WHERE user_id=? AND status='pending' ORDER BY id DESC",(uid,))],month=month)
@app.post('/api/transactions')
@auth
def add_tx():
    vals=parsed(data());db().execute('INSERT INTO transactions(user_id,description,amount,kind,category,date) VALUES (?,?,?,?,?,?)',(session['uid'],*vals));db().commit();return jsonify(ok=True)
@app.delete('/api/transactions/<int:id>')
@auth
def delete_tx(id):
    db().execute('DELETE FROM transactions WHERE id=? AND user_id=?',(id,session['uid']));db().commit();return jsonify(ok=True)
@app.post('/api/pockets')
@auth
def pocket():
    d=data();db().execute('INSERT INTO pockets(user_id,name,target) VALUES (?,?,?)',(session['uid'],textfield(d,'name',60),amount(d.get('target'))));db().commit();return jsonify(ok=True)
@app.post('/api/pockets/<int:id>/allocate')
@auth
def allocate(id):
    d=data();a=amount(d.get('amount'));direction=d.get('direction');uid=session['uid'];c=db();c.execute('BEGIN IMMEDIATE')
    p=c.execute('SELECT * FROM pockets WHERE id=? AND user_id=?',(id,uid)).fetchone()
    if not p: c.rollback();return fail('Pocket tidak ditemukan.',404)
    balance,reserved=totals(uid)
    if direction=='in' and a<=balance-reserved: delta=a
    elif direction=='out' and a<=p['balance']: delta=-a
    else: c.rollback();return fail('Saldo tidak cukup atau arah tidak valid.')
    c.execute('UPDATE pockets SET balance=balance+? WHERE id=? AND user_id=?',(delta,id,uid));c.commit();return jsonify(ok=True)
@app.post('/api/settings')
@auth
def settings():
    d=data();b=amount(d.get('budget'),True);t=amount(d.get('threshold'))
    if t>100: return fail('Ambang harus 1–100%.')
    db().execute('UPDATE users SET budget=?,threshold=?,auto_sync=? WHERE id=?',(b,t,int(d.get('auto_sync') is True),session['uid']));db().commit();return jsonify(ok=True)

def smart(s):
    low=s.lower();cat=next((c for c,words in RULES.items() if any(re.search(r'\b'+re.escape(w)+r'\b',low) for w in words)),'Lainnya')
    m=re.search(r'(\d+(?:[.,]\d+)?)\s*(ribu|juta|rb|jt|k)\b',low)
    if not m: m=re.search(r'(?:rp\.?|idr)\s*(\d+(?:[.,]\d{3})*)(ribu|juta|rb|jt|k)?',low)
    if not m:
        matches=list(re.finditer(r'(\d+(?:[.,]\d{3})*)(ribu|juta|rb|jt|k)?\b',low))
        m=matches[-1] if matches else None
    n=0
    if m:
        raw=m[1];suffix=m[2]
        if suffix: n=round(float(raw.replace(',','.'))*(1_000_000 if suffix in ['juta','jt'] else 1000))
        else: n=int(re.sub(r'[.,]','',raw))
    return dict(description=s[:160],amount=n,kind='income' if re.search(r'\b(gaji|salary|honor|bonus|pemasukan|terima|refund)\b',low) else 'expense',category=cat,date=today())
@app.post('/api/smart')
@auth
def smart_api(): return jsonify(smart(textfield(data(),'text',500)))

# Login Google and Gmail consent are intentionally separate flows.
GMAIL_SCOPE='https://www.googleapis.com/auth/gmail.readonly'
def google_post(url,payload):
    r=requests.post(url,data=payload,timeout=25);r.raise_for_status();return r.json()
def google_get(url,token,params=None):
    r=requests.get(url,headers={'Authorization':'Bearer '+token},params=params,timeout=25);r.raise_for_status();return r.json()
@app.get('/auth/google')
def google_start():
    mode=request.args.get('mode','login')
    if mode not in ['login','gmail']: return fail('Mode tidak valid.')
    if mode=='gmail' and not session.get('uid'): return redirect('/')
    if not os.getenv('GOOGLE_CLIENT_ID'): return redirect('/'+('dashboard.html?error=google_setup' if session.get('uid') else '?error=google_setup'))
    state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
    session['oauth']={'state':state,'mode':mode,'time':time.time(),'verifier':verifier,'uid':session.get('uid')}
    scope='openid email profile'+(' '+GMAIL_SCOPE if mode=='gmail' else '')
    p=dict(client_id=os.getenv('GOOGLE_CLIENT_ID'),redirect_uri=BASE+'/auth/google/callback',response_type='code',scope=scope,state=state,code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),code_challenge_method='S256')
    if mode=='gmail': p.update(access_type='offline',prompt='consent')
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?'+urlencode(p))
@app.get('/auth/google/callback')
def google_callback():
    flow=session.pop('oauth',None);dest='/dashboard.html' if session.get('uid') else '/'
    if not flow or time.time()-flow['time']>600 or not secrets.compare_digest(flow['state'],request.args.get('state','')): return redirect(dest+'?error=oauth_state')
    if request.args.get('error'): return redirect(dest+'?error=oauth_cancelled')
    try:
        tok=google_post('https://oauth2.googleapis.com/token',dict(code=request.args.get('code',''),client_id=os.getenv('GOOGLE_CLIENT_ID'),client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),redirect_uri=BASE+'/auth/google/callback',grant_type='authorization_code',code_verifier=flow['verifier']))
        info=google_get('https://openidconnect.googleapis.com/v1/userinfo',tok['access_token'])
        if info.get('email_verified') is not True: raise ValueError('Unverified Google email')
        if flow['mode']=='gmail':
            if not session.get('uid') or flow['uid']!=session['uid'] or GMAIL_SCOPE not in tok.get('scope','').split(): raise ValueError('Missing Gmail consent')
            uid=session['uid'];old=db().execute('SELECT * FROM gmail WHERE user_id=?',(uid,)).fetchone()
            if 'refresh_token' not in tok and old and old['email']==info['email']: tok['refresh_token']=json.loads(cipher.decrypt(old['token'].encode()))['refresh_token']
            if not tok.get('refresh_token'): raise ValueError('Missing refresh token')
            db().execute('INSERT OR REPLACE INTO gmail(user_id,token,email,last_sync) VALUES (?,?,?,NULL)',(uid,cipher.encrypt(json.dumps(tok).encode()).decode(),info['email']));db().commit()
        else:
            u=db().execute('SELECT * FROM users WHERE google_sub=?',(info['sub'],)).fetchone()
            if not u:
                # Never link a pre-registered, unverified email automatically.
                if db().execute('SELECT id FROM users WHERE email=?',(info['email'].lower(),)).fetchone(): return redirect('/?error=email_exists')
                cur=db().execute('INSERT INTO users(name,email,google_sub) VALUES (?,?,?)',(info.get('name',info['email']),info['email'].lower(),info['sub']));db().commit();uid=cur.lastrowid
            else: uid=u['id']
            login_user(uid)
        return redirect('/dashboard.html?connected=1')
    except (requests.RequestException,ValueError,KeyError,sqlite3.IntegrityError): return redirect(dest+'?error=google_failed')

def body_text(payload):
    parts=[]
    if payload.get('mimeType')=='text/plain' and payload.get('body',{}).get('data'):
        s=payload['body']['data'];parts.append(base64.urlsafe_b64decode(s+'='*(-len(s)%4)).decode('utf-8','replace'))
    for part in payload.get('parts',[]): parts.extend(body_text(part))
    return parts

def sync_gmail(uid):
    c=db();row=c.execute('SELECT * FROM gmail WHERE user_id=?',(uid,)).fetchone()
    if not row: raise ValueError('Hubungkan Gmail terlebih dahulu.')
    if row['last_sync'] and time.time()-float(row['last_sync'])<60: return 0
    tok=json.loads(cipher.decrypt(row['token'].encode())); fresh=google_post('https://oauth2.googleapis.com/token',dict(client_id=os.getenv('GOOGLE_CLIENT_ID'),client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),refresh_token=tok['refresh_token'],grant_type='refresh_token'))
    token=fresh['access_token'];count=0;page=None
    # Up to 500 recent messages; all remain reviewable drafts, never arbitrary auto-posted money.
    for _ in range(5):
        params={'q':'newer_than:30d {"pembayaran berhasil" "transaksi berhasil" "transfer diterima" "payment successful" "dana masuk" "refund berhasil"}', 'maxResults':100}
        if page:params['pageToken']=page
        result=google_get('https://gmail.googleapis.com/gmail/v1/users/me/messages',token,params)
        for item in result.get('messages',[]):
            if c.execute('SELECT id FROM imports WHERE user_id=? AND message_id=?',(uid,item['id'])).fetchone(): continue
            msg=google_get('https://gmail.googleapis.com/gmail/v1/users/me/messages/'+item['id'],token,{'format':'full'})
            headers={h['name'].lower():h['value'] for h in msg.get('payload',{}).get('headers',[])}
            subject=headers.get('subject','Email transaksi');body=' '.join(body_text(msg.get('payload',{}))) or msg.get('snippet','')
            money=re.findall(r'(?:Rp\.?|IDR)\s*(\d+(?:[.,]\d{3})*(?:[.,]\d{2})?)',body,re.I)
            vals=[]
            for raw in money:
                raw=re.sub(r'[.,]\d{2}$','',raw);vals.append(int(re.sub(r'\D','',raw)))
            n=vals[0] if len(set(vals))==1 and vals else 0
            s=smart(subject+' '+body[:1000]);kind='income' if re.search(r'transfer diterima|dana masuk|refund berhasil',subject+' '+body,re.I) else 'expense'
            day=datetime.fromtimestamp(int(msg['internalDate'])/1000,ZoneInfo('Asia/Jakarta')).date().isoformat()
            cur=c.execute('INSERT OR IGNORE INTO imports(user_id,message_id,description,amount,kind,category,date) VALUES (?,?,?,?,?,?,?)',(uid,item['id'],subject[:160],n,kind,s['category'],day));count+=cur.rowcount
            c.commit()
        page=result.get('nextPageToken')
        if not page: break
    c.execute('UPDATE gmail SET last_sync=? WHERE user_id=?',(str(time.time()),uid));c.commit();return count
@app.post('/api/gmail/sync')
@auth
def sync_api():
    try: return jsonify(imported=sync_gmail(session['uid']))
    except requests.RequestException:return fail('Google gagal diakses. Coba lagi atau sambungkan ulang Gmail.',502)
@app.delete('/api/gmail')
@auth
def disconnect():
    uid=session['uid'];row=db().execute('SELECT token FROM gmail WHERE user_id=?',(uid,)).fetchone()
    if row:
        tok=json.loads(cipher.decrypt(row['token'].encode()))
        try: requests.post('https://oauth2.googleapis.com/revoke',data={'token':tok['refresh_token']},timeout=15)
        except requests.RequestException: pass
    db().execute('DELETE FROM gmail WHERE user_id=?',(uid,));db().execute('UPDATE users SET auto_sync=0 WHERE id=?',(uid,));db().commit();return jsonify(ok=True)
@app.post('/api/imports/<int:id>/<action>')
@auth
def accept_import(id,action):
    if action not in ['accept','ignore']:return fail('Aksi tidak valid.')
    vals=parsed(data()) if action=='accept' else None
    c=db();c.execute('BEGIN IMMEDIATE');row=c.execute("SELECT * FROM imports WHERE id=? AND user_id=? AND status='pending'",(id,session['uid'])).fetchone()
    if not row:c.rollback();return fail('Draft sudah diproses atau tidak ditemukan.',409)
    if vals:c.execute("INSERT INTO transactions(user_id,description,amount,kind,category,date,source) VALUES (?,?,?,?,?,?,'gmail')",(session['uid'],*vals))
    c.execute('UPDATE imports SET status=? WHERE id=?',(action,id));c.commit();return jsonify(ok=True)

if __name__=='__main__':app.run(host='0.0.0.0',port=8000)
