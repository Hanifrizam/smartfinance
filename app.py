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
SUPABASE_URL = os.getenv('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.getenv('SUPABASE_PUBLISHABLE_KEY', '')
REST = SUPABASE_URL + '/rest/v1/'
os.makedirs(os.path.dirname(DB) or '.', exist_ok=True)
app.config.update(SECRET_KEY=SECRET, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=BASE.startswith('https://'), PERMANENT_SESSION_LIFETIME=timedelta(days=7), MAX_CONTENT_LENGTH=32768)
cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256((SECRET+'gmail-token').encode()).digest()))
CATS = ['Makanan','Transportasi','Belanja','Tagihan','Kesehatan','Pendidikan','Hiburan','Gaji','Lainnya']
RULES = {'Makanan':['makan','kopi','nasi','resto','gofood','grabfood','ayam','bakso','roti'], 'Transportasi':['bensin','parkir','tol','gojek','grabcar','kereta','transport','ojek'], 'Belanja':['baju','sepatu','laptop','barang','shopee','tokopedia','elektronik'], 'Tagihan':['listrik','internet','wifi','pulsa','sewa','pdam'], 'Kesehatan':['obat','dokter','rumah sakit','apotek'], 'Pendidikan':['buku','kuliah','kursus','sekolah'], 'Hiburan':['bioskop','netflix','spotify','game'], 'Gaji':['gaji','salary','honor','bonus']}

def use_supabase():
    return bool(SUPABASE_URL and SUPABASE_KEY)

class SupabaseError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__('supabase status ' + str(status))

def db():
    if 'db' not in g:
        g.db=sqlite3.connect(DB, timeout=20); g.db.row_factory=sqlite3.Row; g.db.execute('PRAGMA foreign_keys=ON')
    return g.db
@app.teardown_appcontext
def close(_):
    if 'db' in g: g.db.close()
