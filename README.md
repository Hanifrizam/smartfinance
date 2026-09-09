# Saku — HTML5 + Backend Keuangan Pribadi

Source code siap dijalankan dan dikembangkan. Dua halaman utama: `public/index.html` (login/daftar) dan `public/dashboard.html` (dashboard). Backend Flask melayani HTML dan REST API dari domain yang sama. Tidak perlu build frontend, Node.js, atau layanan AI berbayar.

## Fitur

- Registrasi email + password langsung aktif: tidak mengirim email verifikasi maupun notifikasi pendaftaran.
- Login Google OAuth; koneksi baca Gmail terpisah dan opsional.
- Dashboard saldo total, saldo bebas, pemasukan/pengeluaran bulan berjalan, komposisi kategori dan riwayat transaksi.
- Pocket berdasarkan nama kebutuhan, target, tambah/tarik alokasi, indikator progres.
- Input transaksi manual; smart entry berbasis kata kunci Bahasa Indonesia (contoh `makan siang 35rb`, `gaji 5jt`, `beli laptop 8jt`). Hasil bisa dikoreksi sebelum disimpan. Ini parser lokal, bukan LLM, belum memahami semua kalimat.
- Batas pengeluaran bulanan dan ambang peringatan 1–100% per pengguna. Notifikasi tampil di dashboard; bukan email/push saat aplikasi tertutup.
- Gmail: baca email transaksi 30 hari terakhir, maksimal 500 kandidat per sinkronisasi; ekstraksi nominal dan kategori menjadi draft. Konfirmasi draft memasukkan transaksi dan menghitung ulang saldo. Ada tombol abaikan dan deduplikasi ID email.
- Auto-sync tiap 5 menit selama dashboard terlihat, jika diaktifkan pengguna; skrip worker tersedia untuk cron di server.
- Responsive, formulir modal, empty states, validasi, password hash, CSRF, cookie HttpOnly, OAuth state + PKCE, token Gmail terenkripsi dan isolasi data per pengguna.

## Menjalankan dengan Docker (disarankan)

1. Ekstrak ZIP dan buka terminal di folder `saku-finance`.
2. Salin `.env.example` menjadi `.env`.
3. Buat secret acak:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

4. Masukkan hasilnya ke `SECRET_KEY` di `.env`. Nilai contoh akan ditolak aplikasi.
5. Jalankan:

```bash
docker compose up --build -d
```

6. Buka **http://localhost:8000**. Buat akun melalui tab **Buat akun**. Google boleh dikosongkan untuk menggunakan fitur manual.

Database disimpan pada volume `saku-data`. Jangan menjalankan `docker compose down -v` bila ingin mempertahankan data. Nilai SECRET_KEY harus tetap sama setelah restart; perubahan akan mengakhiri sesi dan membuat token Gmail lama tidak dapat dibaca sehingga perlu koneksi ulang.

## Penyimpanan Supabase (opsional, disarankan untuk hosting)

Aplikasi bisa memakai Supabase (PostgreSQL) sebagai pengganti SQLite. Karena aplikasi memakai sesi login sendiri (bukan Supabase Auth) dan selalu memfilter data per `user_id`, skema dimuat dengan Row Level Security nonaktif dan `SUPABASE_PUBLISHABLE_KEY` digunakan hanya di sisi server.

1. Di dashboard Supabase, buka **SQL Editor → New query**, tempel isi `supabase/schema.sql`, lalu jalankan sekali.
2. Salin `Supabase URL` dan key `publishable` (Settings → API / Project settings) dari dashboard Supabase.
3. Isi `.env`:
   ```dotenv
   SUPABASE_URL=https://stgbzqvxvsmjrjnyfrla.supabase.co
   SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
   ```
4. Restart aplikasi. Jika `SUPABASE_URL` dan `SUPABASE_PUBLISHABLE_KEY` terisi, semua data disimpan di Supabase; jika dibiarkan kosong, aplikasi kembali memakai SQLite `DATABASE_PATH`.

Keamanan: jangan pernah membocorkan publishable key ke frontend publik. Saat menggunakan Supabase, cadangan tidak lagi di SQLite lokal; backup dilakukan dari Supabase (misalnya `supabase db dump` atau snapshot proyek). Tabel lama berprefix `saku_*` (jika ada) dari proyek yang sama tidak dipakai oleh skema baru dan bisa dihapus bila tidak dibutuhkan.

## Menjalankan tanpa Docker

Python 3.12 disarankan.

```bash
python -m venv .venv
```

Aktifkan environment:

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- Linux/macOS: `source .venv/bin/activate`

