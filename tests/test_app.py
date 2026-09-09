import os, tempfile, unittest, json, base64
from unittest.mock import patch
TMP=tempfile.TemporaryDirectory()
os.environ['DATABASE_PATH']=TMP.name+'/test.db'
os.environ['SECRET_KEY']='test-secret-'+('x'*50)
os.environ['BASE_URL']='http://localhost:8000'
os.environ['SUPABASE_URL']=''
os.environ['SUPABASE_PUBLISHABLE_KEY']=''
from app import app, db, smart, today, cipher, sync_gmail

class FinanceTests(unittest.TestCase):
    def setUp(self):
        app.config['TESTING']=True
        with app.app_context():
            for table in ['imports','gmail','transactions','pockets','attempts','users']:db().execute('DELETE FROM '+table)
            db().commit()
        self.a=app.test_client();self.b=app.test_client()
        self.signup(self.a,'a@example.com');self.signup(self.b,'b@example.com')
    def req(self,c,path,body=None,method='POST'):
        csrf=c.get('/api/session').json['csrf'];return c.open('/api'+path,method=method,json=body or {},headers={'X-CSRF-Token':csrf})
    def signup(self,c,email):self.assertEqual(self.req(c,'/auth/signup',dict(name='User',email=email,password='strongpassword123')).status_code,200)
    def tx(self,c,n=1000000,kind='income'):
        return self.req(c,'/transactions',dict(description='Transaksi',amount=n,kind=kind,category='Gaji' if kind=='income' else 'Makanan',date=today()))
    def state(self,c):return c.get('/api/dashboard').json
    def test_auth_csrf_and_pages(self):
        c=app.test_client();self.assertEqual(c.get('/api/dashboard').status_code,401)
        self.assertEqual(c.post('/api/auth/signup',json={}).status_code,403)
        self.assertEqual(c.get('/dashboard.html').status_code,302)
        self.assertEqual(self.a.get('/dashboard.html').status_code,200)
        self.req(self.a,'/logout');self.assertEqual(self.req(self.a,'/auth/login',dict(email='a@example.com',password='strongpassword123')).status_code,200)
    def test_pockets_isolation_and_balances(self):
        self.tx(self.a);self.req(self.a,'/pockets',dict(name='Dana darurat',target=2000000));pid=self.state(self.a)['pockets'][0]['id']
        self.assertEqual(self.req(self.b,f'/pockets/{pid}/allocate',dict(amount=50,direction='in')).status_code,404)
        self.assertEqual(self.req(self.a,f'/pockets/{pid}/allocate',dict(amount=600000,direction='in')).status_code,200)
        self.assertEqual(self.req(self.a,f'/pockets/{pid}/allocate',dict(amount=500000,direction='in')).status_code,400)
        self.tx(self.a,100000,'expense');s=self.state(self.a);self.assertEqual((s['balance'],s['reserved'],s['available']),(900000,600000,300000))
        self.assertEqual(self.state(self.b)['balance'],0)
        tid=s['transactions'][0]['id'];self.req(self.b,f'/transactions/{tid}',method='DELETE');self.assertEqual(len(self.state(self.a)['transactions']),2)
    def test_validation_and_smart(self):
        for v in [-1,1.5,'NaN',True,1000000000001]:self.assertEqual(self.tx(self.a,v).status_code,400)
        self.assertEqual(smart('makan siang 35rb')['amount'],35000)
        self.assertEqual(smart('beli 2 nasi 35rb')['amount'],35000)
        self.assertEqual(smart('gaji 2,5jt')['amount'],2500000)
        self.assertEqual(smart('beli laptop 8jt')['category'],'Belanja')
        self.assertEqual(self.req(self.a,'/settings',dict(budget=100000,threshold=101)).status_code,400)
        self.assertEqual(self.req(self.a,'/settings',dict(budget=100000,threshold=80,auto_sync=True)).status_code,200)
    def test_import_idempotency_and_isolation(self):
        uid=self.state(self.a)['user']['id']
        with app.app_context():
            cur=db().execute('INSERT INTO imports(user_id,message_id,description,amount,kind,category,date) VALUES (?,?,?,?,?,?,?)',(uid,'m1','Makan',35000,'expense','Makanan',today())); iid=cur.lastrowid;db().commit()
        body=dict(description='Makan',amount=35000,kind='expense',category='Makanan',date=today())
        self.assertEqual(self.req(self.b,f'/imports/{iid}/accept',body).status_code,409)
        self.assertEqual(self.req(self.a,f'/imports/{iid}/accept',body).status_code,200)
        self.assertEqual(self.req(self.a,f'/imports/{iid}/accept',body).status_code,409)
        self.assertEqual(self.state(self.a)['expense'],35000)
    def test_mocked_gmail_parser_and_deduplication(self):
        uid=self.state(self.a)['user']['id']
        with app.app_context():
            db().execute('INSERT INTO gmail(user_id,token,email) VALUES (?,?,?)',(uid,cipher.encrypt(json.dumps({'refresh_token':'fake'}).encode()).decode(),'a@example.com'));db().commit()
            message={'internalDate':'1788883200000','payload':{'headers':[{'name':'Subject','value':'Pembayaran berhasil'}],'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(b'Total Rp35.000').decode()}}}
            with patch('app.google_post',return_value={'access_token':'fake'}),patch('app.google_get',side_effect=[{'messages':[{'id':'mock-id'}]},message]):self.assertEqual(sync_gmail(uid),1)
            row=db().execute('SELECT * FROM imports').fetchone();self.assertEqual(row['amount'],35000)
            self.assertEqual(db().execute('SELECT COUNT(*) FROM transactions').fetchone()[0],0)
            db().execute('UPDATE gmail SET last_sync=NULL');db().commit()
            with patch('app.google_post',return_value={'access_token':'fake'}),patch('app.google_get',return_value={'messages':[{'id':'mock-id'}]}):self.assertEqual(sync_gmail(uid),0)
    def test_google_rejects_bad_state(self):
        self.assertIn('oauth_state',self.a.get('/auth/google/callback?state=bad&code=fake').location)

if __name__=='__main__':unittest.main()