with app.app_context():
    if not use_supabase():
        db().executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT,google_sub TEXT UNIQUE,budget INTEGER DEFAULT 0,threshold INTEGER DEFAULT 80,auto_sync INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS pockets(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),name TEXT NOT NULL,target INTEGER NOT NULL,balance INTEGER DEFAULT 0 CHECK(balance>=0));
        CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),description TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),kind TEXT NOT NULL,category TEXT NOT NULL,date TEXT NOT NULL,source TEXT DEFAULT 'manual');
        CREATE TABLE IF NOT EXISTS gmail(user_id INTEGER PRIMARY KEY REFERENCES users(id),token TEXT NOT NULL,email TEXT,last_sync TEXT);
        CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),message_id TEXT NOT NULL,description TEXT,amount INTEGER,kind TEXT,category TEXT,date TEXT,status TEXT DEFAULT 'pending',UNIQUE(user_id,message_id));
        CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY,count INTEGER,started REAL);
        '''); db().commit()

def sb_req(method, table, params=None, body=None, prefer='return=minimal'):
    headers = {'apikey': SUPABASE_KEY, 'Authorization': 'Bearer ' + SUPABASE_KEY, 'Content-Type': 'application/json'}
    if method in ('POST', 'PATCH', 'DELETE'):
        headers['Prefer'] = prefer
    try:
        r = requests.request(method, REST + table, headers=headers, params=params or [], json=body if method != 'GET' else None, timeout=25)
        if r.status_code >= 300:
            raise SupabaseError(r.status_code)
        return r.json() if r.status_code != 204 else []
    except requests.RequestException as exc:
        raise SupabaseError(0) from exc

def sbf(**kw):
    out = []
    for k, v in kw.items():
        out.append((k, 'is.' + str(v).lower() if isinstance(v, bool) else 'eq.' + str(v)))
    return out

def get_user_by_email(email):
    if use_supabase():
        rows = sb_req('GET', 'users', params=sbf(email=email) + [('limit', '1')])
        return rows[0] if rows else None
    row = db().execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
    return dict(row) if row else None

def get_user_by_google_sub(sub):
    if use_supabase():
        rows = sb_req('GET', 'users', params=sbf(google_sub=sub) + [('limit', '1')])
        return rows[0] if rows else None
    row = db().execute('SELECT * FROM users WHERE google_sub=?', (sub,)).fetchone()
    return dict(row) if row else None

def get_user_by_id(uid):
    if use_supabase():
        rows = sb_req('GET', 'users', params=sbf(id=uid) + [('limit', '1')])
        return rows[0] if rows else None
    row = db().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    return dict(row) if row else None

def create_user(name, email, password=None, google_sub=None):
    payload = {'name': name, 'email': email}
    if password: payload['password'] = password
    if google_sub: payload['google_sub'] = google_sub
    if use_supabase():
        rows = sb_req('POST', 'users', params=[('select', 'id')], body=payload, prefer='return=representation')
        return rows[0]['id']
    cur = db().execute('INSERT INTO users(name,email,password,google_sub) VALUES (?,?,?,?)', (name, email, password, google_sub)); db().commit()
    return cur.lastrowid

def update_settings(uid, budget, threshold, auto_sync):
    if use_supabase():
        sb_req('PATCH', 'users', params=sbf(id=uid), body={'budget': budget, 'threshold': threshold, 'auto_sync': auto_sync}); return
    db().execute('UPDATE users SET budget=?,threshold=?,auto_sync=? WHERE id=?', (budget, threshold, int(auto_sync), uid)); db().commit()

def update_auto_sync(uid, val):
    if use_supabase():
        sb_req('PATCH', 'users', params=sbf(id=uid), body={'auto_sync': bool(val)}); return
    db().execute('UPDATE users SET auto_sync=? WHERE id=?', (int(bool(val)), uid)); db().commit()

def attempt_lookup(key):
    if use_supabase():
        rows = sb_req('GET', 'attempts', params=sbf(key=key) + [('limit', '1')])
        return rows[0] if rows else None
    row = db().execute('SELECT * FROM attempts WHERE key=?', (key,)).fetchone()
    return dict(row) if row else None

def attempt_reset(key, now):
    if use_supabase():
        try: sb_req('POST', 'attempts', params=[('on_conflict', 'key')], body={'key': key, 'count': 1, 'started': now})
        except SupabaseError: pass
        return
    db().execute('INSERT OR REPLACE INTO attempts VALUES (?,1,?)', (key, now)); db().commit()

def attempt_bump(key):
    if use_supabase():
        try:
            rows = sb_req('GET', 'attempts', params=sbf(key=key) + [('limit', '1')])
            if rows:
                sb_req('POST', 'attempts', params=[('on_conflict', 'key')], body={**rows[0], 'count': rows[0]['count'] + 1})
        except SupabaseError: pass
        return
    db().execute('UPDATE attempts SET count=count+1 WHERE key=?', (key,)); db().commit()

def attempt_prune(now):
    if use_supabase():
        try: sb_req('DELETE', 'attempts', params=[('started', 'lt.' + str(now - 86400))])
        except SupabaseError: pass
        return
    db().execute('DELETE FROM attempts WHERE started<?', (now - 86400,)); db().commit()

def list_transactions(uid):
    if use_supabase():
        return sb_req('GET', 'transactions', params=sbf(user_id=uid) + [('order', 'date.desc,id.desc')])
    return [dict(r) for r in db().execute('SELECT * FROM transactions WHERE user_id=? ORDER BY date DESC,id DESC', (uid,))]

def add_transaction(uid, description, amount, kind, category, date, source='manual'):
    if use_supabase():
        sb_req('POST', 'transactions', body={'user_id': uid, 'description': description, 'amount': amount, 'kind': kind, 'category': category, 'date': date, 'source': source}); return
    db().execute('INSERT INTO transactions(user_id,description,amount,kind,category,date,source) VALUES (?,?,?,?,?,?,?)', (uid, description, amount, kind, category, date, source)); db().commit()

def delete_transaction(uid, tid):
    if use_supabase():
        sb_req('DELETE', 'transactions', params=sbf(user_id=uid, id=tid)); return
    db().execute('DELETE FROM transactions WHERE id=? AND user_id=?', (tid, uid)); db().commit()

def list_pockets(uid):
    if use_supabase():
        return sb_req('GET', 'pockets', params=sbf(user_id=uid))
    return [dict(r) for r in db().execute('SELECT * FROM pockets WHERE user_id=?', (uid,))]

def get_pocket(uid, pid):
    if use_supabase():
        rows = sb_req('GET', 'pockets', params=sbf(user_id=uid, id=pid) + [('limit', '1')])
        return rows[0] if rows else None
    row = db().execute('SELECT * FROM pockets WHERE id=? AND user_id=?', (pid, uid)).fetchone()
    return dict(row) if row else None

def add_pocket(uid, name, target):
    if use_supabase():
        sb_req('POST', 'pockets', body={'user_id': uid, 'name': name, 'target': target}); return
    db().execute('INSERT INTO pockets(user_id,name,target) VALUES (?,?,?)', (uid, name, target)); db().commit()

def set_pocket_balance(uid, pid, balance):
    if use_supabase():
        sb_req('PATCH', 'pockets', params=sbf(user_id=uid, id=pid), body={'balance': balance}); return
    db().execute('UPDATE pockets SET balance=? WHERE id=? AND user_id=?', (balance, pid, uid)); db().commit()

def get_gmail(uid):
    if use_supabase():
        rows = sb_req('GET', 'gmail', params=sbf(user_id=uid) + [('limit', '1')])
        return rows[0] if rows else None
    row = db().execute('SELECT * FROM gmail WHERE user_id=?', (uid,)).fetchone()
    return dict(row) if row else None

def save_gmail(uid, token, email, last_sync=None):
    if use_supabase():
        sb_req('POST', 'gmail', params=[('on_conflict', 'user_id')], body={'user_id': uid, 'token': token, 'email': email, 'last_sync': last_sync}); return
    db().execute('INSERT OR REPLACE INTO gmail(user_id,token,email,last_sync) VALUES (?,?,?,?)', (uid, token, email, last_sync)); db().commit()

def delete_gmail(uid):
    if use_supabase():
        sb_req('DELETE', 'gmail', params=sbf(user_id=uid)); return
    db().execute('DELETE FROM gmail WHERE user_id=?', (uid,)); db().commit()

def set_gmail_last_sync(uid, ts):
    if use_supabase():
        sb_req('PATCH', 'gmail', params=sbf(user_id=uid), body={'last_sync': str(ts)}); return
    db().execute('UPDATE gmail SET last_sync=? WHERE user_id=?', (str(ts), uid)); db().commit()

def list_pending_imports(uid):
    if use_supabase():
        return sb_req('GET', 'imports', params=sbf(user_id=uid, status='pending') + [('order', 'id.desc')])
    return [dict(r) for r in db().execute("SELECT * FROM imports WHERE user_id=? AND status='pending' ORDER BY id DESC", (uid,))]

def get_pending_import(uid, iid):
    if use_supabase():
        rows = sb_req('GET', 'imports', params=sbf(user_id=uid, id=iid, status='pending') + [('limit', '1')])
        return rows[0] if rows else None
    row = db().execute("SELECT * FROM imports WHERE id=? AND user_id=? AND status='pending'", (iid, uid)).fetchone()
    return dict(row) if row else None

def import_exists(uid, message_id):
    if use_supabase():
        rows = sb_req('GET', 'imports', params=sbf(user_id=uid, message_id=message_id) + [('select', 'id'), ('limit', '1')])
        return bool(rows)
    return db().execute('SELECT id FROM imports WHERE user_id=? AND message_id=?', (uid, message_id)).fetchone() is not None

def insert_import(uid, message_id, description, amount, kind, category, date):
    if use_supabase():
        if import_exists(uid, message_id): return 0
        try:
            sb_req('POST', 'imports', body={'user_id': uid, 'message_id': message_id, 'description': description, 'amount': amount, 'kind': kind, 'category': category, 'date': date})
            return 1
        except SupabaseError: return 0
    cur = db().execute('INSERT OR IGNORE INTO imports(user_id,message_id,description,amount,kind,category,date) VALUES (?,?,?,?,?,?,?)', (uid, message_id, description, amount, kind, category, date)); db().commit()
    return cur.rowcount

def set_import_status(uid, iid, status):
    if use_supabase():
        sb_req('PATCH', 'imports', params=sbf(user_id=uid, id=iid), body={'status': status}); return
    db().execute('UPDATE imports SET status=? WHERE id=? AND user_id=?', (status, iid, uid)); db().commit()

def auto_sync_uids():
    if use_supabase():
        return [r['id'] for r in sb_req('GET', 'users', params=sbf(auto_sync=True) + [('select', 'id')])]
    return [r[0] for r in db().execute('SELECT u.id FROM users u JOIN gmail g ON g.user_id=u.id WHERE u.auto_sync=1')]

def totals(uid):
    rows = list_transactions(uid)
    income = sum(r['amount'] for r in rows if r['kind'] == 'income')
    expense = sum(r['amount'] for r in rows if r['kind'] == 'expense')
    reserved = sum(p['balance'] for p in list_pockets(uid))
    return income - expense, reserved

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
@app.errorhandler(SupabaseError)
def db_error(_): return fail('Database tidak dapat diakses.',503)

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
    r=attempt_lookup(key)
    if r and now-r['started']<900 and r['count']>=15: return fail('Terlalu banyak percobaan. Tunggu 15 menit.',429)
    if not r or now-r['started']>=900: attempt_reset(key,now)
    else: attempt_bump(key)
    attempt_prune(now)
    u=get_user_by_email(email)
    if mode=='signup':
        if len(password)<10: return fail('Password minimal 10 karakter.')
        if u: return fail('Email sudah digunakan. Silakan masuk.')
        name=textfield(d,'name',80)
        try:
            uid=create_user(name,email,generate_password_hash(password))
        except (sqlite3.IntegrityError, SupabaseError) as e:
            if isinstance(e, SupabaseError) and e.status != 409: raise
            return fail('Email sudah digunakan.')
    else:
        if not u or not u['password'] or not check_password_hash(u['password'],password): return fail('Email atau password salah.',401)
        uid=u['id']
    login_user(uid);return jsonify(ok=True)
@app.post('/api/logout')
def logout(): session.clear();return jsonify(ok=True)
@app.get('/api/dashboard')
@auth
def overview():
    uid=session['uid'];u={k:get_user_by_id(uid)[k] for k in ('id','name','email','budget','threshold','auto_sync')}
    tx=list_transactions(uid)
    income=sum(t['amount'] for t in tx if t['kind']=='income');expense=sum(t['amount'] for t in tx if t['kind']=='expense');balance=income-expense
    pockets=list_pockets(uid);reserved=sum(p['balance'] for p in pockets)
    month=today()[:7]; minc=sum(t['amount'] for t in tx if t['kind']=='income' and t['date'].startswith(month)); mexp=sum(t['amount'] for t in tx if t['kind']=='expense' and t['date'].startswith(month))
    return jsonify(user=u,balance=balance,reserved=reserved,available=balance-reserved,income=minc,expense=mexp,transactions=tx,pockets=pockets,gmail=get_gmail(uid),imports=list_pending_imports(uid),month=month)
@app.post('/api/transactions')
@auth
def add_tx():
    vals=parsed(data());add_transaction(session['uid'],*vals);return jsonify(ok=True)
@app.delete('/api/transactions/<int:id>')
@auth
def delete_tx(id):
    delete_transaction(session['uid'],id);return jsonify(ok=True)
@app.post('/api/pockets')
@auth
def pocket():
    d=data();add_pocket(session['uid'],textfield(d,'name',60),amount(d.get('target')));return jsonify(ok=True)
@app.post('/api/pockets/<int:id>/allocate')
@auth
def allocate(id):
    d=data();a=amount(d.get('amount'));direction=d.get('direction');uid=session['uid']
    p=get_pocket(uid,id)
    if not p: return fail('Pocket tidak ditemukan.',404)
    balance,reserved=totals(uid)
    if direction=='in' and a<=balance-reserved: delta=a
    elif direction=='out' and a<=p['balance']: delta=-a
    else: return fail('Saldo tidak cukup atau arah tidak valid.')
    set_pocket_balance(uid,id,p['balance']+delta);return jsonify(ok=True)
@app.post('/api/settings')
@auth
def settings():
    d=data();b=amount(d.get('budget'),True);t=amount(d.get('threshold'))
    if t>100: return fail('Ambang harus 1–100%.')
    update_settings(session['uid'],b,t,int(d.get('auto_sync') is True));return jsonify(ok=True)

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
            uid=session['uid'];old=get_gmail(uid)
            if 'refresh_token' not in tok and old and old['email']==info['email']: tok['refresh_token']=json.loads(cipher.decrypt(old['token'].encode()))['refresh_token']
            if not tok.get('refresh_token'): raise ValueError('Missing refresh token')
            save_gmail(uid,cipher.encrypt(json.dumps(tok).encode()).decode(),info['email'])
        else:
            u=get_user_by_google_sub(info['sub'])
            if not u:
                # Never link a pre-registered, unverified email automatically.
                if get_user_by_email(info['email'].lower()): return redirect('/?error=email_exists')
                uid=create_user(info.get('name',info['email']),info['email'].lower(),None,info['sub'])
            else: uid=u['id']
            login_user(uid)
        return redirect('/dashboard.html?connected=1')
    except (requests.RequestException,ValueError,KeyError,sqlite3.IntegrityError,SupabaseError): return redirect(dest+'?error=google_failed')

def body_text(payload):
    parts=[]
    if payload.get('mimeType')=='text/plain' and payload.get('body',{}).get('data'):
        s=payload['body']['data'];parts.append(base64.urlsafe_b64decode(s+'='*(-len(s)%4)).decode('utf-8','replace'))
    for part in payload.get('parts',[]): parts.extend(body_text(part))
    return parts

def sync_gmail(uid):
    row=get_gmail(uid)
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
            if import_exists(uid,item['id']): continue
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
            count+=insert_import(uid,item['id'],subject[:160],n,kind,s['category'],day)
        page=result.get('nextPageToken')
        if not page: break
    set_gmail_last_sync(uid,str(time.time()));return count
@app.post('/api/gmail/sync')
@auth
def sync_api():
    try: return jsonify(imported=sync_gmail(session['uid']))
    except requests.RequestException:return fail('Google gagal diakses. Coba lagi atau sambungkan ulang Gmail.',502)
@app.delete('/api/gmail')
@auth
def disconnect():
    uid=session['uid'];row=get_gmail(uid)
    if row:
        tok=json.loads(cipher.decrypt(row['token'].encode()))
        try: requests.post('https://oauth2.googleapis.com/revoke',data={'token':tok['refresh_token']},timeout=15)
        except requests.RequestException: pass
    delete_gmail(uid);update_auto_sync(uid,0);return jsonify(ok=True)
@app.post('/api/imports/<int:id>/<action>')
@auth
def accept_import(id,action):
    if action not in ['accept','ignore']:return fail('Aksi tidak valid.')
    vals=parsed(data()) if action=='accept' else None
    if not get_pending_import(session['uid'],id):return fail('Draft sudah diproses atau tidak ditemukan.',409)
    if vals:add_transaction(session['uid'],*vals,source='gmail')
    set_import_status(session['uid'],id,action);return jsonify(ok=True)

if __name__=='__main__':app.run(host='0.0.0.0',port=8000)