```bash
pip install -r requirements.txt
```

Buat `.env` seperti langkah Docker, kemudian:

```bash
python app.py
```

Server development dibuka pada port 8000. Untuk produksi Linux gunakan:

```bash
python -m gunicorn --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 300 app:app
```

## Konfigurasi Google Login dan Gmail

1. Di Google Cloud Console, buat/pilih project dan aktifkan **Gmail API**.
2. Konfigurasikan Google Auth Platform / OAuth consent: nama aplikasi, support email, audience dan domain milikmu. Pada mode Testing, tambahkan akun yang akan mencoba ke daftar test users.
3. Buat OAuth Client ID tipe **Web application**.
4. Tambahkan authorized redirect URI yang cocok persis:
   - Lokal: `http://localhost:8000/auth/google/callback`
   - Produksi: `https://domainkamu.com/auth/google/callback`
5. Isi `.env`:

```dotenv
SECRET_KEY=hasil-random-secret-kamu
BASE_URL=http://localhost:8000
DATABASE_PATH=data/saku.db
GOOGLE_CLIENT_ID=client-id-dari-google
GOOGLE_CLIENT_SECRET=client-secret-dari-google
```

6. Restart aplikasi. Coba login Google, kemudian gunakan **Hubungkan Gmail** untuk memberi izin baca.

Scope login: `openid email profile`. Scope tambahan Gmail: `https://www.googleapis.com/auth/gmail.readonly`. Tidak meminta izin kirim/hapus email. Gmail readonly termasuk restricted scope Google; penggunaan publik dapat memerlukan verifikasi OAuth dan security assessment sesuai penggunaan/penyimpanan data. Siapkan halaman privasi dan ketentuan untuk pengajuan produksi. Pada mode testing, refresh token dapat kedaluwarsa sesuai kebijakan Google, sehingga koneksi ulang mungkin diperlukan.

Akun manual tidak otomatis ditautkan dengan akun Google yang emailnya sama karena email manual tidak diverifikasi. Bila email sudah terdaftar manual, masuk memakai password; setelah itu Gmail tetap dapat dikoneksikan dari dashboard. Google dapat menampilkan pemberitahuan keamanan/consent miliknya sendiri; aplikasi ini tidak mengirim email pendaftaran.

Dokumentasi resmi:
- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/workspace/gmail/api/auth/scopes

## Preview GitHub Pages

Repository ini menyediakan workflow `.github/workflows/pages.yml` yang menerbitkan folder `public/` sebagai preview statis setiap kali ada push ke branch `main`.

Aktifkan satu kali di GitHub: buka **Settings → Pages → Source: GitHub Actions**. Setelah workflow selesai, halaman login dapat dibuka dari alamat `https://HANIFRIZAM.github.io/smartfinance/`.

GitHub Pages hanya menyediakan frontend statis. Login, registrasi, dashboard, SQLite, dan integrasi Google tetap harus dijalankan melalui backend Flask pada server/container. Untuk menjalankan aplikasi lengkap, gunakan alamat server Flask sesuai bagian deployment di bawah.

## Deployment server

Gunakan server/container dengan Python dan disk persisten. Kode ini tidak bisa dijalankan seluruhnya di GitHub Pages/static hosting karena memiliki backend dan SQLite.

- Upload source ke server, buat `.env` di server, jalankan Docker Compose.
- Arahkan domain ke server dan pasang reverse proxy HTTPS ke port 8000.
- Ubah `BASE_URL` menjadi origin HTTPS persis, tanpa trailing slash atau subpath. Update redirect URI Google dan restart.
- Cookie Secure otomatis aktif jika BASE_URL HTTPS. Frontend/API harus berada di origin yang sama.
- Simpan database pada satu disk persisten; jangan menjalankan salinan terpisah di banyak server dengan SQLite berbeda. Untuk skala besar, migrasikan ke PostgreSQL dan antrean worker.
- Backup volume/database secara konsisten dengan SQLite backup API atau saat service berhenti. Simpan `.env`/SECRET_KEY dengan aman terpisah; jangan commit.
- Tidak tersedia reset password via email. Jangan menganggap pendaftaran tanpa verifikasi membuktikan kepemilikan email.

## Sinkronisasi saat dashboard tertutup

Di server Linux, jadwalkan setiap 5 menit memakai cron. Sesuaikan direktori absolut tempat project berada:

```cron
*/5 * * * * cd /opt/saku-finance && /usr/bin/flock -n /tmp/saku-sync.lock /usr/bin/docker compose exec -T web python sync_worker.py
```

Worker hanya memproses pengguna yang mengaktifkan auto-sync. Deduplikasi berlaku terhadap ID pesan Gmail. Cron mencegah worker bertumpuk; sinkronisasi manual dapat berlangsung bersamaan tetapi insert email dan konfirmasi draft tetap idempoten. Untuk mailbox besar, kembangkan antrean dan incremental history sync.

## Cara menghitung saldo

Semua nominal bilangan bulat Rupiah; bulan transaksi berdasarkan Asia/Jakarta.

- Total saldo = seluruh pemasukan − seluruh pengeluaran.
- Alokasi pocket = total saldo yang ditandai untuk tujuan tertentu.
- Saldo bebas = total saldo − seluruh alokasi pocket.
- Transfer ke/dari pocket **tidak** menambah pemasukan maupun pengeluaran.
- Saldo awal: catat transaksi pemasukan berkategori Lainnya, deskripsi `Saldo awal`. Tanggal menentukan bulan masuknya pada ringkasan.
- Jika pengeluaran baru membuat saldo bebas negatif, tampil peringatan untuk menyesuaikan alokasi. Pocket tidak didebit otomatis.
- Transaksi salah dapat dihapus dan dicatat ulang. Menghapus transaksi menghitung ulang saldo dan dapat memunculkan peringatan alokasi.

## Batas pembacaan email

Parser bukan integrasi bank resmi dan tidak menjamin semua format bank/merchant terbaca. Hanya mencari frasa transaksi berhasil tertentu, membaca plain text atau snippet, dan mencari nominal berawalan Rp/IDR. Banyak nominal berbeda membuat nominal draft 0 agar pengguna mengisinya sendiri. Tanggal draft mengikuti waktu email diterima, bukan pasti tanggal transaksi bank. Transfer antar rekening pribadi, email duplikat dengan ID berbeda, atau transaksi yang telah dicatat manual harus diperiksa pengguna. Seluruh kandidat disimpan sebagai draft, tidak otomatis diposting ke saldo. Badan email penuh tidak disimpan ke database; hanya ringkasan dan ID pesan.

## Struktur source

```text
saku-finance/
  public/
    index.html          Login & registrasi
    dashboard.html      Dashboard dan modal
    style.css           Tampilan responsive
    auth.js             Interaksi login
    dashboard.js        Interaksi dashboard/API
  app.py                API, database, auth, parser, OAuth Gmail
  sync_worker.py        Sinkronisasi terjadwal
  supabase/schema.sql   Skema dan hak akses Supabase (jalankan sekali di SQL Editor)
  tests/test_app.py     Pengujian backend
  requirements.txt
  Dockerfile
  compose.yaml
  .env.example
  README.md
```

## Ringkasan API

GET `/api/session` memperoleh CSRF token dan status login. Semua POST/DELETE harus menyertakan header `X-CSRF-Token` serta cookie sesi. API mengembalikan JSON; error memakai field `error`.

| Endpoint | Method | Kegunaan |
|---|---|---|
| `/api/auth/signup`, `/api/auth/login` | POST | Email/password; signup juga memakai name |
| `/api/logout` | POST | Keluar |
| `/api/dashboard` | GET | Ringkasan dan data milik pengguna |
| `/api/transactions` | POST | description, amount, kind, category, date |
| `/api/transactions/:id` | DELETE | Hapus milik sendiri |
| `/api/pockets` | POST | name, target |
| `/api/pockets/:id/allocate` | POST | amount, direction: in/out |
| `/api/settings` | POST | budget, threshold, auto_sync boolean |
| `/api/smart` | POST | text → saran transaksi |
| `/auth/google?mode=login` | GET | Login Google |
| `/auth/google?mode=gmail` | GET | Consent Gmail |
| `/api/gmail/sync` | POST | Ambil kandidat email |
| `/api/gmail` | DELETE | Cabut koneksi lokal dan coba revoke token Google |
| `/api/imports/:id/accept` | POST | Konfirmasi dengan field transaksi |
| `/api/imports/:id/ignore` | POST | Abaikan draft |

## Pengujian

```bash
python -m unittest discover -s tests -v
```

Tes memakai database temporer, bukan data akun sebenarnya. Mencakup autentikasi/CSRF, pemisahan data akun, saldo/alokasi, validasi nominal, smart entry, duplikasi konfirmasi, OAuth state, dan parser Gmail dengan respons simulasi. Integrasi Google langsung memerlukan kredensial serta consent akun nyata; belum diuji langsung dalam paket ini. Pemeriksaan syntax JavaScript dilakukan dengan `node --check`; pengujian browser visual dan build Docker belum dilakukan.
