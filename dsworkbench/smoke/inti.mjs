// Uji integrasi: VS Code sungguhan + ekstensi + Local Runner sungguhan (--stdio).
//
// Membuka jendela VS Code sebentar di komputer ini dengan profil terisolasi
// (user-data-dir dan extensions-dir sementara, berjalur pendek), lalu
// menghapus semuanya. Tidak menyentuh `~/.workbench-agent` maupun folder kerja
// pengguna: agent memakai --state-dir dan AGENT_WORKSPACE_ROOT sementara.
//
//   node scripts/run-integration.mjs            (atau: npm run test:integration)
//   DSW_VSCODE=/jalur/ke/Code node scripts/run-integration.mjs
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as http from 'node:http';
import * as os from 'node:os';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

const akarEkstensi = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
// DSW_EXT_PATH: muat ekstensi dari folder lain (mis. dist-ext/dsworkbench hasil `npm run package`).
const akarMuat = process.env.DSW_EXT_PATH ? path.resolve(process.env.DSW_EXT_PATH) : akarEkstensi;
const repo = path.resolve(akarEkstensi, '..', '..', '..');
const KODE = process.env.DSW_VSCODE || {
	darwin: '/Applications/Visual Studio Code.app/Contents/MacOS/Code',
	win32: path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Microsoft VS Code', 'Code.exe'),
}[process.platform] || 'code';
const PYTHON = process.env.DSW_PYTHON || 'python3';
// Bundel uji asap (`npm run smoke:bundle`) menjalankan berkas ini DI LUAR repo: suite terkompilasi,
// fixture agent, dan daftar operasi diberikan lewat variabel lingkungan.
const SUITE = process.env.DSW_SUITE ? path.resolve(process.env.DSW_SUITE) : path.join(akarEkstensi, 'out-test', 'integration', 'suite.cjs');
const FIXTURE_AGENT = process.env.DSW_FIXTURE_AGENT ? path.resolve(process.env.DSW_FIXTURE_AGENT) : path.join(akarEkstensi, 'test', 'fixtures', 'agen_sungguhan.py');
const OPS_JSON = process.env.DSW_OPS_JSON ? path.resolve(process.env.DSW_OPS_JSON) : path.join(repo, 'docs', 'ide-local-ops.json');
// Windows: `fsPath` VS Code berhuruf drive kecil dan sistem berkasnya tidak peka huruf besar/kecil.
const nj = (p) => (process.platform === 'win32' && typeof p === 'string' ? p.toLowerCase() : p);
const nyata = (p) => fs.realpathSync.native(p);
const COURSE = 'data-wrangling';
const USERNAME = '122450001';
const TOKEN = 'token-uji-integrasi';
const RAHASIA = 'kredensial-perangkat-uji-0123456789';

if (!fs.existsSync(KODE) && KODE !== 'code') {
	console.error(`VS Code tidak ditemukan di ${KODE}. Setel DSW_VSCODE.`);
	process.exit(2);
}
for (const j of [path.join(akarMuat, 'dist', 'extension.js'), path.join(akarMuat, 'package.json'), SUITE, FIXTURE_AGENT, OPS_JSON]) {
	if (!fs.existsSync(j)) {
		console.error(`${j} tidak ada. Jalankan: npm run build && npm run build:test`);
		process.exit(2);
	}
}

// Jalur pendek: soket IPC VS Code dibatasi 103 karakter.
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dsw-'));
const dir = (n) => {
	const d = path.join(tmp, n);
	fs.mkdirSync(d, { recursive: true });
	return d;
};
const userData = dir('u');
const extDir = dir('e');
const stateDir = dir('s');
const ws = dir('w');
const pemicu = dir('p');
const wsCourse = path.join(ws, COURSE);
fs.mkdirSync(wsCourse);
// Folder runtime IDE dialihkan ke jalur sementara yang TIDAK ada: uji tidak boleh membuatnya
// (dan tidak menyentuh pemasangan DSWorkbench sungguhan di komputer ini).
const folderRuntime = path.join(tmp, 'r', 'DSWorkbench');
const catatanAgent = path.join(tmp, 'agent.json');
const out = path.join(tmp, 'hasil.json');

const PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
const sel = (source) => ({ cell_type: 'code', execution_count: null, metadata: {}, outputs: [], source });
// Sengaja tanpa `language_info`: ekstensi harus menambahkannya agar execution_count tersimpan.
const notebook = {
	cells: [
		sel("import sys, os\nprint('halo dari Local Runner')\nprint('peringatan', file=sys.stderr)\nx = 6 * 7\nopen('jejak.txt', 'w').write(os.getcwd())\nx"),
		sel(
			'import base64\n' +
				'class Tabel:\n' +
				"    def _repr_html_(self): return f'<table><tr><td>x</td><td>{x}</td></tr></table>'\n" +
				`    def _repr_png_(self): return base64.b64decode('${PNG_B64}')\n` +
				"print('variabel bertahan:', x)\n" +
				'Tabel()',
		),
		sel("RAHASIA_KODE_SEL = 'jangan-bocor'\n1 / 0"),
	],
	metadata: {},
	nbformat: 4,
	nbformat_minor: 5,
};
const NAMA_NB = `M01_${USERNAME}.ipynb`;
fs.writeFileSync(path.join(wsCourse, NAMA_NB), JSON.stringify(notebook, null, 1));
// Notebook kedua di mata kuliah yang sama: interupsi dan mulai ulang kernel.
const NAMA_NB2 = 'coba-coba.ipynb';
fs.writeFileSync(path.join(wsCourse, NAMA_NB2), JSON.stringify({ ...notebook, cells: [sel('y = 5\ny'), sel('import time\ntime.sleep(120)')] }, null, 1));
const luarDir = dir('luar');
const nbLuar = path.join(luarDir, 'di-luar.ipynb');
fs.writeFileSync(nbLuar, JSON.stringify({ ...notebook, cells: [sel('1 + 1')] }, null, 1));

// Panel "Pergudangan Data": mata kuliah kedua yang modulnya butuh basis data praktikum.
// Amplopnya memuat `services`; layanan dan dataset dijawab "relay" server palsu, katalog
// dijawab agent (fixture) dari berkas contoh ini karena mesin uji tidak punya PostgreSQL.
const COURSE_PGD = 'pergudangan-data';
const MODUL_PGD = 'module-02';
const KOLOM_ANEH = '<img src=x onerror="alert(1)">';
const kolomPgd = (name, type = 'integer') => ({ name, type, nullable: false, comment: null });
const KATALOG_PGD = {
	port: 5434,
	truncated: false,
	schemas: [
		{
			name: 'dw',
			tables: [
				{ name: 'dim_pelanggan', kind: 'table', rowsEstimate: 500, partitions: 0, columns: [kolomPgd('pelanggan_sk'), kolomPgd(KOLOM_ANEH, 'text')], primaryKey: ['pelanggan_sk'], foreignKeys: [] },
				{ name: 'dim_produk', kind: 'table', rowsEstimate: 80, partitions: 0, columns: [kolomPgd('produk_sk'), kolomPgd('nama_produk', 'text')], primaryKey: ['produk_sk'], foreignKeys: [] },
				{
					name: 'fakta_penjualan', kind: 'table', rowsEstimate: 12000, partitions: 0,
					columns: [kolomPgd('penjualan_sk', 'bigint'), kolomPgd('pelanggan_sk'), kolomPgd('produk_sk'), kolomPgd('order_id'), kolomPgd('jumlah', 'numeric(12,2)')],
					primaryKey: ['penjualan_sk'],
					foreignKeys: [
						{ name: 'fk_pelanggan', columns: ['pelanggan_sk'], refSchema: 'dw', refTable: 'dim_pelanggan', refColumns: ['pelanggan_sk'] },
						{ name: 'fk_produk', columns: ['produk_sk'], refSchema: 'dw', refTable: 'dim_produk', refColumns: ['produk_sk'] },
						{ name: 'fk_order', columns: ['order_id'], refSchema: 'staging', refTable: 'orders', refColumns: ['order_id'] },
					],
				},
				{ name: 'log_muat', kind: 'table', rowsEstimate: 3, partitions: 0, columns: [kolomPgd('id')], primaryKey: ['id'], foreignKeys: [] },
			],
		},
		{ name: 'staging', tables: [{ name: 'orders', kind: 'table', rowsEstimate: 9000, partitions: 0, columns: [kolomPgd('order_id'), kolomPgd('tanggal', 'date')], primaryKey: ['order_id'], foreignKeys: [] }] },
	],
};
const katalogPgd = path.join(tmp, 'katalog.json');
fs.writeFileSync(katalogPgd, JSON.stringify(KATALOG_PGD));
const SERVICES_PGD = { postgres: { version: '17', clusters: [{ alias: 'source', port: 5433, databases: ['nusamart_oltp'], studentAccess: 'read' }, { alias: 'dw', port: 5434, databases: ['nusamart_dw'], studentAccess: 'owner' }] } };
// Keadaan "laptop" menurut relay palsu: basis data mati, dataset belum ada, data belum dimuat.
const pgd = { menyala: false, dataset: false, dimuat: false, job: new Map(), cek: new Map(), riwayat: [] };
// Pemeriksa SQL modul (`sql.check`, job relay): keluaran mentah psql dan nama artefak ikut di jawaban
// "agent" — ekstensi tidak boleh menampilkannya. Pemeriksaan pertama GAGAL (1 butir), berikutnya LULUS.
const KELUARAN_MENTAH_PSQL = 'KELUARAN-MENTAH-PSQL-TIDAK-BOLEH-TAMPIL';
const ARTEFAK_CEK = 'checks/check_m02_KUNCI.sql';
const hasilCek = (ke) => {
	const gagal = ke === 1;
	const findings = [
		{ status: 'LULUS', title: 'B1 dim_pelanggan ada', detail: null },
		{ status: gagal ? 'GAGAL' : 'LULUS', title: 'B2 grain fakta_penjualan <b>unik</b>', detail: gagal ? 'ada 3 baris ganda' : null },
		{ status: 'LEWAT', title: 'B3 opsional', detail: null },
	];
	return { status: gagal ? 'GAGAL' : 'LULUS', summary: gagal ? '1 dari 2 butir lulus' : '2 butir lulus', passed: !gagal, durationSeconds: 0.6, findings };
};
// Tautan GitHub (device flow dijalankan "Local Runner" lewat relay; di sini relay palsu yang menjawab).
// Skenario per `github.auth_start`: ke-1 tertaut, ke-2 ditolak di GitHub, ke-3 menunggu selamanya (dibatalkan).
const TOKEN_GITHUB = 'ghu_TOKEN_GITHUB_UJI_TIDAK_BOLEH_SAMPAI_KE_EKSTENSI';
const gh = { akun: null, mulai: 0, tagih: 0, dicabut: 0 };

// Fase 3: modul kedua untuk "Siapkan berkas modul", checkpoint, dan pengumpulan.
const MODUL2 = 'm02-lanjut';
const NAMA_STARTER2 = `M02_${USERNAME}.ipynb`;
const ISI_STARTER2 = JSON.stringify({ ...notebook, cells: [sel("print('starter modul 2')")] }, null, 1);
const ISI_CATATAN = '# Catatan modul 2\n\nDisalin dari server.\n';
const BERKAS_MODUL2 = {
	'M02_starter.ipynb': { path: 'notebooks/M02_starter.ipynb', kind: 'Notebook', isi: ISI_STARTER2 },
	'catatan.md': { path: 'docs/catatan.md', kind: 'Dokumen', isi: ISI_CATATAN },
	'sudah-ada.md': { path: 'docs/sudah-ada.md', kind: 'Dokumen', isi: 'VERSI SERVER - tidak boleh menimpa\n' },
	'data.csv': { path: 'data/data.csv', kind: 'Dataset', isi: 'a,b\n1,2\n' },
};
const ISI_SUDAH_ADA = 'pekerjaan mahasiswa, jangan ditimpa\n';
fs.writeFileSync(path.join(wsCourse, 'sudah-ada.md'), ISI_SUDAH_ADA);
// Fase 5–6: naskah modul 2 (penanda @berkas, @jalankan, gambar, rumus, dan HTML mentah yang harus di-escape),
// pengumuman, kurikulum Kelas, dan Bravais.
const NASKAH2 = [
	'# Modul 2: Lanjut', '', '## Tujuan', '', 'Naskah uji dengan rumus $`x^2`$ dan gambar.', '', '![Alur data uji](alur-data.png)', '',
	'## Langkah', '', '```sql', '-- @berkas modul-02/catatan-naskah.sql', 'SELECT 42 AS jawaban;', '```', '',
	'```bash', '# @jalankan', 'psql -X -q -p 5434 -d nusamart_dw -v ON_ERROR_STOP=1 -v batch=1 \\', '  -f modul-02/muat.sql', '```', '',
	'## Penutup', '', '<script>window.bocor = 1</script> dan [dokumentasi](https://www.postgresql.org/docs/).', '',
].join('\n');
const ISI_BERKAS_NASKAH = 'SELECT 42 AS jawaban;\n';
const JAWABAN_BRAVAIS = 'Sel itu mencetak teks.\n\n```python\nprint("contoh")\n```\n';
const bravais = { tagihan: 0 };
// --- Fase 7: Sosial palsu (bentuk jawaban seperti social_http.py; SSE sungguhan; aset avatar publik tanpa bearer) ---
const sosialPalsu = { aliran: new Set(), pesan: [], sseOtorisasi: [], didorongLewatSse: 0, avatarDiminta: [], n: 0 };
function jawabSosial(req, res, badan, jawab, token) {
	const u = new URL(req.url, 'http://x');
	if (u.pathname.startsWith('/social-presence/')) {
		// Aset publik memang tanpa bearer: dicatat terpisah, bukan di `permintaan` (yang semuanya wajib ber-token).
		permintaan.pop();
		sosialPalsu.avatarDiminta.push({ jalur: u.pathname, otorisasi: req.headers.authorization, cookie: req.headers.cookie });
		if (u.pathname !== '/social-presence/catalog/rabbit-128.png') {
			jawab(404, { error: 'tidak_ditemukan', message: 'Tidak ditemukan.' });
			return true;
		}
		res.statusCode = 200;
		res.setHeader('Content-Type', 'image/png');
		res.end(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64'));
		return true;
	}
	if (!u.pathname.startsWith('/api/me/social/')) return false;
	if (req.headers.authorization !== `Bearer ${token}`) return false; // ditolak 401 oleh penjaga umum di bawah
	const k = `${req.method} ${u.pathname}`;
	const baru = (dari, ke, body) => {
		const m = { id: `smsg-${String(++sosialPalsu.n).padStart(4, '0')}`, fromUserId: dari, toUserId: ke, body, kind: 'text', meta: null, createdAt: new Date().toISOString(), readAt: null };
		sosialPalsu.pesan.push(m);
		return m;
	};
	if (k === 'GET /api/me/social/events') {
		sosialPalsu.sseOtorisasi.push({ otorisasi: req.headers.authorization, cookie: req.headers.cookie, terima: req.headers.accept });
		res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-cache, no-store, no-transform' });
		res.write(': connected\n\n');
		sosialPalsu.aliran.add(res);
		res.on('close', () => sosialPalsu.aliran.delete(res));
		return true;
	}
	if (k === 'GET /api/me/social/peers') {
		jawab(200, {
			peers: [
				{ id: 'usr-rani', displayName: 'Rani <b>Uji</b>', username: '122450002', status: 'online', online: true, avatarId: 'rabbit', colorTheme: 'coral', accessory: null, presenceState: 'active', lastSeenAt: new Date().toISOString(), statusText: 'Fokus belajar', statusTextAt: null, slot: 0 },
				{ id: 'usr-dimas', displayName: 'Dimas', username: '122450003', status: 'away', online: false, avatarId: 'smiley', colorTheme: 'blue', accessory: null, presenceState: 'last_seen', lastSeenAt: '2026-10-01T03:00:00+00:00', statusText: null, statusTextAt: null, slot: 1 },
			],
			kelasIds: ['k1'],
		});
		return true;
	}
	if (k === 'GET /api/me/social/avatar') return jawab(200, { avatar: { avatarId: 'smiley', colorTheme: 'blue', displayName: 'Mahasiswa Uji', accessory: null }, status: 'online', configured: true, statusText: null, statusTextAt: null }), true;
	if (k === 'GET /api/me/social/conversations') return jawab(200, { conversations: [] }), true;
	if (k === 'GET /api/me/social/inbox') return jawab(200, { pokes: [], messages: sosialPalsu.pesan.filter((m) => m.toUserId === 'u-uji' && m.createdAt > (u.searchParams.get('since') ?? '')), serverTime: new Date().toISOString() }), true;
	if (k === 'GET /api/me/social/messages') {
		const p = u.searchParams.get('with');
		return jawab(200, { messages: sosialPalsu.pesan.filter((m) => m.fromUserId === p || m.toUserId === p), peerId: p }), true;
	}
	if (k === 'POST /api/me/social/messages') {
		const m = baru('u-uji', badan.toUserId, String(badan.body).trim());
		jawab(201, { message: m });
		// Pesan masuk tiruan dari teman LAIN, didorong lewat SSE sesaat kemudian.
		setTimeout(() => {
			const masuk = baru('usr-dimas', 'u-uji', 'ISI-RAHASIA-PESAN-MASUK');
			for (const r of sosialPalsu.aliran) {
				r.write(`event: chat:message\ndata: ${JSON.stringify({ message: masuk, fromDisplayName: 'Dimas' })}\n\n`);
				sosialPalsu.didorongLewatSse += 1;
			}
		}, 400);
		return true;
	}
	if (k === 'POST /api/me/social/typing') return jawab(204, {}), true;
	return false;
}
const lessonSelesai = [];
const sha256 = (b) => createHash('sha256').update(b).digest('hex');
const unggahan = {}; // objectId → { sha256, panjang }
// Berkas saya (0.1.8): objek `user_archive` milik akun uji. Dua sudah ada di "server": satu bernama
// berbahaya, satu ZIP berisi jalur keluar folder — keduanya harus aman saat diimpor.
const { buatZip: buatZipUji } = await import('./lib/zip.mjs');
const arsip = new Map([
	['awal-1', { nama: '../../../keluar-dari-folder.txt', tipe: 'text/plain', data: Buffer.from('isi berkas bernama berbahaya') }],
	['awal-2', { nama: 'jebakan.zip', tipe: 'application/zip', data: buatZipUji([{ nama: 'aman.txt', isi: Buffer.from('aman') }, { nama: '../../jebakan-keluar.txt', isi: Buffer.from('keluar') }]) }],
]);
const publikArsip = (id, o) => ({ id, displayFilename: o.nama, contentType: o.tipe, sizeBytesVerified: o.data.length, sha256: sha256(o.data), status: 'available', purpose: 'user_archive', createdAt: '2026-10-10T01:00:00+00:00' });
const relayHasil = new Map();
let gitDiperiksa = false;
const lampiran = [];

// PYTHONPATH seperti scripts/run-tests.sh: src semua paket.
const pypath = [];
for (const grup of ['packages', 'providers', 'services', 'agent']) {
	const g = path.join(repo, grup);
	if (!fs.existsSync(g)) continue;
	for (const n of fs.readdirSync(g)) {
		const src = path.join(g, n, 'src');
		if (fs.existsSync(src)) pypath.push(src);
	}
}
// DSW_AGENT_PAYLOAD=1: agent diimpor HANYA dari agent-payload/ ekstensi yang dimuat
// (membuktikan payload lengkap), bukan dari sumber repo.
const pakaiPayload = process.env.DSW_AGENT_PAYLOAD === '1';
if (pakaiPayload) {
	pypath.length = 0;
	pypath.push(path.join(akarMuat, 'agent-payload', 'src'));
	if (!fs.existsSync(path.join(pypath[0], 'workbench_agent', 'cli.py'))) {
		console.error(`agent-payload belum ada di ${akarMuat}. Jalankan: node scripts/bundle-agent.mjs`);
		process.exit(2);
	}
}
const envAgent = { PYTHONPATH: pypath.join(path.delimiter), PYTHONDONTWRITEBYTECODE: '1', DSW_KATALOG_PALSU: katalogPgd };
const harness = FIXTURE_AGENT;
const perintahAgent = [PYTHON, harness, ws, catatanAgent, pemicu];

// --- server HTTP palsu (Control API) ---------------------------------------
const permintaan = [];
// Semua operasi kelas A, tetapi amplop TANPA `services` (seperti server dengan flag
// `services.postgres` mati): operasi SQL harus ditolak agent dengan kode yang jelas.
const OPS = JSON.parse(fs.readFileSync(OPS_JSON, 'utf8')).ops;
const server = http.createServer((req, res) => {
	const bagian = [];
	req.on('data', (d) => bagian.push(d));
	req.on('end', () => {
		const byte = Buffer.concat(bagian);
		const biner = req.headers['content-type'] === 'application/octet-stream';
		const mentah = biner ? '' : byte.toString('utf8');
		let badan;
		try {
			badan = mentah ? JSON.parse(mentah) : undefined;
		} catch {
			badan = mentah;
		}
		if (biner) badan = { sha256: sha256(byte), panjang: byte.length };
		permintaan.push({ metode: req.method, jalur: req.url, otorisasi: req.headers.authorization, cookie: req.headers.cookie, badan });
		const jawab = (status, isi) => {
			res.statusCode = status;
			res.setHeader('Content-Type', 'application/json');
			res.end(JSON.stringify(isi));
		};
		// Fase 7: rute Sosial dan aset avatar publik (yang terakhir memang tanpa bearer).
		if (jawabSosial(req, res, badan, jawab, TOKEN)) return;
		if (req.headers.authorization !== `Bearer ${TOKEN}`) return jawab(401, { error: 'autentikasi_gagal', message: 'Autentikasi gagal.' });
		if (req.method === 'POST' && req.url === '/api/app/session-envelope') {
			const kini = new Date();
			const amplop = {
				envelopeId: `env-${permintaan.length}`,
				courseId: badan.courseId,
				workspace: { layout: 'per-course', courseId: badan.courseId },
				// Hanya mata kuliah berbasis data yang amplopnya memuat `services` (seperti server sungguhan).
				...(badan.courseId === COURSE_PGD ? { services: SERVICES_PGD } : {}),
				ops: OPS,
				issuedAt: kini.toISOString(),
				expiresAt: new Date(kini.getTime() + 3600_000).toISOString(),
			};
			// "Relay": agent mengambilnya pada putaran poll berikutnya (tulis atomik).
			const sementara = path.join(pemicu, `.tmp-${amplop.envelopeId}`);
			fs.writeFileSync(sementara, JSON.stringify(amplop));
			fs.renameSync(sementara, path.join(pemicu, `amplop-${amplop.envelopeId}.json`));
			return jawab(202, { envelopeId: amplop.envelopeId, courseId: amplop.courseId, issuedAt: amplop.issuedAt, expiresAt: amplop.expiresAt, ops: OPS, messageId: 'm-1' });
		}
		if (req.method === 'GET' && req.url === '/api/catalog/courses') {
			return jawab(200, {
				courses: [{
					id: COURSE,
					workspaceLayout: 'per-course',
					defaultRuntimeProfileId: 'python-data-science',
					outputNaming: { notebook: 'M{module}_{nim}.ipynb' },
					modules: [
						{ id: 'm01-pengantar', order: 1, runtimeProfile: 'python-data-science', starterFiles: ['M01_starter.ipynb'] },
						{ id: MODUL2, order: 2, runtimeProfile: 'python-data-science', starterFiles: ['notebooks/M02_starter.ipynb'], capabilities: [], checkpointArtifact: 'checks/check_m02.py' },
					],
				}, {
					id: COURSE_PGD,
					name: 'Pergudangan Data',
					workspaceLayout: 'per-course',
					modules: [
						{ id: 'module-00', name: 'Pengantar', order: 0, capabilities: [] },
						{ id: MODUL_PGD, name: 'Skema Bintang', order: 2, capabilities: ['postgres'], checkpointArtifact: ARTEFAK_CEK },
						{ id: 'module-03', name: 'ETL', order: 3, capabilities: ['postgres'], checkpointArtifact: 'checks/check_m03.sql' },
						// Tidak dirujuk lesson mana pun dan tanpa pemeriksa: server menganggapnya terbuka.
						{ id: 'module-04', name: 'SCD', order: 4, capabilities: ['postgres'], checkpointArtifact: null },
					],
				}],
			});
		}
		if (req.method === 'GET' && req.url === '/api/auth/me') return jawab(200, { user: { id: 'u-uji', username: USERNAME } });
		// Kelas Pergudangan Data (mahasiswa, bukan staf): kurikulumnya menentukan modul mana yang sudah dirilis.
		if (req.method === 'GET' && req.url === '/api/me/kelas') return jawab(200, { kelas: [{ id: 'k-pgd', courseId: COURSE_PGD, title: 'Pergudangan Data RA', active: true, courseName: 'Pergudangan Data', staffRole: null }] });
		if (req.method === 'GET' && req.url === '/api/me/kelas/k-pgd/kurikulum') {
			const l = (id, isi) => ({ id, moduleId: 'kmp', courseId: COURSE_PGD, title: id, sortOrder: 1, ...isi });
			return jawab(200, {
				courseId: COURSE_PGD, kelasId: 'k-pgd', previewMode: false, staffRole: null,
				modules: [{
					id: 'kmp', courseId: COURSE_PGD, title: 'Pertemuan', sortOrder: 1, release: { locked: false, releaseAt: null },
					lessons: [
						l('l-lab-02', { type: 'lab', packageModuleId: MODUL_PGD, locked: false, release: { locked: false, releaseAt: null } }),
						// Lesson terkunci: server hanya mengirim judul (tanpa packageModuleId), persis `LOCKED_SAFE_FIELDS`.
						l('l-lab-03', { type: 'lab', locked: true, contentHidden: true, release: { locked: true, releaseAt: '2026-10-12T01:00:00+00:00' } }),
						// Kuis merujuk modul 03 hanya untuk pengelompokan: tidak membuka modulnya.
						l('l-kuis-03', { type: 'quiz', packageModuleId: 'module-03', locked: false, release: { locked: false, releaseAt: null } }),
					],
				}],
			});
		}
		// Tautan GitHub: status (baca saja). DELETE tidak ada di cakupan token aplikasi → 403 bila dipanggil.
		if (req.url === '/api/me/integrations/github') {
			if (req.method !== 'GET') return jawab(403, { error: 'di_luar_cakupan_aplikasi', message: 'Di luar cakupan token aplikasi.' });
			return jawab(200, { enabled: true, configured: true, appType: 'github-app', appName: 'Workbench Uji', appSlug: null, account: gh.akun, repoCreate: false, visibilityPolicy: 'private', bindings: [] });
		}
		// --- Fase 5: naskah, gambar, kurikulum, pengumuman ---
		if (req.method === 'GET' && req.url === `/api/courses/${COURSE}/modules/${MODUL2}/material`) {
			res.statusCode = 200;
			res.setHeader('Content-Type', 'text/markdown; charset=utf-8');
			return res.end(NASKAH2);
		}
		if (req.method === 'GET' && req.url === `/api/courses/${COURSE}/modules/${MODUL2}/figures/alur-data.png`) {
			res.statusCode = 200;
			res.setHeader('Content-Type', 'image/png');
			return res.end(Buffer.from(PNG_B64, 'base64'));
		}
		if (req.method === 'GET' && req.url === '/api/me/kelas/k1/kurikulum') {
			return jawab(200, { courseId: COURSE, kelasId: 'k1', modules: [{ id: 'km2', courseId: COURSE, title: 'Modul 2', sortOrder: 2, lessons: [{ id: 'l-baca', moduleId: 'km2', courseId: COURSE, title: 'Bacaan Modul 2', type: 'reading', sortOrder: 1, packageModuleId: MODUL2, completed: false }] }] });
		}
		if (req.method === 'POST' && req.url === '/api/me/kelas/k1/lessons/l-baca/complete') {
			lessonSelesai.push(req.url);
			return jawab(200, { ok: true, progress: { lessonId: 'l-baca', completed: true } });
		}
		if (req.method === 'GET' && req.url === '/api/me/announcements') {
			return jawab(200, { announcements: [{ id: 'ann-1', title: 'Pemeliharaan server <uji>', body: 'Sabtu pukul 22.00 server berhenti sebentar.', severity: 'warning', startsAt: '2026-10-01T00:00:00+00:00', endsAt: null }] });
		}
		// --- Fase 6: Bravais (bentuk jawaban seperti companion_http.py) ---
		if (req.method === 'GET' && req.url === '/api/me/companion/config') {
			return jawab(200, { name: 'Bravais', assistant: true, webSearch: false, playlists: [], quota: { dailyQueries: 20, usedToday: 1, unlimited: false, dailyUnits: null, usedUnitsToday: 0, llmAllowed: true } });
		}
		if (req.method === 'POST' && req.url === '/api/me/companion/chats') return jawab(201, { chat: { id: 'c-uji', title: badan.title, courseId: badan.courseId ?? null, moduleId: badan.moduleId ?? null, createdAt: 'x', updatedAt: 'x', messages: 0 } });
		if (req.method === 'POST' && req.url === '/api/me/companion/chats/c-uji/messages') return jawab(201, { message: { id: 'm', role: badan.role, content: badan.content } });
		if (req.method === 'POST' && req.url === '/api/me/companion/assistant') return jawab(202, { route: 'pending', jobId: 'aj-0a1b', pollMs: 500 });
		if (req.method === 'GET' && req.url === '/api/me/companion/assistant/jobs/aj-0a1b') {
			bravais.tagihan += 1;
			if (bravais.tagihan < 2) return jawab(200, { route: 'pending', state: 'running' });
			return jawab(200, { answer: JAWABAN_BRAVAIS, sources: [], toolsUsed: [], route: 'llm', tier: 'mini', actions: [] });
		}
		if (req.method === 'POST' && (req.url === '/api/me/activity/notebook-opened' || req.url === '/api/me/companion/events')) return jawab(200, { ok: true });
		// --- Fase 3 ---
		const kunci = `${req.method} ${req.url}`;
		const modul2 = `/api/courses/${COURSE}/modules/${MODUL2}`;
		if (kunci === `GET ${modul2}`) {
			return jawab(200, {
				id: MODUL2, name: 'Modul 2', order: 2, course: { id: COURSE, version: '2026.1' }, workFolder: null,
				files: Object.entries(BERKAS_MODUL2).map(([name, f]) => ({ name, path: f.path, kind: f.kind, label: name, description: '', workPath: null })),
			});
		}
		if (req.method === 'GET' && req.url.startsWith(`${modul2}/files/`)) {
			const u = new URL(req.url, 'http://x');
			const f = BERKAS_MODUL2[decodeURIComponent(u.pathname.split('/').pop())];
			if (!f || u.searchParams.get('path') !== f.path) return jawab(404, { error: 'tidak_ditemukan', message: 'Berkas tidak ditemukan.' });
			res.statusCode = 200;
			res.setHeader('Content-Type', 'application/octet-stream');
			return res.end(f.isi);
		}
		if (kunci === 'GET /api/me/tasks') {
			return jawab(200, {
				tasks: [
					{ assignmentId: 'a-m02', courseId: COURSE, moduleId: MODUL2, title: 'Tugas Modul 2', dueAt: '2030-01-01T00:00:00+00:00', attemptStatus: 'none', attemptId: null, gradeReleased: false, grade: null, href: `/courses/${COURSE}/modules/${MODUL2}/tasks?kelasId=k1`, release: { locked: false, releaseAt: null, lockSource: null } },
					// Tugas modul ber-basis data (pemeriksa SQL): di panel Tugas tanpa tombol "Mode tugas".
					{ assignmentId: 'a-pgd-02', courseId: COURSE_PGD, moduleId: MODUL_PGD, title: 'Tugas Skema Bintang', dueAt: '2030-02-01T00:00:00+00:00', attemptStatus: 'none', attemptId: null, gradeReleased: false, grade: null, href: `/courses/${COURSE_PGD}/modules/${MODUL_PGD}/tasks?kelasId=k-pgd`, release: { locked: false, releaseAt: null, lockSource: null } },
				],
			});
		}
		if (kunci === `GET ${modul2}/assignments`) {
			return jawab(200, { assignments: [{ id: 'a-m02', courseId: COURSE, moduleId: MODUL2, title: 'Tugas Modul 2', requiredArtifacts: [{ name: 'M02_NIM.ipynb', label: 'Notebook', contentTypes: ['application/x-ipynb+json'] }, { name: 'catatan.md', label: 'Catatan', contentTypes: ['text/markdown'] }], analysisQuestions: [] }], submission: { manualUpload: false } });
		}
		const percobaan = `/api/courses/${COURSE}/assignments/a-m02/attempts`;
		if (kunci === `GET ${percobaan}`) return jawab(200, { attempts: [] });
		if (kunci === `POST ${percobaan}`) return jawab(201, { attempt: { id: 'att-uji', assignmentId: 'a-m02', status: 'draft', artifacts: [], answers: {} } });
		// -- Berkas saya ---------------------------------------------------------------
		if (kunci === 'GET /api/me/objects') {
			const objects = [...arsip.entries()].filter(([, o]) => o.data).map(([id, o]) => publikArsip(id, o));
			const usedBytes = objects.reduce((a, o) => a + o.sizeBytesVerified, 0);
			return jawab(200, { objects, usage: { usedBytes, limitBytes: 1024 ** 3, remainingBytes: 1024 ** 3 - usedBytes } });
		}
		if (kunci === 'POST /api/objects/upload/reserve' && badan?.purpose === 'user_archive') {
			const objectId = `ars-${arsip.size + 1}`;
			arsip.set(objectId, { nama: badan.displayFilename, tipe: badan.contentType, pesan: badan });
			return jawab(201, { sessionId: `ses-${objectId}`, objectId, expiresAt: 'x' });
		}
		let ma;
		if ((ma = /^POST \/api\/objects\/upload\/ses-(ars-\d+)\/initiate$/.exec(kunci))) return jawab(200, { sessionId: `ses-${ma[1]}`, method: 'PUT', url: `localfs://put/${ma[1]}`, headers: {}, maxBytes: 50 * 1024 * 1024 });
		if ((ma = /^PUT \/api\/objects\/local\/(ars-\d+)$/.exec(kunci)) && arsip.has(ma[1])) {
			arsip.get(ma[1]).data = byte;
			res.statusCode = 204;
			return res.end();
		}
		if ((ma = /^POST \/api\/objects\/upload\/ses-(ars-\d+)\/finalize$/.exec(kunci))) {
			if (arsip.get(ma[1])?.data?.length !== badan.sizeBytes) return jawab(400, { error: 'permintaan_tidak_sah', message: 'Ukuran tidak cocok.' });
			return jawab(200, { object: publikArsip(ma[1], arsip.get(ma[1])) });
		}
		if ((ma = /^POST \/api\/objects\/((?:ars|awal)-\d+)\/download-grant$/.exec(kunci)) && arsip.get(ma[1])?.data) {
			return jawab(200, { objectId: ma[1], method: 'GET', url: `/api/objects/local/${ma[1]}`, expiresAt: 'x', headers: {} });
		}
		if ((ma = /^GET \/api\/objects\/local\/((?:ars|awal)-\d+)$/.exec(kunci)) && arsip.get(ma[1])?.data) {
			res.statusCode = 200;
			res.setHeader('Content-Type', 'application/octet-stream');
			return res.end(arsip.get(ma[1]).data);
		}
		if (kunci === 'POST /api/objects/upload/reserve') {
			const objectId = `obj-${Object.keys(unggahan).length + 1}`;
			unggahan[objectId] = { nama: badan.displayFilename };
			return jawab(200, { sessionId: `ses-${objectId}`, objectId, expiresAt: 'x' });
		}
		let m;
		if ((m = /^POST \/api\/objects\/upload\/ses-(obj-\d+)\/initiate$/.exec(kunci))) return jawab(200, { sessionId: `ses-${m[1]}`, method: 'PUT', url: `/api/objects/local/${m[1]}`, headers: {}, maxBytes: 1e7 });
		if ((m = /^PUT \/api\/objects\/local\/(obj-\d+)$/.exec(kunci)) && unggahan[m[1]]) {
			Object.assign(unggahan[m[1]], badan);
			return jawab(200, { ok: true });
		}
		if ((m = /^POST \/api\/objects\/upload\/ses-(obj-\d+)\/finalize$/.exec(kunci))) {
			if (unggahan[m[1]].panjang !== badan.sizeBytes) return jawab(400, { error: 'permintaan_tidak_sah', message: 'Ukuran tidak cocok.' });
			return jawab(200, { object: { id: m[1], status: 'available' } });
		}
		if (kunci === 'POST /api/attempts/att-uji/artifacts') {
			lampiran.push(badan);
			return jawab(200, { attempt: { id: 'att-uji', assignmentId: 'a-m02', status: 'draft', artifacts: lampiran } });
		}
		if (kunci === 'GET /api/me/integrations/github/submit-gate?assignmentId=a-m02') {
			// Gerbang berlaku; baru terpenuhi setelah `git.status` datang LEWAT RELAY.
			return jawab(200, { assignmentId: 'a-m02', courseId: COURSE, applies: true, exempt: false, ok: gitDiperiksa, reason: gitDiperiksa ? null : 'no_proof', message: gitDiperiksa ? null : 'Push ke GitHub dulu.', proof: null });
		}
		if (kunci === `GET /api/courses/${COURSE_PGD}/modules/${MODUL_PGD}`) {
			return jawab(200, {
				id: MODUL_PGD, name: 'Skema Bintang', order: 2, course: { id: COURSE_PGD, version: '2026.3' }, workFolder: 'modul-02', localDataset: true,
				files: [{ name: '01_ddl.sql', path: 'sql/01_ddl.sql', kind: 'SQL', label: '01_ddl.sql', description: '', workPath: 'modul-02/01_ddl.sql' }, { name: '02_muat.sql', path: 'sql/02_muat.sql', kind: 'SQL', label: '02_muat.sql', description: '', workPath: 'modul-02/02_muat.sql' }],
			});
		}
		// Modul lain mata kuliah itu belum dibuka pengampu: pesan server tampil apa adanya di panel.
		if (kunci === `GET /api/courses/${COURSE_PGD}/modules/module-03`) return jawab(403, { error: 'tidak_berhak', message: 'Materi modul ini belum dirilis dosen. Dirilis 12-10-2026 08.00 WIB.' });
		if (kunci === `GET /api/courses/${COURSE_PGD}/modules/module-04`) return jawab(200, { id: 'module-04', name: 'SCD', order: 4, course: { id: COURSE_PGD, version: '2026.3' }, workFolder: 'modul-04', localDataset: false, files: [] });
		// Riwayat checkpoint modul PGD: diisi "server" saat job sql.check selesai (seperti `_catat_sql_check`).
		if (kunci === `GET /api/me/courses/${COURSE_PGD}/modules/${MODUL_PGD}/checkpoint-runs`) return jawab(200, { runs: [...pgd.riwayat].reverse() });
		let mc;
		if ((mc = /^GET \/api\/relay\/jobs\/(job-cek-\d+)\?since=(\d+)$/.exec(kunci)) && pgd.cek.has(mc[1])) {
			const j = pgd.cek.get(mc[1]);
			j.tagihan += 1;
			if (j.batal) return jawab(200, { jobId: mc[1], state: 'cancelled', elapsedMs: 700, output: [], nextSince: 0, result: null, reason: 'cancelled_by_user', detail: 'Kueri dihentikan.' });
			if (j.tagihan < 2 || j.macet) return jawab(200, { jobId: mc[1], state: 'running', elapsedMs: 800 * j.tagihan, output: [], nextSince: 0, result: null, reason: null, detail: null });
			// Basis data mati: agent menolak dengan kode kontrak, tanpa butir.
			if (!j.menyala) return jawab(200, { jobId: mc[1], state: 'failed', elapsedMs: 900, output: [], nextSince: 0, result: { code: 'service_stopped', alias: 'dw' }, reason: 'error', detail: null });
			const result = hasilCek(j.ke);
			if (!j.dicatat) {
				j.dicatat = true;
				const counts = {};
				for (const f of result.findings) counts[f.status] = (counts[f.status] ?? 0) + 1;
				pgd.riwayat.push({ id: `run-${pgd.riwayat.length + 1}`, courseId: COURSE_PGD, moduleId: MODUL_PGD, status: result.status, findingCounts: counts, durationMs: 600, createdAt: new Date().toISOString(), attemptId: null, mode: null });
			}
			return jawab(200, { jobId: mc[1], state: 'succeeded', elapsedMs: 1700, output: [{ type: 'stream', name: 'stdout', text: KELUARAN_MENTAH_PSQL }], nextSince: 40, result: { result, output: KELUARAN_MENTAH_PSQL, seconds: 0.6, artifact: ARTEFAK_CEK, exitCode: 0 }, reason: null, detail: null });
		}
		let mj;
		if ((mj = /^GET \/api\/relay\/jobs\/(job-pgd-\d+)\?since=(\d+)$/.exec(kunci)) && pgd.job.has(mj[1])) {
			const j = pgd.job.get(mj[1]);
			j.tagihan += 1;
			const log = j.op === 'dataset.materialize' ? 'Mengunduh nusamart-oltp 2026.1 …\nSelesai: 9 berkas di data/raw/.\n' : 'Memeriksa berkas dataset (SHA-256) …\nCOPY pelanggan\n';
			const since = Number(mj[2]);
			if (j.tagihan < 2) return jawab(200, { jobId: mj[1], state: 'running', elapsedMs: 800, output: [{ type: 'stream', name: 'stdout', text: log.slice(since, 20) }], nextSince: 20, result: null, reason: null, detail: null });
			if (j.op === 'dataset.materialize') pgd.dataset = true;
			else pgd.dimuat = true;
			return jawab(200, { jobId: mj[1], state: 'succeeded', elapsedMs: 1900, output: [{ type: 'stream', name: 'stdout', text: log.slice(since) }], nextSince: log.length, result: j.op === 'dataset.materialize' ? { filesWritten: 9 } : { alias: 'source', tables: 7, rows: 12345 }, reason: null, detail: null });
		}
		if (kunci === 'POST /api/relay/dispatch' && badan?.payload?.courseId === COURSE_PGD) {
			// Relay "kelas B" untuk panel Pergudangan Data (bentuk jawaban seperti agent: pgservice.status, datasets_op.verify).
			const messageId = `rly-${relayHasil.size + 1}`;
			const op = badan.operation;
			let result = { status: 'rejected', payload: {}, detail: 'operasi tidak dikenal uji' };
			let jobId;
			const status = () => ({
				service: 'postgres', version: '17', installed: true,
				clusters: [
					{ alias: 'source', port: 5433, initialized: true, state: pgd.menyala ? 'running' : 'stopped', access: 'read', databases: [{ name: 'nusamart_oltp', ...(pgd.dimuat ? { loaded: { dataset: 'nusamart-oltp', version: '2026.1', rows: 12345, loadedAt: '2026-10-10T00:00:00+00:00' } } : {}) }] },
					{ alias: 'dw', port: 5434, initialized: true, state: pgd.menyala ? 'running' : 'stopped', access: 'owner', databases: [{ name: 'nusamart_dw' }] },
				],
			});
			if (op === 'service.status') result = { status: 'ok', payload: status(), detail: null };
			else if (op === 'service.start' || op === 'service.stop') {
				pgd.menyala = op === 'service.start';
				result = { status: 'ok', payload: { ...status(), terminal: '.workbench/shell/terminal.sh' }, detail: null };
			} else if (op === 'dataset.verify') {
				if (badan.payload.moduleId !== MODUL_PGD) return jawab(404, { error: 'tidak_ditemukan', message: 'Modul ini tidak memakai dataset.' });
				result = { status: 'ok', payload: { ref: 'nusamart-oltp', version: '2026.1', mount: 'data/raw', present: pgd.dataset ? 9 : 0, total: 9, ready: pgd.dataset }, detail: null };
			} else if ((op === 'dataset.materialize' || op === 'service.postgres.load') && badan.payload.job === true) {
				if (op === 'service.postgres.load' && !pgd.dataset) result = { status: 'failed', payload: { code: 'dataset_missing' }, detail: 'Dataset belum lengkap atau berubah di laptop.' };
				else {
					jobId = `job-pgd-${pgd.job.size + 1}`;
					pgd.job.set(jobId, { op, tagihan: 0 });
					result = { status: 'ok', payload: { jobId, state: 'queued' }, detail: null };
				}
			} else if (op === 'sql.check' && badan.payload.job === true) {
				// Kunci rilis modul ditegakkan server (403), pemeriksa hanya ada untuk modul 02 (404 selain itu).
				if (badan.payload.moduleId === 'module-03') return jawab(403, { error: 'tidak_berhak', message: 'Materi modul ini belum dirilis dosen. Dirilis 12-10-2026 08.00 WIB.' });
				if (badan.payload.moduleId !== MODUL_PGD) return jawab(404, { error: 'tidak_ditemukan', message: 'Modul ini tidak memiliki pemeriksa SQL.' });
				jobId = `job-cek-${pgd.cek.size + 1}`;
				const ke = [...pgd.cek.values()].filter((x) => x.menyala && !x.macet).length + 1;
				// Pemeriksaan ke-4 yang sah sengaja "macet" agar dapat dihentikan (job.cancel).
				pgd.cek.set(jobId, { tagihan: 0, menyala: pgd.menyala, ke, macet: pgd.menyala && ke === 4 });
				result = { status: 'ok', payload: { jobId, state: 'queued' }, detail: null };
			}
			relayHasil.set(messageId, result);
			return jawab(202, { messageId, operation: op, queueDepth: 0, ...(jobId ? { jobId, timeoutSeconds: 120 } : {}) });
		}
		if (kunci === 'POST /api/relay/dispatch') {
			const messageId = `rly-${relayHasil.size + 1}`;
			let result = { status: 'rejected', payload: {}, detail: 'operasi tidak dikenal uji' };
			if (badan.operation === 'git.status') {
				gitDiperiksa = true;
				result = { status: 'ok', payload: { initialized: true, clean: true, ahead: 0, behind: 0, branch: 'main' }, detail: null };
			} else if (badan.operation === 'checkpoint.run') {
				result = { status: 'ok', payload: { result: { status: 'LULUS', summary: '2 butir lulus', passed: true, durationSeconds: 0.4, findings: [{ status: 'LULUS', title: 'Berkas ada' }, { status: 'LULUS', title: 'Sel pertama berjalan' }] } }, detail: null };
			} else if (badan.operation === 'job.cancel' && pgd.cek.has(badan.payload?.jobId)) {
				pgd.cek.get(badan.payload.jobId).batal = true;
				result = { status: 'ok', payload: { jobId: badan.payload.jobId, state: 'cancelling' }, detail: null };
			} else if (badan.operation === 'github.auth_start') {
				gh.mulai += 1;
				gh.tagih = 0;
				result = { status: 'ok', payload: { userCode: `UJI${gh.mulai}-KODE`, verificationUri: gh.mulai === 2 ? 'https://jahat.example/login/device' : 'https://github.com/login/device', expiresIn: 900, interval: 1, appName: 'Workbench Uji', appType: 'github-app' }, detail: null };
			} else if (badan.operation === 'github.auth_poll') {
				gh.tagih += 1;
				if (gh.mulai === 1 && gh.tagih >= 2) {
					// "Server" mencatat tautan dari hasil relay perangkat (github_http.record_relay_result).
					gh.akun = { githubUserId: 4242, login: 'mhs-uji', avatarUrl: null, linkedAt: new Date().toISOString(), deviceId: badan.deviceId };
					result = { status: 'ok', payload: { state: 'linked', account: { githubUserId: 4242, login: 'mhs-uji', avatarUrl: null }, accessToken: TOKEN_GITHUB }, detail: null };
				} else if (gh.mulai === 2) result = { status: 'ok', payload: { state: 'denied' }, detail: null };
				else result = { status: 'ok', payload: { state: 'pending', interval: 1 }, detail: null };
			} else if (badan.operation === 'github.auth_revoke') {
				gh.dicabut += 1;
				gh.akun = null;
				result = { status: 'ok', payload: { revokedLocal: true, revokedRemote: true }, detail: null };
			}
			relayHasil.set(messageId, result);
			return jawab(202, { messageId, operation: badan.operation, queueDepth: 0 });
		}
		if ((m = /^GET \/api\/relay\/results\/(rly-\d+)$/.exec(kunci)) && relayHasil.has(m[1])) {
			return jawab(200, { messageId: m[1], state: 'ready', operation: 'x', result: { messageId: m[1], ...relayHasil.get(m[1]) } });
		}
		if (kunci === 'POST /api/attempts/att-uji/submit') {
			if (!gitDiperiksa) return jawab(403, { error: 'github_push_required', message: 'Push ke GitHub dulu.' });
			return jawab(200, { attempt: { id: 'att-uji', status: 'submitted' }, receipt: { id: 'rcpt-uji', checksum: 'c0ffee0123456789', submittedAt: new Date().toISOString(), late: false } });
		}
		if (kunci === `GET /api/me/courses/${COURSE}/modules/${MODUL2}/checkpoint-runs`) return jawab(200, { runs: [] });
		return jawab(404, { error: 'tidak_ditemukan', message: 'Tidak ditemukan.' });
	});
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const SERVER = `http://127.0.0.1:${server.address().port}`;

// Pembaruan: paket tiruan ber-id sama, versi 99.0.0, dipasang suite lewat perintah pembaruan.
const VERSI_VSIX_UJI = '99.0.0';
const vsixUji = path.join(dir('v'), `dsworkbench-${VERSI_VSIX_UJI}.vsix`);
{
	const { buatZip } = await import('./lib/zip.mjs');
	const pkgMuat = JSON.parse(fs.readFileSync(path.join(akarMuat, 'package.json'), 'utf8'));
	const id = { name: pkgMuat.name, publisher: pkgMuat.publisher };
	fs.writeFileSync(vsixUji, buatZip([
		{ nama: 'extension.vsixmanifest', isi: Buffer.from(`<?xml version="1.0" encoding="utf-8"?>\n<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011"><Metadata><Identity Language="en-US" Id="${id.name}" Version="${VERSI_VSIX_UJI}" Publisher="${id.publisher}"/><DisplayName>DSWorkbench (paket uji)</DisplayName><Description xml:space="preserve">paket uji pembaruan</Description><Properties><Property Id="Microsoft.VisualStudio.Code.Engine" Value="${pkgMuat.engines.vscode}"/></Properties></Metadata><Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation><Dependencies/><Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/></Assets></PackageManifest>\n`) },
		{ nama: '[Content_Types].xml', isi: Buffer.from('<?xml version="1.0" encoding="utf-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension=".json" ContentType="application/json"/><Default Extension=".vsixmanifest" ContentType="text/xml"/></Types>\n') },
		{ nama: 'extension/package.json', isi: Buffer.from(JSON.stringify({ ...id, displayName: 'DSWorkbench (paket uji)', version: VERSI_VSIX_UJI, engines: pkgMuat.engines })) },
	]));
}

// Aplikasi bermerek menolak memasang versi baru dari ekstensi BAWAAN kecuali id-nya terdaftar di
// product.json → builtInExtensionsEnabledWithAutoUpdates (vscode `InstallExtensionTask`, "builtinAutoUpdate").
const produkJson = [process.env.DSW_PRODUK_JSON, path.join(path.dirname(KODE), '..', 'Resources', 'app', 'product.json'), path.join(path.dirname(KODE), 'resources', 'app', 'product.json')].find((j) => j && fs.existsSync(j));
const produk = produkJson ? JSON.parse(fs.readFileSync(produkJson, 'utf8')) : {};
const adaBawaan = produkJson ? fs.existsSync(path.join(path.dirname(produkJson), 'extensions', 'dsworkbench', 'package.json')) : false;
const bolehPerbarui = !adaBawaan || (produk.builtInExtensionsEnabledWithAutoUpdates ?? []).some((id) => String(id).toLowerCase() === 'sditera.dsworkbench');

const cek = [];
const dilewati = [];
const peringatan = [];
let ringkasan = { lulus: false };
let kode = 1;
try {
	// Pairing lewat subperintah `adopt` yang asli (kredensial lewat stdin).
	const adopt = spawnSync(PYTHON, [harness, ws, catatanAgent, pemicu, '--state-dir', stateDir, '--url', SERVER, 'adopt'], {
		input: JSON.stringify({ deviceId: 'dev-uji', credential: RAHASIA, name: 'Laptop Uji', os: os.type(), arch: os.arch(), account: USERNAME }),
		env: { ...process.env, ...envAgent },
		encoding: 'utf8',
	});
	assert.equal(adopt.status, 0, `adopt gagal: ${adopt.stderr}`);
	assert.ok(!(adopt.stdout + adopt.stderr).includes(RAHASIA), 'adopt mencetak kredensial');

	fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
	fs.writeFileSync(
		path.join(userData, 'User', 'settings.json'),
		JSON.stringify({
			'dsworkbench.serverUrl': SERVER,
			'dsworkbench.agent.command': perintahAgent,
			'dsworkbench.agent.stateDir': stateDir,
			'dsworkbench.agent.env': envAgent,
			'security.workspace.trust.enabled': false,
			'telemetry.telemetryLevel': 'off',
			'update.mode': 'none',
			'extensions.autoUpdate': false,
			'workbench.startupEditor': 'none',
		}),
	);

	const argumen = [
		`--extensionDevelopmentPath=${akarMuat}`,
		`--extensionTestsPath=${SUITE}`,
		`--user-data-dir=${userData}`,
		`--extensions-dir=${extDir}`,
		'--disable-workspace-trust',
		'--skip-welcome',
		'--skip-release-notes',
		'--disable-updates',
		'--new-window',
		// Argumen tambahan untuk aplikasi (JSON array), mis. `["--no-sandbox"]` pada AppImage di runner CI.
		...(process.env.DSW_ARG_APLIKASI ? JSON.parse(process.env.DSW_ARG_APLIKASI) : []),
		wsCourse,
	];
	console.log(`VS Code: ${KODE}\nekstensi dari: ${akarMuat}\nagent dari: ${pakaiPayload ? 'agent-payload ekstensi' : 'sumber repo'}\nprofil sementara: ${tmp}\nserver palsu: ${SERVER}`);
	const mulai = Date.now();
	const keluar = await new Promise((selesai) => {
		const anak = spawn(KODE, argumen, {
			stdio: ['ignore', 'pipe', 'pipe'],
			env: { ...process.env, DSW_LUAR: luarDir, DSW_DATA_ROOT: folderRuntime, DSW_OUT: out, DSW_SERVER: SERVER, DSW_TOKEN: TOKEN, DSW_USERNAME: USERNAME, DSW_COURSE: COURSE, DSW_WS_COURSE: wsCourse, DSW_NOTEBOOK: NAMA_NB, DSW_NOTEBOOK2: NAMA_NB2, DSW_NOTEBOOK_LUAR: nbLuar, DSW_MODUL2: MODUL2, DSW_UJI_VSIX: vsixUji, DSW_COURSE_PGD: COURSE_PGD, DSW_MODUL_PGD: MODUL_PGD },
		});
		let log = '';
		anak.stdout.on('data', (d) => (log += d));
		anak.stderr.on('data', (d) => (log += d));
		const pewaktu = setTimeout(() => anak.kill('SIGKILL'), 240_000);
		anak.on('close', (k) => {
			clearTimeout(pewaktu);
			selesai({ kode: k, log });
		});
	});
	console.log(`VS Code selesai (kode ${keluar.kode}) dalam ${((Date.now() - mulai) / 1000).toFixed(1)} dtk`);
	if (!fs.existsSync(out)) {
		console.error(keluar.log.slice(-4000));
		throw new Error('uji di dalam VS Code tidak menulis hasil');
	}
	const h = JSON.parse(fs.readFileSync(out, 'utf8'));
	ringkasan = { ...ringkasan, vscode: h.vscode, app: h.app, temaBawaan: h.tema?.nilaiBawaan, galatSuite: h.galat ? String(h.galat).split('\n')[0] : undefined };
	if (process.env.DSW_SIMPAN_HASIL) fs.writeFileSync(process.env.DSW_SIMPAN_HASIL, JSON.stringify({ hasil: h, permintaan }, null, 1));
	if (h.galat) {
		console.error(JSON.stringify(h, null, 1).slice(0, 6000));
		throw new Error(`uji di dalam VS Code gagal: ${h.galat}`);
	}

	const periksa = (nama, f) => {
		f();
		cek.push(nama);
	};
	periksa('ekstensi dimuat, pengendali terdaftar tanpa ekstensi Jupyter', () => {
		assert.equal(h.jupyterExt, false);
		assert.equal(h.ipynbBawaan, true);
		assert.equal(h.idPengendali, 'dsworkbench-local-runner');
		assert.equal(h.jenisNotebook, 'jupyter-notebook');
		for (const p of ['dsworkbench.login', 'dsworkbench.logout', 'dsworkbench.openCourse', 'dsworkbench.notebook.restartKernel']) assert.ok(h.perintah.includes(p), p);
	});
	periksa('pemasang lingkungan: perintah dan view Status terdaftar, profil terbaca dari payload, folder runtime tidak dibuat saat aktif', () => {
		for (const p of ['dsworkbench.env.setup', 'dsworkbench.env.install', 'dsworkbench.env.update', 'dsworkbench.env.check', 'dsworkbench.env.openFolder', 'dsworkbench.getStarted']) assert.ok(h.perintah.includes(p), p);
		assert.equal(nj(h.lingkungan.folder), nj(folderRuntime));
		// Baris GitHub (0.1.7) hanya ada saat masuk dan fiturnya menyala; diperiksa tersendiri di bawah.
		assert.deepEqual(h.lingkungan.status.filter((x) => x !== 'github'), ['akun', 'agent', 'lingkungan', 'disk', 'folder', 'versi']);
		if (fs.existsSync(path.join(akarMuat, 'agent-payload', 'requirements', 'profiles.json'))) {
			assert.deepEqual(h.lingkungan.profil, [
				{ id: 'python-data-science', keadaan: 'belum_dipasang' },
				{ id: 'python-data-warehouse', keadaan: 'belum_dipasang' },
				{ id: 'python-deep-learning', keadaan: process.platform === 'darwin' && process.arch === 'x64' ? 'tidak_didukung' : 'belum_dipasang' },
			]);
		}
		assert.equal(fs.existsSync(folderRuntime), false, 'folder runtime tidak boleh dibuat hanya karena ekstensi aktif');
	});
	const bermerek = /^\s*DSWorkbench\b/i.test(h.app ?? '');
	periksa(`kedua tema DSWorkbench terdaftar; ${bermerek ? 'di aplikasi bermerek tema gelap diterapkan sekali sebagai bawaan' : 'tidak dipaksakan di VS Code biasa'}`, () => {
		assert.deepEqual(h.tema.terdaftar, [['DSWorkbench Gelap', 'vs-dark'], ['DSWorkbench Terang', 'vs']]);
		assert.ok(h.tema.berkasAda.every(Boolean), 'berkas tema ikut di folder ekstensi');
		if (bermerek) {
			assert.equal(h.tema.nilaiAwal, 'DSWorkbench Gelap', 'aplikasi bermerek: ekstensi menetapkan tema bawaan sekali (profil baru)');
			// `null` = ekstensi lama tanpa kait uji `temaBawaan`.
			if (h.tema.bawaanDiterapkan !== null) assert.equal(h.tema.bawaanDiterapkan, true);
		} else {
			assert.equal(h.tema.nilaiAwal, null, 'ekstensi tidak boleh menetapkan workbench.colorTheme sendiri di luar aplikasi bermerek');
			if (h.tema.bawaanDiterapkan !== null) assert.equal(h.tema.bawaanDiterapkan, false);
		}
		assert.ok(h.perintah.includes('dsworkbench.useTheme'));
	});
	periksa('kedua tema dapat diterapkan lewat workbench.colorTheme (Terang → terang, Gelap → gelap)', () => {
		// ColorThemeKind: 1 = Light, 2 = Dark. Tema tak dikenal tidak mengubah jenis.
		assert.deepEqual(h.tema.terap, [
			{ nama: 'DSWorkbench Terang', jenis: 1, setelan: 'DSWorkbench Terang' },
			{ nama: 'DSWorkbench Gelap', jenis: 2, setelan: 'DSWorkbench Gelap' },
		]);
	});
	periksa('ekstensi aktif tanpa galat dengan tema DSWorkbench Gelap sepanjang uji', () => {
		assert.equal(h.tema.aktifSetelahTema, true);
		assert.equal(h.tema.akhir.setelan, 'DSWorkbench Gelap');
		assert.equal(h.tema.akhir.jenis, 2);
		assert.equal(h.tema.akhir.aktif, true);
		assert.equal(h.galat, undefined);
	});
	periksa('agent sungguhan siap dan relay tersambung', () => {
		assert.equal(h.statusSiap.keadaan, 'siap');
		assert.equal(h.statusSiap.relay, 'tersambung');
		assert.match(h.statusSiap.versi, /^\d+\.\d+\.\d+/);
	});
	periksa('akar mata kuliah dari agent (workspace.ensure absolutePath)', () => {
		assert.equal(h.mataKuliah.courseId, COURSE);
		assert.equal(h.mataKuliah.layout, 'per-course');
		assert.equal(nj(h.mataKuliah.akar), nj(nyata(wsCourse)));
	});
	periksa('tiga sel: dua berhasil berurutan, variabel bertahan, sel ketiga gagal', () => {
		assert.deepEqual(h.sel.map((s) => [s.urutan, s.berhasil]), [[1, true], [2, true], [3, false]]);
	});
	const nb = h.berkas;
	periksa('tersimpan sebagai nbformat 4 dengan language_info', () => {
		assert.equal(h.tersimpan, true);
		assert.equal(nb.nbformat, 4);
		assert.equal(nb.metadata.language_info.name, 'python');
		assert.deepEqual(nb.cells.map((c) => c.execution_count), [1, 2, 3]);
	});
	const gabung = (t) => (Array.isArray(t) ? t.join('') : t);
	periksa('sel 1: stream stdout + stderr + execute_result text/plain', () => {
		const o = nb.cells[0].outputs;
		assert.deepEqual(o.map((x) => x.output_type), ['stream', 'stream', 'execute_result']);
		assert.deepEqual([o[0].name, gabung(o[0].text)], ['stdout', 'halo dari Local Runner\n']);
		assert.deepEqual([o[1].name, gabung(o[1].text)], ['stderr', 'peringatan\n']);
		assert.equal(o[2].execution_count, 1);
		assert.equal(gabung(o[2].data['text/plain']), '42');
	});
	periksa('sel 2: stream + execute_result text/html + image/png (base64 utuh)', () => {
		const o = nb.cells[1].outputs;
		assert.deepEqual(o.map((x) => x.output_type), ['stream', 'execute_result']);
		assert.equal(gabung(o[0].text), 'variabel bertahan: 42\n');
		assert.equal(o[1].execution_count, 2);
		assert.equal(gabung(o[1].data['text/html']), '<table><tr><td>x</td><td>42</td></tr></table>');
		assert.equal(gabung(o[1].data['image/png']).replace(/\s+/g, ''), PNG_B64);
		assert.ok('text/plain' in o[1].data);
	});
	periksa('sel 3: error ZeroDivisionError dengan traceback', () => {
		const o = nb.cells[2].outputs;
		assert.equal(o.length, 1);
		assert.equal(o[0].output_type, 'error');
		assert.equal(o[0].ename, 'ZeroDivisionError');
		assert.match(o[0].evalue, /division by zero/);
		assert.ok(Array.isArray(o[0].traceback) && o[0].traceback.length > 0);
	});
	periksa('direktori kerja kernel = akar mata kuliah', () => {
		assert.equal(nj(fs.readFileSync(path.join(wsCourse, 'jejak.txt'), 'utf8')), nj(nyata(wsCourse)));
	});
	periksa('notebook di luar folder mata kuliah ditolak dengan pesan jelas', () => {
		assert.equal(h.luar.berhasil, false);
		assert.match(JSON.stringify(h.luar.keluaran), /di luar folder mata kuliah/);
	});
	periksa('tiap notebook punya kernel sendiri; interupsi menghentikan sel yang berjalan', () => {
		assert.deepEqual(h.kedua.sebelum, { urutan: 1, berhasil: true, teks: '5' }, 'penghitung notebook kedua mulai dari 1');
		assert.equal(h.kedua.interupsi.berhasil, false);
		assert.ok(h.kedua.interupsi.detik < 30, `interupsi butuh ${h.kedua.interupsi.detik} dtk`);
		// Windows: proses anak tidak bisa di-SIGINT, jadi kernel dimulai ulang (ADR-049);
		// sel tetap berhenti, tetapi variabel hilang dan pesannya KernelRestarted.
		const polaHenti = process.platform === 'win32' ? /KeyboardInterrupt|dihentikan|KernelRestarted|dimulai ulang/i : /KeyboardInterrupt|dihentikan/i;
		assert.match(JSON.stringify(h.kedua.interupsi.keluaran), polaHenti);
	});
	periksa('"Mulai ulang kernel" menghapus variabel dan mengulang penghitung', () => {
		assert.equal(h.kedua.setelahUlang.berhasil, false);
		assert.equal(h.kedua.setelahUlang.urutan, 1);
		assert.match(JSON.stringify(h.kedua.setelahUlang.keluaran), /NameError/);
	});
	const jalur = (p) => permintaan.filter((x) => x.jalur === p);
	periksa('amplop diminta dengan token aplikasi, tanpa cookie', () => {
		const a = jalur('/api/app/session-envelope');
		assert.ok(a.length >= 1);
		assert.deepEqual(a[0].badan, { courseId: COURSE });
		for (const x of permintaan) {
			assert.equal(x.otorisasi, `Bearer ${TOKEN}`);
			assert.equal(x.cookie, undefined);
		}
	});
	periksa('suar notebook-opened dan NOTEBOOK_CELL_FAILED, bentuk sama dengan web', () => {
		const buka = jalur('/api/me/activity/notebook-opened');
		// Satu per notebook mata kuliah; notebook di luar mata kuliah tidak dikirim.
		assert.deepEqual(buka.map((b) => b.badan), [
			{ courseId: COURSE, moduleId: 'm01-pengantar', fileName: NAMA_NB },
			{ courseId: COURSE, fileName: NAMA_NB2 },
			// Starter modul 2 yang baru disalin dikenali sebagai notebook modulnya.
			{ courseId: COURSE, moduleId: MODUL2, fileName: NAMA_STARTER2 },
		]);
		const gagal = jalur('/api/me/companion/events');
		assert.ok(gagal.length >= 1 && gagal.length <= 2, `suar sel gagal: ${gagal.length}`);
		for (const g of gagal) assert.notEqual(g.badan.data.errorName, 'KeyboardInterrupt', 'sel yang dihentikan bukan galat belajar');
		assert.deepEqual(gagal[0].badan, { event: 'NOTEBOOK_CELL_FAILED', data: { errorName: 'ZeroDivisionError', courseId: COURSE, moduleId: 'm01-pengantar' } });
	});
	periksa('tidak ada kode sel, keluaran, atau kredensial perangkat yang dikirim ke server', () => {
		const semua = JSON.stringify(permintaan);
		for (const terlarang of ['jangan-bocor', 'RAHASIA_KODE_SEL', 'halo dari Local Runner', 'time.sleep', RAHASIA]) assert.ok(!semua.includes(terlarang), terlarang);
	});
	// --- Fase 3 -----------------------------------------------------------------
	const akarNyata = nyata(wsCourse);
	periksa('perintah Fase 3 terdaftar', () => {
		for (const p of ['dsworkbench.prepareModule', 'dsworkbench.runCheckpoint', 'dsworkbench.runCheckpointTask', 'dsworkbench.submitTask', 'dsworkbench.sql.runFile', 'dsworkbench.sql.runSelection', 'dsworkbench.sql.preview', 'dsworkbench.sql.serviceMenu']) assert.ok(h.perintah.includes(p), p);
	});
	periksa('siapkan berkas modul: disalin lewat agent sungguhan, yang sudah ada tidak ditimpa, dataset dilewati', () => {
		const s = h.siapkan.hasil[0];
		assert.deepEqual(s.ditulis, [NAMA_STARTER2, 'catatan.md']);
		assert.deepEqual(s.sudahAda, ['sudah-ada.md']);
		assert.deepEqual(s.dilewati.map((d) => d.nama), ['data.csv']);
		assert.deepEqual(s.gagal, []);
		assert.equal(fs.readFileSync(path.join(wsCourse, NAMA_STARTER2), 'utf8'), ISI_STARTER2);
		assert.equal(fs.readFileSync(path.join(wsCourse, 'catatan.md'), 'utf8'), ISI_CATATAN);
		assert.equal(fs.readFileSync(path.join(wsCourse, 'sudah-ada.md'), 'utf8'), ISI_SUDAH_ADA);
		assert.ok(!fs.existsSync(path.join(wsCourse, 'data.csv')));
		const unduh = permintaan.filter((x) => x.jalur.includes(`/modules/${MODUL2}/files/`)).map((x) => x.jalur);
		assert.ok(unduh.includes(`/api/courses/${COURSE}/modules/${MODUL2}/files/M02_starter.ipynb?path=notebooks%2FM02_starter.ipynb`), unduh.join(' '));
	});
	periksa('notebook starter terbuka di editor setelah disiapkan', () => {
		assert.equal(nj(h.siapkan.starter), nj(path.join(akarNyata, NAMA_STARTER2)));
		assert.equal(nj(h.siapkan.aktif), nj(path.join(akarNyata, NAMA_STARTER2)));
		assert.equal(h.siapkan.jumlahSel, 1);
	});
	periksa('panel Tugas: tugas mata kuliah aktif terbaca', () => {
		assert.deepEqual(h.tugas.map((t) => [t.assignmentId, t.moduleId]), [['a-m02', MODUL2]]);
	});
	const kirim = permintaan.filter((x) => x.jalur === '/api/relay/dispatch').map((x) => x.badan);
	periksa('checkpoint Mode tugas: dispatch lewat relay dengan muatan seperti web, hasil per butir', () => {
		assert.deepEqual(kirim[0], { deviceId: 'dev-uji', operation: 'checkpoint.run', payload: { courseId: COURSE, moduleId: MODUL2, tugas: true } });
		assert.equal(h.checkpoint.jenis, 'hasil');
		assert.deepEqual(h.checkpoint.hasil.findings.map((f) => f.status), ['LULUS', 'LULUS']);
	});
	periksa('kumpul ujung ke ujung: berkas dibaca lewat agent sungguhan, diunggah utuh, gerbang lewat relay, lalu submit', () => {
		assert.equal(h.kumpul.hasil.jenis, 'terkumpul', JSON.stringify(h.kumpul.hasil));
		assert.equal(h.kumpul.hasil.resi.id, 'rcpt-uji');
		const berkas = [
			{ nama: 'M02_NIM.ipynb', jalur: NAMA_STARTER2, ukuran: Buffer.byteLength(ISI_STARTER2) },
			{ nama: 'catatan.md', jalur: 'catatan.md', ukuran: Buffer.byteLength(ISI_CATATAN) },
		];
		assert.deepEqual(h.kumpul.ditanya, [berkas], 'konfirmasi menerima daftar berkas');
		assert.deepEqual(Object.values(unggahan), [
			{ nama: NAMA_STARTER2, sha256: sha256(fs.readFileSync(path.join(wsCourse, NAMA_STARTER2))), panjang: Buffer.byteLength(ISI_STARTER2) },
			{ nama: 'catatan.md', sha256: sha256(Buffer.from(ISI_CATATAN)), panjang: Buffer.byteLength(ISI_CATATAN) },
		]);
		assert.deepEqual(lampiran, [{ name: 'M02_NIM.ipynb', objectId: 'obj-1' }, { name: 'catatan.md', objectId: 'obj-2' }]);
		assert.deepEqual(kirim[1], { deviceId: 'dev-uji', operation: 'git.status', payload: { courseId: COURSE } });
		const urut = permintaan.map((x) => `${x.metode} ${x.jalur}`);
		// Dispatch `git.status` itu sendiri (panel Pergudangan Data mengirim dispatch lain sesudahnya).
		const iGit = permintaan.findIndex((x) => x.jalur === '/api/relay/dispatch' && x.badan?.operation === 'git.status');
		const iSubmit = urut.indexOf('POST /api/attempts/att-uji/submit');
		assert.ok(iGit > 0 && iSubmit > iGit, 'git.status lewat relay mendahului submit');
		assert.equal(urut.filter((u) => u === 'POST /api/attempts/att-uji/submit').length, 1);
		assert.ok(urut.indexOf(`POST /api/courses/${COURSE}/assignments/a-m02/attempts`) < urut.indexOf('POST /api/objects/upload/reserve'), 'draf dibuat sebelum unggah');
	});

	// --- Fase 5–6: panel Naskah, Beranda, Bravais ---------------------------------
	const cspDari = (html) => /<meta http-equiv="Content-Security-Policy" content="([^"]*)">/.exec(html)?.[1] ?? '';
	periksa('perintah dan view Fase 5–6 terdaftar (Buka naskah modul, Beranda, Bravais di bilah samping kedua)', () => {
		for (const p of ['dsworkbench.openMaterial', 'dsworkbench.home', 'dsworkbench.bravais.open']) assert.ok(h.perintah.includes(p), p);
		assert.equal(h.bravais.perintahFokus, true, 'view dsworkbench.bravais terdaftar (perintah .focus ada)');
		const manifest = JSON.parse(fs.readFileSync(path.join(akarMuat, 'package.json'), 'utf8')).contributes;
		assert.deepEqual(manifest.viewsContainers.secondarySidebar.map((c) => c.id), ['dsworkbenchBravais']);
		assert.deepEqual(manifest.views.dsworkbenchBravais.map((v) => [v.id, v.type]).slice(0, 1), [['dsworkbench.bravais', 'webview']]);
	});
	periksa('panel Naskah terbuka dari modul sebagai tab editor; HTML memuat kartu Jalankan berkas, blok @berkas, daftar isi, rumus, dan gambar lokal', () => {
		const n = h.naskah;
		assert.equal(n.judul, 'Modul 2');
		assert.equal(n.tab, 'Modul 2', 'tab editor berjudul nama modul');
		assert.deepEqual([n.berkas, n.jalankan, n.gambar], [1, 1, 1]);
		assert.ok(n.html.includes('<div class="md-jalankan">'), 'kartu jalankan');
		assert.ok(n.html.includes('data-tindakan="jalankan-berkas" data-indeks="0"'));
		assert.ok(n.html.includes('<code class="md-jalankan__path">modul-02/muat.sql</code>'));
		assert.ok(n.html.includes('isi variabel <code>batch</code> dengan <code>1</code>'));
		assert.ok(n.html.includes('<details class="md-jalankan__terminal">'));
		assert.ok(n.html.includes('data-tindakan="simpan-berkas" data-indeks="0"'));
		assert.ok(n.html.includes('data-tindakan="tandai-selesai"') && n.html.includes('data-tindakan="siapkan-berkas-modul"'));
		assert.ok(n.html.includes('<nav class="isi" aria-label="Daftar isi">') && n.html.includes('data-lompat="langkah"'));
		assert.ok(n.html.includes('class="katex"'), 'rumus dirender KaTeX');
		// Isi naskah adalah data: HTML mentah di-escape, tautan tidak dapat dinavigasi.
		assert.ok(n.html.includes('&lt;script&gt;window.bocor = 1&lt;/script&gt;') && !n.html.includes('<script>window.bocor'));
		assert.ok(n.html.includes('data-tautan="0"') && !/<a\s[^>]*href="http/.test(n.html));
		// Gambar diunduh ekstensi (bearer) lalu ditampilkan dari berkas lokal.
		const img = /<img src="([^"]+)"/.exec(n.html)?.[1] ?? '';
		assert.ok(img.endsWith('/alur-data.png') && !img.startsWith('http://127.0.0.1') && /vscode/.test(img), `alamat gambar: ${img}`);
		assert.equal(permintaan.filter((x) => x.jalur === `/api/courses/${COURSE}/modules/${MODUL2}/figures/alur-data.png`).length, 1);
		assert.ok(n.pesanPanel >= 1, 'skrip panel naskah berjalan dan mengirim "siap"');
	});
	periksa('CSP panel (Naskah, Beranda, Bravais) tidak memuat sumber jaringan; token dan alamat server tidak ada di HTML panel', () => {
		for (const [nama, html] of [['naskah', h.naskah.html], ['beranda', h.beranda.html], ['bravais', h.bravais.html]]) {
			const csp = cspDari(html);
			assert.ok(csp.startsWith("default-src 'none'"), `${nama}: ${csp}`);
			assert.ok(!/unsafe-inline|unsafe-eval|http:|\*;|\* /.test(csp), `${nama}: CSP longgar: ${csp}`);
			assert.ok(csp.includes("connect-src 'none'") && csp.includes("frame-src 'none'"), nama);
			const nonce = /script-src 'nonce-([A-Za-z0-9]+)'/.exec(csp)?.[1];
			assert.ok(nonce && nonce.length >= 16, `${nama}: skrip hanya ber-nonce`);
			for (const arahan of csp.split(';').map((a) => a.trim())) {
				for (const v of arahan.split(/\s+/).slice(1)) {
					assert.ok(v === "'none'" || v === 'data:' || v === "'self'" || v === `'nonce-${nonce}'` || /^(https:\/\/\*\.vscode-cdn\.net|vscode-webview-resource:|vscode-resource:)$/.test(v), `${nama}: sumber ${v} di "${arahan}"`);
				}
			}
			const skrip = [...html.matchAll(/<script\b([^>]*)>/g)].map((m) => m[1]);
			assert.ok(skrip.length >= 1 && skrip.every((a) => a.includes(`nonce="${nonce}"`) && /src="[^"]*\/media\/(panel|lottie)\/[a-z.]+\.js"/.test(a)), `${nama}: skrip hanya berkas lokal ekstensi`);
			assert.ok(!html.includes(TOKEN) && !html.includes(SERVER) && !html.includes('sditera.cloud'), `${nama}: token/alamat server di HTML`);
			assert.ok(!/<iframe|<form\b|\son[a-z]+="/.test(html), nama);
		}
	});
	periksa('naskah: "Simpan ke folder kerja" menulis lewat agent sungguhan tanpa menimpa; "Jalankan berkas" menolak berkas yang belum ada; "Tandai selesai" ke server; pesan tak sah ditolak', () => {
		const n = h.naskah.aksi;
		assert.deepEqual([n.simpan.ok, n.simpan.kode], [true, 'tersimpan'], JSON.stringify(n.simpan));
		assert.equal(fs.readFileSync(path.join(wsCourse, 'modul-02', 'catatan-naskah.sql'), 'utf8'), ISI_BERKAS_NASKAH);
		assert.deepEqual([n.simpanLagi.ok, n.simpanLagi.kode], [false, 'sudah_ada']);
		assert.deepEqual([n.jalankan.ok, n.jalankan.kode], [false, 'belum_ada']);
		assert.equal(nj(n.jalankan.jalur), nj(path.join(nyata(wsCourse), 'modul-02', 'muat.sql')), 'path dari naskah dipetakan ke akar mata kuliah');
		assert.deepEqual([n.tandai.ok, n.tandai.kode], [true, 'selesai']);
		assert.equal(lessonSelesai.length, 1);
		assert.equal(n.gulir.kode, 'gulir');
		assert.deepEqual(n.ditolak.map((d) => d.kode), ['ditolak', 'ditolak', 'ditolak']);
		assert.ok(!fs.existsSync('/etc/passwd.dsw'));
	});
	periksa('Beranda: sapaan, Lanjutkan (naskah dan notebook terakhir), tenggat, pengumuman dari server; tanpa nilai', () => {
		const b = h.beranda;
		assert.equal(b.terbuka, true);
		assert.match(b.html, /Selamat (pagi|siang|sore|malam), Mahasiswa Uji/);
		assert.deepEqual(b.model.lanjut.map((x) => [x.jenis, x.judul]).slice(0, 2), [['naskah', 'Modul 2'], ['notebook', NAMA_STARTER2]]);
		assert.ok(b.html.includes('Pemeliharaan server &lt;uji&gt;') && b.html.includes('Tugas Modul 2'));
		assert.deepEqual(b.model.tenggat.map((t) => t.judul).slice(0, 1), ['Tugas Modul 2']);
		assert.ok(b.html.includes('data-tindakan="buka-kelas"') && b.html.includes('data-tindakan="siapkan-lingkungan"') && b.html.includes('data-tindakan="buka-web"'));
		assert.ok(!/nilai|peringkat/i.test(b.html));
		assert.equal(b.ditolak, 'ditolak');
		assert.equal(permintaan.filter((x) => x.jalur === '/api/me/announcements').length >= 1, true);
	});
	periksa('Bravais: skrip panel hidup, pertanyaan terkirim ke server palsu dengan konteks notebook aktif (bentuk web), job ditunggu, jawaban dirender aman', () => {
		const b = h.bravais;
		assert.ok(b.pesanPanel >= 1, 'skrip panel Bravais berjalan dan mengirim "siap"');
		assert.ok(permintaan.some((x) => x.jalur === '/api/me/companion/config'), 'konfigurasi diambil setelah panel siap');
		assert.equal(b.kirim, 'terkirim');
		const tanya = permintaan.filter((x) => x.jalur === '/api/me/companion/assistant');
		assert.equal(tanya.length, 1);
		assert.deepEqual(tanya[0].badan, {
			message: 'Jelaskan sel yang sedang saya buka.',
			history: [],
			view: {
				kind: 'notebook', title: 'Notebook', path: `/courses/${COURSE}/modules/${MODUL2}/notebook`, file: NAMA_STARTER2,
				snippet: { label: 'Isi sel ke-1', lang: 'python', text: "print('starter modul 2')" },
				detail: [{ label: 'Jumlah sel', value: '1' }, { label: 'Sel aktif', value: 'sel ke-1 (kode)' }],
			},
			courseId: COURSE,
			moduleId: MODUL2,
		});
		assert.ok(bravais.tagihan >= 2, 'job ditagih sampai selesai');
		assert.deepEqual(permintaan.find((x) => x.metode === 'POST' && x.jalur === '/api/me/companion/chats').badan, { title: 'Jelaskan sel yang sedang saya buka.', courseId: COURSE, moduleId: MODUL2 });
		const simpan = permintaan.filter((x) => x.jalur === '/api/me/companion/chats/c-uji/messages').map((x) => x.badan);
		assert.deepEqual(simpan, [{ role: 'user', content: 'Jelaskan sel yang sedang saya buka.' }, { role: 'assistant', content: JAWABAN_BRAVAIS, route: 'llm', sources: [] }]);
		assert.ok(!JSON.stringify(simpan).includes('starter modul 2'), 'konteks jendela tidak ikut disimpan ke riwayat');
		const k = b.keadaan;
		assert.deepEqual([k.masuk, k.asisten, k.sibuk, k.pesan.length, k.judul], [true, true, false, 2, 'Jelaskan sel yang sedang saya buka.']);
		assert.equal(k.pesan[0].teks, 'Jelaskan sel yang sedang saya buka.');
		assert.ok(k.pesan[1].html.includes('data-tindakan="sisipkan-kode" data-indeks="0"') && k.pesan[1].html.includes('data-tindakan="salin-kode"'));
		assert.equal(k.konteks.label, `Konteks: ${NAMA_STARTER2}, sel 1`);
		assert.deepEqual(b.ditolak, ['ditolak', 'ditolak', 'ditolak']);
		assert.ok(b.html.includes('id="masukan"') && b.html.includes('/media/bravais/bravais.png'));
	});

	if (h.menulis) {
		const mn = h.menulis;
		periksa('Menulis: perintah palet dan view terdaftar; mesin "belum dipasang" tanpa membuat folder runtime; tiga templat dibundel', () => {
			for (const p of ['writing.open', 'writing.new', 'writing.compile', 'writing.preview', 'writing.export', 'writing.exportPdf', 'writing.exportMarkdown', 'writing.exportZip', 'writing.install']) assert.ok(h.perintah.includes(`dsworkbench.${p}`), p);
			assert.deepEqual(mn.templat, ['ta-sains-data', 'laporan-praktikum', 'artikel']);
			assert.equal(mn.awal.mesin.keadaan, 'belum_dipasang');
			assert.equal(mn.awal.lab, false);
			assert.equal(fs.existsSync(folderRuntime), false, 'folder runtime tidak dibuat oleh alat Menulis');
		});
		periksa('Menulis: "Dokumen baru" dari templat TA membuat proyek utuh di folder NON-mata-kuliah, membuka main.tex, dan tidak menimpa', () => {
			const akar = path.join(luarDir, 'Tugas Akhir Uji');
			assert.equal(nj(nyata(mn.utama)), nj(nyata(path.join(akar, 'main.tex'))));
			assert.equal(nj(nyata(mn.editorAktif)), nj(nyata(path.join(akar, 'main.tex'))), 'main.tex dibuka di editor');
			for (const b of ['main.tex', 'tasainsdata.cls', '.gitignore', 'README.md', 'awal/abstrak.tex', 'bab/bab1.tex', 'bab/bab5.tex', 'akhir/daftar-pustaka.tex', 'gambar/LogoITERA.png']) assert.ok(fs.statSync(path.join(akar, b)).isFile(), b);
			assert.ok(!fs.existsSync(path.join(akar, 'contoh')) && !fs.existsSync(path.join(akar, 'main.pdf')) && !fs.existsSync(path.join(akar, '_gitignore')));
			assert.ok(fs.readFileSync(path.join(akar, 'gambar', 'LogoITERA.png')).equals(fs.readFileSync(path.join(akarMuat, 'templat', 'ta-sains-data', 'gambar', 'LogoITERA.png'))));
			assert.match(mn.timpa, /sudah berisi berkas/);
			assert.equal(mn.aktif.nama, 'main.tex');
			assert.deepEqual(mn.proyek.map((p) => [p.judul, p.ada]), [['Tugas Akhir Uji', true]]);
			// Folder itu bukan mata kuliah: tidak ada amplop/permintaan server untuk membuat proyek.
			assert.ok(!nj(nyata(akar)).startsWith(nj(nyata(ws))), 'di luar folder kerja mata kuliah');
		});
		periksa('Menulis: ekspor ZIP (sumber + gambar, tanpa berkas bantu/tersembunyi) dan Markdown (jumlah yang tidak dikonversi dilaporkan) berjalan tanpa mesin LaTeX', () => {
			const zip = fs.readFileSync(path.join(luarDir, 'ta-uji.zip'));
			assert.equal(zip.readUInt32LE(0), 0x04034b50);
			const nama = [];
			for (let i = zip.length - 22, p = zip.readUInt32LE(i + 16), n = zip.readUInt16LE(i + 10); n > 0; n--) {
				const pj = zip.readUInt16LE(p + 28);
				nama.push(zip.subarray(p + 46, p + 46 + pj).toString('utf8'));
				p += 46 + pj + zip.readUInt16LE(p + 30) + zip.readUInt16LE(p + 32);
			}
			for (const b of ['main.tex', 'tasainsdata.cls', 'bab/bab2.tex', 'gambar/LogoITERA.png', 'README.md']) assert.ok(nama.includes(`Tugas Akhir Uji/${b}`), b);
			assert.ok(!nama.some((x) => /\.(aux|log|gz)$/.test(x) || x.split('/').some((b) => b.startsWith('.')) || x.includes('/build/')), nama.join(' '));
			assert.ok(nama.includes('Tugas Akhir Uji/main.pdf'), 'PDF terakhir (build/main.pdf) ikut');
			assert.match(mn.teksZip, /^ZIP proyek disimpan \(22 berkas, termasuk main\.pdf\)/);
			const md = fs.readFileSync(path.join(luarDir, 'ta-uji.md'), 'utf8');
			assert.match(md, /^# Judul Skripsi Anda\n/);
			assert.ok(md.includes('\n# PENDAHULUAN\n') && md.includes('\n## Latar Belakang\n') && md.includes('$$\n\\begin{aligned}'));
			assert.match(mn.teksMd, /Markdown disimpan: .*ta-uji\.md\. \d+ bagian tidak dapat dikonversi dan ditinggalkan sebagai kode LaTeX: /);
			assert.match(mn.teksPdf, /^PDF disimpan: /);
			assert.equal(fs.readFileSync(path.join(luarDir, 'ta-uji.pdf'), 'utf8'), '%PDF-uji');
			assert.match(mn.pdfTanpaKompilasi, /PDF belum ada\. Kompilasi dokumen lebih dulu\./);
		});
		periksa('Menulis: view webview ber-CSP ketat, tanpa jalur ke panel selain tindakan tetap; pesan dengan argumen liar ditolak', () => {
			assert.equal(mn.panel.terpasang, true);
			assert.match(mn.panel.html, /Content-Security-Policy" content="default-src 'none'; [^"]*connect-src 'none'/);
			assert.match(mn.panel.html, /data-tindakan="baru"/);
			assert.match(mn.panel.html, /Proyek terakhir/);
			assert.match(mn.panel.html, /Tugas Akhir Uji/);
			assert.match(mn.panel.html, /Mesin LaTeX \(TinyTeX\): belum dipasang/);
			assert.ok(!mn.panel.html.includes(TOKEN));
			assert.deepEqual(mn.ditolak, ['ditolak', 'ditolak', 'ditolak']);
			assert.equal(mn.segarkan, 'segarkan');
		});
	} else {
		dilewati.push('Menulis: ekstensi yang diuji belum punya kait uji (versi lama)');
	}
	if (h.berkasSaya) {
		const bk = h.berkasSaya;
		const wsNyata = nyata(wsCourse);
		periksa('Berkas saya: daftar + kuota dari server; perintah, view, dan menu konteks Explorer terdaftar', () => {
			for (const p of ['files.open', 'files.save', 'files.import', 'files.refresh', 'files.openWeb']) assert.ok(h.perintah.includes(`dsworkbench.${p}`), p);
			assert.deepEqual(bk.awal.map((o) => o.nama), ['../../../keluar-dari-folder.txt', 'jebakan.zip']);
			assert.ok(permintaan.some((p) => p.metode === 'GET' && p.jalur === '/api/me/objects' && p.otorisasi === `Bearer ${TOKEN}`));
		});
		periksa('Berkas saya: "Simpan ke Berkas saya" — berkas apa adanya, folder kecil di-ZIP; batas dan kuota server disebut SEBELUM mengunggah', () => {
			assert.deepEqual(bk.simpan.tersimpan, ['catatan-rumah.csv', 'dataset-kecil.zip']);
			assert.equal(bk.simpan.pesan, undefined);
			assert.match(bk.simpan.ditanya[0], /^catatan-rumah\.csv \| \d+ B$/);
			assert.match(bk.simpan.ditanya[1], /^dataset-kecil\.zip \| folder, 2 berkas, ZIP /);
			assert.match(bk.simpan.ditanya[2], /^Sisa kuota 1\.00 GB dari 1\.00 GB; batas satu berkas 50\.0 MB\. Jumlah yang diunggah /);
			const csv = arsip.get('ars-3');
			assert.equal(csv.data.toString('utf8'), 'nim,nilai\n122450001,90\n');
			assert.deepEqual([csv.pesan.purpose, csv.pesan.classification, csv.pesan.logicalBucket, csv.pesan.contentType, csv.pesan.displayFilename], ['user_archive', 'sensitive_academic', 'academic_private', 'text/csv', 'catatan-rumah.csv']);
			assert.equal(arsip.get('ars-4').data.readUInt32LE(0), 0x04034b50, 'folder diunggah sebagai ZIP');
			const urut = permintaan.map((p) => `${p.metode} ${p.jalur}`);
			const iDaftar = urut.indexOf('GET /api/me/objects');
			const iPesan = urut.indexOf('POST /api/objects/upload/ses-ars-3/initiate');
			assert.ok(iDaftar >= 0 && iDaftar < iPesan, 'kuota diminta sebelum unggah');
			assert.deepEqual(bk.setelah.map((o) => o.nama), ['../../../keluar-dari-folder.txt', 'jebakan.zip', 'catatan-rumah.csv', 'dataset-kecil.zip']);
		});
		periksa('Berkas saya: "Impor" — nama berbahaya dari server disanitasi dan tetap di folder tujuan; tidak menimpa; ZIP sendiri diekstrak; ZIP berjalur keluar ditolak utuh', () => {
			const tujuan = path.join(wsNyata, 'impor');
			// Nama `../../../keluar-dari-folder.txt` → berkas biasa di dalam folder tujuan.
			assert.equal(nj(nyata(bk.imporNama.berkas)), nj(path.join(tujuan, 'keluar-dari-folder.txt')));
			assert.equal(fs.readFileSync(path.join(tujuan, 'keluar-dari-folder.txt'), 'utf8'), 'isi berkas bernama berbahaya');
			// Diimpor dua kali: yang kedua bernama lain, yang pertama utuh.
			assert.equal(path.basename(bk.imporLagi.berkas), 'keluar-dari-folder (2).txt');
			for (const luar of [path.join(ws, 'keluar-dari-folder.txt'), path.join(tmp, 'keluar-dari-folder.txt'), path.join(path.dirname(tmp), 'keluar-dari-folder.txt')]) assert.equal(fs.existsSync(luar), false, luar);
			// ZIP berisi `../../jebakan-keluar.txt`: berkas ZIP tersimpan, TIDAK ada yang diekstrak.
			assert.equal(path.basename(bk.imporJebakan.berkas), 'jebakan.zip');
			assert.match(bk.imporJebakan.ditolak, /Arsip ditolak: memuat jalur yang tidak aman/);
			assert.equal(bk.imporJebakan.ekstrak, undefined);
			assert.equal(fs.existsSync(path.join(tujuan, 'jebakan')), false);
			for (const luar of [path.join(wsNyata, 'jebakan-keluar.txt'), path.join(ws, 'jebakan-keluar.txt'), path.join(tujuan, 'aman.txt')]) assert.equal(fs.existsSync(luar), false, luar);
			// ZIP buatan sendiri (folder dataset): diekstrak ke folder baru, isinya sama.
			assert.equal(bk.imporZip.jumlahEkstrak, 2);
			assert.equal(nj(nyata(bk.imporZip.ekstrak)), nj(path.join(tujuan, 'dataset-kecil')));
			assert.equal(fs.readFileSync(path.join(tujuan, 'dataset-kecil', 'dataset-kecil', 'a.csv'), 'utf8'), 'a\n1\n');
			assert.equal(fs.readFileSync(path.join(tujuan, 'dataset-kecil', 'dataset-kecil', 'sub', 'b.csv'), 'utf8'), 'b\n2\n');
			assert.equal(fs.readFileSync(path.join(tujuan, 'catatan-rumah.csv'), 'utf8'), 'nim,nilai\n122450001,90\n');
			// Unduhan lewat alamat yang diberi server untuk objek itu, dengan token; tidak ada host lain.
			assert.ok(permintaan.some((p) => p.metode === 'POST' && p.jalur === '/api/objects/awal-1/download-grant'));
			assert.ok(permintaan.filter((p) => /^\/api\/objects\/local\/(ars|awal)-/.test(p.jalur)).every((p) => p.otorisasi === `Bearer ${TOKEN}`));
		});
		periksa('Berkas saya: view webview — nama dari server di-escape, id objek dan token tidak sampai ke panel, pesan liar ditolak', () => {
			assert.equal(bk.panel.terpasang, true);
			assert.match(bk.panel.html, /connect-src 'none'/);
			assert.match(bk.panel.html, /\.\.\/\.\.\/\.\.\/keluar-dari-folder\.txt/);
			assert.match(bk.panel.html, /data-tindakan="impor" data-indeks="0"/);
			assert.match(bk.panel.html, /Terpakai .* dari 1\.00 GB/);
			assert.ok(!bk.panel.html.includes(TOKEN) && !/awal-1|ars-3/.test(bk.panel.html));
			assert.deepEqual(bk.ditolak, ['ditolak', 'ditolak', 'ditolak']);
		});
	} else {
		dilewati.push('Berkas saya: ekstensi yang diuji belum punya kait uji (versi lama)');
	}
	periksa('suara panel: bawaan nyala 0,35 (seperti web); <audio> lokal + media-src lokal saja di keempat panel; Bravais berpikir berbunyi; pengaturan berlaku tanpa memuat ulang', () => {
		const s = h.suara;
		assert.deepEqual(s.awal, { aktif: true, volume: 0.35 });
		assert.ok(s.terkirim.includes('berpikir'), `suara terkirim: ${s.terkirim}`);
		assert.ok(s.terkirim.every((n) => ['klik', 'buka', 'tutup', 'berpikir', 'jawaban', 'galat', 'notif'].includes(n)));
		assert.deepEqual([s.mati, s.klikMati, s.terkirimMati], [{ aktif: false, volume: 0.35 }, 'konteks', 0], 'dimatikan: langsung diam');
		assert.deepEqual([s.nyala, s.akhir], [{ aktif: true, volume: 0.8 }, { aktif: true, volume: 0.35 }]);
		for (const [nama, html] of [['naskah', h.naskah.html], ['beranda', h.beranda.html], ['bravais', h.bravais.html], ['sosial', s.htmlSosial]]) {
			const media = cspDari(html).split(';').map((a) => a.trim()).find((a) => a.startsWith('media-src '));
			assert.ok(media && !/data:|blob:|http:|unsafe|\*(?!\.vscode-cdn\.net)/.test(media), `${nama}: ${media}`);
			const src = [...html.matchAll(/<audio id="suara-([a-z]+)" preload="auto" src="([^"]*)">/g)];
			assert.deepEqual(src.map((m) => m[1]), ['klik', 'buka', 'tutup', 'berpikir', 'jawaban', 'galat', 'notif'], nama);
			for (const m of src) assert.ok(/vscode/.test(m[2]) && /\/media\/suara\/[a-z-]+\.mp3$/.test(m[2]) && !m[2].startsWith('http://'), `${nama}: ${m[2]}`);
		}
		for (const f of ['click.mp3', 'open.mp3', 'close.mp3', 'thinking.mp3', 'response.mp3', 'error.mp3', 'xp.mp3', 'CATATAN.txt']) assert.ok(fs.existsSync(path.join(akarMuat, 'media', 'suara', f)), f);
		assert.deepEqual(fs.readdirSync(path.join(akarMuat, 'media', 'suara')).sort(), ['CATATAN.txt', 'click.mp3', 'close.mp3', 'error.mp3', 'open.mp3', 'response.mp3', 'thinking.mp3', 'xp.mp3']);
	});

	// --- Fase 7: panel Sosial -------------------------------------------------------
	periksa('view Sosial terdaftar di container yang sama dengan Bravais (view kedua), perintah dan pengaturan notifikasi ada', () => {
		const manifest = JSON.parse(fs.readFileSync(path.join(akarMuat, 'package.json'), 'utf8')).contributes;
		assert.deepEqual(manifest.views.dsworkbenchBravais.map((v) => [v.id, v.type]), [['dsworkbench.bravais', 'webview'], ['dsworkbench.sosial', 'webview']]);
		assert.equal(h.sosial.perintahFokus, true, 'view dsworkbench.sosial terdaftar (perintah .focus ada)');
		assert.ok(h.perintah.includes('dsworkbench.social.open'));
		assert.equal(manifest.configuration.properties['dsworkbench.social.notifications'].default, true);
	});
	periksa('Sosial: skrip panel hidup (mengirim "siap"), daftar teman dari server palsu tampil urut Online/Offline, avatar dari cache lokal', () => {
		const s = h.sosial;
		assert.ok(s.pesanPanel >= 1, 'skrip panel Sosial berjalan di bawah CSP dan mengirim "siap"');
		assert.equal(s.sebelum.keadaan, 'siap');
		assert.deepEqual(s.sebelum.online.map((t) => [t.id, t.nama, t.pil, t.suasana]), [['usr-rani', 'Rani <b>Uji</b>', 'Online', 'Fokus belajar']]);
		assert.deepEqual(s.sebelum.offline.map((t) => [t.id, t.nama]), [['usr-dimas', 'Dimas']]);
		assert.equal(s.sebelum.lencana, 0);
		// Avatar: diunduh ekstensi dari aset publik server (tanpa bearer), ditampilkan dari berkas lokal.
		const minta = sosialPalsu.avatarDiminta.filter((x) => x.jalur === '/social-presence/catalog/rabbit-128.png');
		assert.equal(minta.length, 1, 'PNG avatar diambil sekali');
		assert.deepEqual([minta[0].otorisasi, minta[0].cookie], [undefined, undefined]);
		// Akar sumber lokal harus `file:`; akar `vscode-userdata:` membuat webview menolak PNG avatar (cacat uji 2–3).
		assert.ok(s.akarSkema.length === 2 && s.akarSkema.every((x) => x === 'file'), `skema akar sumber lokal: ${s.akarSkema}`);
		assert.ok(s.avatarRani && /vscode/.test(s.avatarRani) && !s.avatarRani.startsWith('http://127.0.0.1') && s.avatarRani.includes('rabbit-128.png'), `alamat avatar: ${s.avatarRani}`);
		assert.equal(s.sesudah.keadaan.offline[0].avatar.uri, undefined, 'unduhan gagal (404) → inisial');
		assert.ok(!sosialPalsu.avatarDiminta.some((x) => x.jalur.includes('/lottie/')) || s.tampak, 'animasi hanya diminta panel yang tampak');
	});
	periksa('Sosial: satu pesan terkirim (bentuk web), SSE lewat bearer satu sambungan, lencana belum-dibaca berubah setelah pesan masuk tiruan, isi pesan tidak bocor ke notifikasi', () => {
		const s = h.sosial;
		assert.deepEqual([s.buka, s.kirim], ['dibuka', 'terkirim']);
		const kirim = permintaan.filter((x) => x.metode === 'POST' && x.jalur === '/api/me/social/messages');
		assert.deepEqual(kirim.map((x) => [x.badan, x.otorisasi, x.cookie]), [[{ toUserId: 'usr-rani', body: 'Halo dari IDE' }, `Bearer ${TOKEN}`, undefined]]);
		assert.equal(sosialPalsu.sseOtorisasi.length, 1, 'satu sambungan SSE untuk seluruh jendela');
		assert.deepEqual(sosialPalsu.sseOtorisasi[0], { otorisasi: `Bearer ${TOKEN}`, cookie: undefined, terima: 'text/event-stream' });
		assert.equal(sosialPalsu.didorongLewatSse, 1, 'pesan masuk tiruan didorong lewat SSE');
		assert.equal(s.sesudah.lencana, 1, 'WebviewView.badge = jumlah belum dibaca');
		const k = s.sesudah.keadaan;
		assert.equal(k.belumDibaca, 1);
		assert.equal(k.offline[0].belumDibaca, 1);
		assert.equal(k.obrolan.id, 'usr-rani');
		const grup = k.obrolan.butir.filter((b) => b.jenis === 'grup');
		assert.deepEqual(grup.map((g) => [g.milikSaya, g.pesan.map((p) => p.bagian.map((b) => b.teks).join(''))]), [[true, ['Halo dari IDE']]]);
		assert.ok(permintaan.some((x) => x.jalur === '/api/me/social/messages?with=usr-rani'), 'riwayat diambil saat percakapan dibuka');
		assert.deepEqual(s.ditolak, ['ditolak', 'ditolak', 'ditolak', 'ditolak']);
		for (const n of s.sesudah.notifikasi) assert.ok(!n.includes('ISI-RAHASIA'), 'notifikasi tidak memuat isi pesan');
		for (const x of permintaan) assert.ok(!x.jalur.startsWith('/api/me/social/') || /^\/api\/me\/social\/(peers|avatar|conversations|messages|inbox|status|status-text|poke|typing|events)(\?|$)/.test(x.jalur), x.jalur);
	});
	periksa('Sosial: HTML panel tanpa sumber jaringan (CSP ketat, skrip lokal ber-nonce); token, alamat server, dan isi dari pengguna lain tidak ada di HTML', () => {
		const html = h.sosial.html;
		const csp = cspDari(html);
		assert.ok(csp.startsWith("default-src 'none'"), csp);
		assert.ok(!/unsafe-inline|unsafe-eval|http:|\*;|\* /.test(csp), `CSP longgar: ${csp}`);
		assert.ok(csp.includes("connect-src 'none'") && csp.includes("frame-src 'none'"));
		const nonce = /script-src 'nonce-([A-Za-z0-9]+)'/.exec(csp)?.[1];
		assert.ok(nonce && nonce.length >= 16, 'skrip hanya ber-nonce');
		for (const arahan of csp.split(';').map((a) => a.trim())) {
			for (const v of arahan.split(/\s+/).slice(1)) {
				assert.ok(v === "'none'" || v === 'data:' || v === "'self'" || v === `'nonce-${nonce}'` || /^(https:\/\/\*\.vscode-cdn\.net|vscode-webview-resource:|vscode-resource:)$/.test(v), `sumber ${v} di "${arahan}"`);
			}
		}
		const skrip = [...html.matchAll(/<script\b([^>]*)>/g)].map((m) => m[1]);
		assert.deepEqual(skrip.map((a) => /src="[^"]*\/media\/((?:panel|lottie)\/[a-z.]+\.js)"/.exec(a)?.[1]), ['lottie/lottie.min.js', 'panel/umum.js', 'panel/sosial.js']);
		assert.ok(skrip.every((a) => a.includes(`nonce="${nonce}"`)));
		assert.ok(!html.includes(TOKEN) && !html.includes(SERVER) && !html.includes('sditera.cloud'), 'token/alamat server di HTML');
		assert.ok(!html.includes('Rani') && !html.includes('Halo dari IDE') && !html.includes('ISI-RAHASIA'), 'isi pengguna tidak ditanam di HTML (dikirim lewat postMessage sebagai teks)');
		assert.ok(!/<iframe|<form\b|\son[a-z]+="|<link\b|<img\b/.test(html));
	});

	periksa('SQL tanpa layanan di amplop: agent sungguhan menolak, ekstensi memberi pesan jelas', () => {
		assert.equal(h.sqlTanpaLayanan.state, 'rejected');
		assert.equal(h.sqlTanpaLayanan.code, 'layanan_tidak_ada_di_amplop');
		assert.match(h.sqlTanpaLayanan.detail, /services\.postgres/);
	});

	// --- Panel "Pergudangan Data" ---------------------------------------------------------
	if (h.gudang) {
		const g = h.gudang;
		const kirimPgd = permintaan.filter((x) => x.jalur === '/api/relay/dispatch' && x.badan?.payload?.courseId === COURSE_PGD).map((x) => x.badan);
		const opPgd = kirimPgd.map((x) => x.operation);
		const langkah = (b) => Object.fromEntries(['layanan', 'dataset', 'kerja', 'jelajah', 'er'].map((id) => [id, /class="langkah langkah--([a-z]+)"/.exec(b[`b-${id}`])?.[1]]));
		const langkahCek = (b) => /class="langkah langkah--([a-z]+)"/.exec(b['b-cek'])?.[1];
		periksa('panel Pergudangan Data: hanya untuk mata kuliah yang modulnya butuh basis data (katalog), terbuka sebagai tab, skrip panel berjalan di bawah CSP, enam langkah berstatus nyata', () => {
			assert.ok(h.perintah.includes('dsworkbench.gudang.open'));
			assert.deepEqual(g.berlayanan, [COURSE_PGD], 'ditentukan dari kemampuan modul di katalog');
			assert.equal(g.bukanLayanan, false, 'mata kuliah tanpa basis data praktikum: panel tidak dibuka');
			assert.equal(g.tab, 'Pergudangan Data');
			assert.ok(g.pesanPanel >= 1 && g.siapPanel, 'skrip panel berjalan dan mengirim "siap"');
			const csp = cspDari(g.html);
			assert.ok(csp.startsWith("default-src 'none'") && csp.includes("connect-src 'none'") && /script-src 'nonce-[0-9a-f]{32}'/.test(csp) && !/unsafe/.test(csp), csp);
			assert.ok(!g.html.includes(TOKEN) && !g.html.includes(SERVER) && !/<iframe|<form\b|\son[a-z]+="|<link\b|<img\b/.test(g.html));
			assert.equal((g.html.match(/<script\b/g) ?? []).length, 2);
			const a = g.awal;
			assert.equal(a.model.namaMataKuliah, 'Pergudangan Data');
			assert.deepEqual(a.model.modul.map((m) => m.id), [MODUL_PGD, 'module-03', 'module-04'], 'pemilih modul: hanya modul yang butuh basis data');
			assert.equal(langkahCek(a.bagian), 'menunggu', 'langkah Periksa menunggu basis data menyala');
			assert.equal(a.model.modulTerpilih, 0);
			assert.deepEqual(langkah(a.bagian), { layanan: 'kini', dataset: 'kini', kerja: 'info', jelajah: 'menunggu', er: 'menunggu' });
			assert.ok(a.bagian['b-layanan'].includes('<code>localhost:5433</code>') && a.bagian['b-layanan'].includes('data-tindakan="nyalakan"'));
			assert.ok(a.bagian['b-dataset'].includes('Belum disiapkan') && a.bagian['b-dataset'].includes('0/9 berkas'));
			assert.ok(a.bagian['b-kerja'].includes('data-tindakan="buka-berkas" data-indeks="0"') && a.bagian['b-kerja'].includes('modul-02/02_muat.sql — belum ada di folder kerja'));
			assert.ok(a.bagian['b-web'].includes('Studi Kasus, portofolio kelompok, dan salinan data Kelas'));
		});
		periksa('panel: "Nyalakan" lewat relay (service.start); katalog lewat pipa agent dengan amplop ber-services; diagram ER memuat tabel dan relasi dari kunci asing; nama kolom ber-HTML tetap teks', () => {
			assert.equal(g.nyalakan, 'berhasil');
			assert.ok(opPgd.indexOf('service.start') > opPgd.indexOf('service.status') && opPgd.indexOf('service.status') >= 0);
			assert.deepEqual(kirimPgd.find((x) => x.operation === 'service.start'), { deviceId: 'dev-uji', operation: 'service.start', payload: { courseId: COURSE_PGD } });
			assert.ok(!opPgd.includes('db.catalog') && !opPgd.includes('sql.execute'), 'katalog tidak lewat relay');
			const m = g.menyala.model;
			assert.equal(m.katalog.jenis, 'siap');
			assert.equal(m.katalog.katalog.schemas.length, 2);
			assert.deepEqual(m.target.map((t) => [t.label, t.menyala]), [['Sumber · nusamart_oltp', true], ['Gudang · nusamart_dw', true]]);
			const d = m.er.diagram;
			assert.deepEqual([d.kotak.length, d.garis.length, d.adaInfoFk], [5, 3, true]);
			assert.deepEqual(d.garis.map((x) => x.label).sort(), ['dw.fakta_penjualan(order_id) → staging.orders(order_id)', 'dw.fakta_penjualan(pelanggan_sk) → dw.dim_pelanggan(pelanggan_sk)', 'dw.fakta_penjualan(produk_sk) → dw.dim_produk(produk_sk)']);
			const er = g.menyala.bagian['b-er'];
			assert.equal((er.match(/<g class="er-tabel/g) ?? []).length, 5);
			assert.equal((er.match(/<path class="er-garis"/g) ?? []).length, 3);
			assert.ok(er.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;') || er.includes('&lt;img src=x onerr'), 'nama kolom ber-HTML di-escape');
			assert.ok(!/<img|<script/.test(er) && !/<img|<script/.test(g.menyala.bagian['b-jelajah']));
			assert.ok(g.menyala.bagian['b-jelajah'].includes('— 4 tabel') && g.menyala.bagian['b-jelajah'].includes('data-tindakan="pratinjau"'));
			assert.deepEqual(langkah(g.menyala.bagian), { layanan: 'selesai', dataset: 'kini', kerja: 'info', jelajah: 'info', er: 'info' });
		});
		periksa('panel: "Siapkan dataset" lalu "Muat data ke basis data sumber" sebagai job relay {courseId, moduleId, job:true}; status belum → siap → dimuat; sibuk tidak tertinggal', () => {
			assert.equal(g.muatSebelumSiap, 'ditolak', 'memuat sebelum dataset lengkap ditolak ekstensi');
			assert.equal(g.siapkan, 'selesai');
			assert.equal(g.muat, 'selesai');
			assert.deepEqual(kirimPgd.find((x) => x.operation === 'dataset.verify'), { deviceId: 'dev-uji', operation: 'dataset.verify', payload: { courseId: COURSE_PGD, moduleId: MODUL_PGD } });
			assert.deepEqual(kirimPgd.find((x) => x.operation === 'dataset.materialize'), { deviceId: 'dev-uji', operation: 'dataset.materialize', payload: { courseId: COURSE_PGD, moduleId: MODUL_PGD, job: true } });
			assert.deepEqual(kirimPgd.find((x) => x.operation === 'service.postgres.load'), { deviceId: 'dev-uji', operation: 'service.postgres.load', payload: { courseId: COURSE_PGD, moduleId: MODUL_PGD, job: true } });
			assert.ok(opPgd.indexOf('dataset.materialize') < opPgd.indexOf('service.postgres.load'));
			const tagih = permintaan.filter((x) => x.jalur.startsWith('/api/relay/jobs/job-pgd-')).map((x) => x.jalur);
			assert.deepEqual(tagih, ['/api/relay/jobs/job-pgd-1?since=0', '/api/relay/jobs/job-pgd-1?since=20', '/api/relay/jobs/job-pgd-2?since=0', '/api/relay/jobs/job-pgd-2?since=20']);
			assert.ok(g.setelahSiapkan.bagian['b-dataset'].includes('Siap di laptop, belum dimuat') && g.setelahSiapkan.bagian['b-dataset'].includes('9/9 berkas') && g.setelahSiapkan.bagian['b-dataset'].includes('Dataset modul siap di data/raw/'));
			assert.ok(g.setelahMuat.bagian['b-dataset'].includes('Sudah dimuat') && g.setelahMuat.bagian['b-dataset'].includes('7 tabel, 12.345 baris') && g.setelahMuat.bagian['b-dataset'].includes('Muat ulang data sumber'));
			assert.ok(g.setelahMuat.bagian['b-layanan'].includes('data nusamart-oltp 2026.1, 12.345 baris'));
			assert.deepEqual(langkah(g.setelahMuat.bagian), { layanan: 'selesai', dataset: 'selesai', kerja: 'info', jelajah: 'info', er: 'info' });
			assert.deepEqual([g.awal.sibuk, g.setelahSiapkan.sibuk, g.setelahMuat.sibuk], [false, false, false]);
			assert.ok(pgd.dataset && pgd.dimuat);
		});
		periksa('panel: pilih skema dan saring diagram, buka berkas .sql modul dari folder kerja, modul terkunci menampilkan pesan server; pesan tak sah ditolak tanpa efek', () => {
			assert.equal(g.pilihSkema.hasil, 'skema');
			assert.deepEqual([g.pilihSkema.kotak, g.pilihSkema.luar, g.pilihSkema.garis], [5, ['staging.orders'], 3], 'skema dw: tabel luar staging.orders ikut digambar');
			assert.equal(g.saring.hasil, 'saring');
			assert.deepEqual(g.saring.nama.sort(), ['dim_produk', 'fakta_penjualan']);
			assert.equal(g.bukaBerkas.hasil, 'dibuka');
			assert.equal(nj(nyata(g.bukaBerkas.aktif)), nj(path.join(nyata(ws), COURSE_PGD, 'modul-02', '01_ddl.sql')));
			assert.equal(g.bukaBerkasBelumAda, 'tidak_ada');
			assert.equal(g.modulTerkunci.hasil, 'terkunci', 'modul yang belum dirilis tidak dapat dipilih walau pesannya dikarang');
			assert.equal(g.modulTerkunci.terpilih, MODUL_PGD, 'modul terpilih tidak berubah');
			assert.equal(g.modulLain.hasil, 'modul');
			assert.ok(g.modulLain.dataset.includes('tidak memakai dataset'), 'modul tanpa dataset lokal');
			assert.deepEqual(g.ditolak, Array(g.ditolak.length).fill('ditolak'));
			assert.ok(g.ditolak.length >= 8);
			const jumlah = (op) => opPgd.filter((o) => o === op).length;
			assert.deepEqual([jumlah('service.start'), jumlah('service.stop'), jumlah('dataset.materialize'), jumlah('service.postgres.load')], [1, 0, 1, 1], 'pesan tak sah tidak memicu operasi apa pun');
		});
		periksa('pemilih modul panel: hanya modul yang sudah dirilis bagi mahasiswa ini — dari kurikulum Kelas (data pohon "Kelas saya"), sisanya ditanyakan ke server; yang terkunci tampil nonaktif dengan pesan server', () => {
			const m = g.awal.model.modul;
			assert.deepEqual(m.map((x) => [x.id, x.terkunci === true]), [[MODUL_PGD, false], ['module-03', true], ['module-04', false]]);
			assert.equal(m[1].keterangan, 'Materi modul ini belum dirilis dosen. Dirilis 12-10-2026 08.00 WIB.');
			assert.equal(g.awal.model.modulMemuat, false);
			const kepala = g.awal.bagian['b-kepala'];
			assert.ok(kepala.includes('<option value="0" selected>Modul 02 · Skema Bintang</option>'), kepala);
			assert.ok(kepala.includes('<option value="1" disabled title="Materi modul ini belum dirilis dosen. Dirilis 12-10-2026 08.00 WIB.">Modul 03 · ETL — belum dirilis</option>'), kepala);
			assert.ok(kepala.includes('<option value="2">Modul 04 · SCD</option>'));
			// Sumbernya server: kurikulum Kelas mata kuliah itu, lalu info modul HANYA untuk yang tidak terbukti terbuka.
			const jalur = permintaan.map((x) => `${x.metode} ${x.jalur}`);
			assert.ok(jalur.includes('GET /api/me/kelas/k-pgd/kurikulum'));
			const iKur = jalur.indexOf('GET /api/me/kelas/k-pgd/kurikulum');
			const tanya = (id) => jalur.map((j, i) => [j, i]).filter(([j]) => j === `GET /api/courses/${COURSE_PGD}/modules/${id}`).map(([, i]) => i);
			assert.ok(tanya('module-03').length >= 1 && tanya('module-03')[0] > iKur, 'modul 03 ditanyakan ke server setelah kurikulum');
			assert.ok(tanya('module-04').length >= 1, 'modul 04 (tidak dirujuk lesson) ditanyakan, tidak ditebak terkunci');
			assert.ok(!jalur.includes(`GET /api/courses/${COURSE_PGD}/modules/module-00`), 'modul tanpa basis data tidak ditawarkan dan tidak ditanyakan');
			assert.equal(kirimPgd.filter((x) => x.payload?.moduleId === 'module-03').length, 0, 'tidak ada operasi relay untuk modul terkunci');
		});
		const kirimCek = kirimPgd.filter((x) => x.operation === 'sql.check');
		periksa('checkpoint Pergudangan Data dari aplikasi: sql.check sebagai job relay {courseId, moduleId, job:true} — bukan checkpoint.run; gagal lalu lulus; hasil per butir; server yang mencatat riwayat', () => {
			const c = g.cek;
			assert.equal(c.sebelumNyala, 'ditolak', 'tombol Periksa mati selama basis data belum menyala');
			assert.deepEqual([c.gagal.hasil, c.lulus.hasil], ['belum_lulus', 'lulus']);
			assert.ok(kirimCek.length >= 2);
			for (const k of kirimCek) assert.deepEqual(k, { deviceId: 'dev-uji', operation: 'sql.check', payload: { courseId: COURSE_PGD, moduleId: MODUL_PGD, job: true } }, 'muatan persis seperti web: tanpa skrip, path, atau saklar apa pun');
			assert.equal(permintaan.filter((x) => x.jalur === '/api/relay/dispatch' && x.badan?.operation === 'checkpoint.run' && x.badan?.payload?.courseId === COURSE_PGD).length, 0, 'modul berpemeriksa SQL tidak pernah dikirimi checkpoint.run');
			assert.ok(permintaan.some((x) => /^\/api\/relay\/jobs\/job-cek-1\?since=/.test(x.jalur)));
			// Panel: status tertulis per butir, teks dari "agent" di-escape, tanpa keluaran mentah psql atau nama artefak pemeriksa.
			const hg = c.gagal.bagian;
			assert.equal((hg.match(/<li class="cek__butir/g) ?? []).length, 3);
			assert.ok(hg.includes('1/3 butir lulus') && hg.includes('✗</span> GAGAL</span>') && hg.includes('ada 3 baris ganda') && hg.includes('Periksa lagi'), hg);
			assert.ok(hg.includes('&lt;b&gt;unik&lt;/b&gt;') && !hg.includes('<b>unik'));
			assert.equal(c.gagal.langkah, 'kini');
			assert.ok(c.lulus.bagian.includes('2/3 butir lulus') && c.lulus.bagian.includes('lencana--dimuat'));
			assert.equal(c.lulus.langkah, 'selesai');
			for (const teks of [hg, c.lulus.bagian, JSON.stringify(c.modeTugas), JSON.stringify(c.mati), g.html]) {
				assert.ok(!teks.includes(KELUARAN_MENTAH_PSQL), 'keluaran mentah psql tidak ditampilkan');
				assert.ok(!teks.includes(ARTEFAK_CEK), 'nama artefak pemeriksa tidak ditampilkan');
			}
			// "Server" mencatat tiap pemeriksaan yang selesai; riwayatnya tampil di panel Tugas & tenggat.
			assert.deepEqual(pgd.riwayat.map((r) => [r.status, r.mode]).slice(0, 2), [['GAGAL', null], ['LULUS', null]]);
			assert.ok(c.pohon.riwayat.length >= 2 && c.pohon.riwayat.some((r) => r.startsWith('LULUS |')) && c.pohon.riwayat.some((r) => r.startsWith('GAGAL |')), JSON.stringify(c.pohon.riwayat));
			assert.ok(!c.pohon.riwayat.some((r) => /Mode tugas/.test(r)), 'pemeriksa SQL tidak punya Mode tugas di riwayat');
		});
		periksa('checkpoint SQL lewat perintah "Jalankan checkpoint (Mode tugas)": tetap sql.check tanpa saklar tugas; tidak berjalan saat basis data mati (pesan jelas); dapat dihentikan (job.cancel)', () => {
			const c = g.cek;
			// Mode tugas diminta untuk modul SQL: dijalankan sebagai pemeriksa SQL biasa, seperti di web.
			assert.equal(c.modeTugas.jenis, 'hasil');
			assert.equal(c.modeTugas.hasil.pemeriksa, 'sql');
			assert.deepEqual(c.modeTugas.hasil.findings.map((f) => f.status), ['LULUS', 'LULUS', 'LEWAT']);
			assert.ok(!kirimCek.some((k) => 'tugas' in k.payload), 'saklar tugas tidak pernah dikirim ke pemeriksa SQL');
			// Panel Tugas: modul SQL satu tombol tanpa "Mode tugas"; modul Python tetap dua tombol.
			assert.equal(c.pohon.jenis, 'sql');
			assert.deepEqual(c.pohon.konteks, ['tugasSql']);
			assert.deepEqual(c.pohon.aksi.filter((a) => /checkpoint/i.test(a)), ['Jalankan checkpoint SQL']);
			assert.equal(c.pohonPython.jenis, 'python');
			assert.deepEqual(c.pohonPython.konteks, ['tugas']);
			assert.deepEqual(c.pohonPython.aksi.filter((a) => /checkpoint/i.test(a)), ['Jalankan checkpoint', 'Jalankan checkpoint (Mode tugas)']);
			// Dihentikan pengguna: job.cancel lewat relay dengan id job itu.
			assert.equal(c.dihentikan.hasil, 'dihentikan');
			assert.equal(c.dihentikan.pesanHenti, 'dihentikan');
			assert.ok(c.dihentikan.bagian.includes('Pemeriksaan dihentikan.'), c.dihentikan.bagian);
			const batal = permintaan.filter((x) => x.jalur === '/api/relay/dispatch' && x.badan?.operation === 'job.cancel').map((x) => x.badan);
			assert.equal(batal.length, 1);
			assert.match(batal[0].payload.jobId, /^job-cek-\d+$/);
			// Basis data mati: agent menolak (service_stopped) → saran yang dapat ditindaklanjuti, tanpa butir.
			assert.equal(c.mati.jenis, 'galat');
			assert.equal(c.mati.kode, 'service_stopped');
			assert.match(c.mati.pesan, /Basis data belum menyala/);
			// Modul tanpa pemeriksa (katalog: checkpointArtifact kosong): langkah Periksa menjelaskannya, tanpa tombol.
			assert.ok(g.modulLain.cek.includes('tidak punya pemeriksa checkpoint otomatis') && !g.modulLain.cek.includes('data-tindakan="periksa"'));
			// Arahan "ke web" untuk checkpoint sudah tidak ada di panel.
			assert.ok(!/Pemeriksa SQL modul \(checkpoint SQL\)|halaman Checkpoint di web/.test(g.html + g.setelahMuat.bagian['b-web']));
		});
	} else {
		dilewati.push('panel Pergudangan Data: ekstensi yang diuji belum punya kait uji panel (versi lama)');
	}

	// --- Tautkan GitHub dari aplikasi -----------------------------------------------------
	if (h.github) {
		const G = h.github;
		const kirimGh = permintaan.filter((x) => x.jalur === '/api/relay/dispatch' && String(x.badan?.operation).startsWith('github.')).map((x) => x.badan);
		periksa('GitHub: perintah Tautkan/Putuskan terdaftar; baris GitHub di view Status (belum tertaut → tertaut sebagai @login → belum tertaut)', () => {
			for (const p of ['dsworkbench.github.link', 'dsworkbench.github.unlink']) assert.ok(h.perintah.includes(p), p);
			assert.deepEqual(G.awal.keadaan, { jenis: 'belum' });
			assert.deepEqual([G.awal.baris?.keterangan, G.awal.baris?.konteks], ['belum tertaut', 'githubBelum']);
			assert.deepEqual(G.tertaut.keadaan, { jenis: 'tertaut', login: 'mhs-uji', diSini: true });
			assert.deepEqual([G.tertaut.baris?.keterangan, G.tertaut.baris?.konteks], ['tertaut sebagai @mhs-uji', 'githubTertaut']);
			assert.deepEqual(G.putus.keadaan, { jenis: 'belum' });
			assert.ok(h.lingkungan.status.includes('github'), 'baris GitHub ada di view Status');
		});
		periksa('GitHub tautkan: mulai → kode → menunggu → tertaut, seluruhnya lewat relay ke perangkat token; token GitHub tidak pernah sampai ke ekstensi', () => {
			assert.deepEqual(G.taut.hasil, { jenis: 'tertaut', login: 'mhs-uji' });
			assert.deepEqual(G.taut.kode, { userCode: 'UJI1-KODE', url: 'https://github.com/login/device', expiresIn: 900, interval: 1, appName: 'Workbench Uji' });
			assert.ok(G.taut.kemajuan.some((t) => /kode UJI1-KODE — menunggu persetujuan di GitHub/.test(t)), JSON.stringify(G.taut.kemajuan));
			assert.deepEqual(kirimGh.slice(0, 3), [
				{ deviceId: 'dev-uji', operation: 'github.auth_start', payload: {} },
				{ deviceId: 'dev-uji', operation: 'github.auth_poll', payload: {} },
				{ deviceId: 'dev-uji', operation: 'github.auth_poll', payload: {} },
			]);
			// Token ada di jawaban "Local Runner" palsu; ekstensi tidak meneruskannya ke mana pun.
			assert.ok(!JSON.stringify(h).includes(TOKEN_GITHUB), 'token GitHub tidak ada di hasil apa pun dari ekstensi');
			assert.ok(!JSON.stringify(permintaan).includes(TOKEN_GITHUB), 'token GitHub tidak dikirim ekstensi ke server');
			assert.deepEqual([...new Set(permintaan.filter((x) => x.jalur.startsWith('/api/me/integrations/github') && !x.jalur.includes('submit-gate')).map((x) => `${x.metode} ${x.jalur}`))], ['GET /api/me/integrations/github'], 'di luar relay hanya GET status');
		});
		periksa('GitHub putuskan (github.auth_revoke lewat relay), ditolak di GitHub, dan dibatalkan pengguna; alamat verifikasi asing tidak pernah dibuka', () => {
			assert.deepEqual(G.putus.hasil, { jenis: 'diputus', jauh: true });
			assert.equal(gh.dicabut, 1);
			assert.deepEqual(kirimGh.find((x) => x.operation === 'github.auth_revoke'), { deviceId: 'dev-uji', operation: 'github.auth_revoke', payload: {} });
			assert.deepEqual(G.ditolak.hasil, { jenis: 'ditolak' });
			// Skenario kedua mengirim alamat verifikasi di host lain: diganti alamat tetap github.com.
			assert.equal(G.ditolak.kode.url, 'https://github.com/login/device');
			assert.deepEqual(G.batal.hasil, { jenis: 'batal' });
			assert.equal(G.batal.kode.userCode, 'UJI3-KODE');
			const setelahBatal = kirimGh.slice(kirimGh.findIndex((x, i) => x.operation === 'github.auth_start' && kirimGh.slice(0, i + 1).filter((y) => y.operation === 'github.auth_start').length === 3));
			assert.deepEqual(setelahBatal.map((x) => x.operation), ['github.auth_start'], 'setelah dibatalkan tidak ada tagihan lagi dan tidak ada auth_revoke');
			assert.deepEqual(G.akhir.keadaan, { jenis: 'belum' });
			for (const u of G.akhir.dibuka) assert.equal(new URL(u).hostname, 'github.com');
		});
	} else {
		dilewati.push('Tautkan GitHub: ekstensi yang diuji belum punya kait uji (versi lama)');
	}

	// --- Penyimpanan tanda masuk dan gerak avatar -------------------------------------------
	if (h.rahasia) {
		periksa('penyimpanan rahasia: tanpa penanda dari jendela sebelumnya tidak ada alarm ("belum tahu"); baris Akun tanpa tanda', () => {
			assert.equal(h.rahasia.putusan, 'belum_tahu');
			assert.equal(h.rahasia.tidakTersimpan, false);
			assert.equal(h.rahasia.akun.keterangan, 'Mahasiswa Uji');
			assert.ok(h.perintah.includes('dsworkbench.auth.explainStorage'));
		});
	}
	if (h.animasi) {
		periksa('gerak avatar Sosial: bawaan `selalu` di kerangka dan pesan panel; pengaturan `mati`/`ikutiSistem` berlaku tanpa memuat ulang; dikembalikan ke bawaan', () => {
			const a = h.animasi;
			assert.deepEqual(a.awal, { pengaturan: 'selalu', terkirim: 'selalu', kerangka: 'selalu' });
			assert.deepEqual([a.mati.pengaturan, a.mati.terkirim], ['mati', 'mati'], 'pesan keadaan membawa mode baru ke panel yang sedang tampak');
			assert.deepEqual([a.ikuti.pengaturan, a.ikuti.terkirim], ['ikutiSistem', 'ikutiSistem']);
			assert.deepEqual([a.akhir.pengaturan, a.akhir.terkirim], ['selalu', 'selalu']);
			assert.ok(a.kerangkaTetap, 'panel tidak dimuat ulang oleh perubahan pengaturan (kerangka ber-nonce yang sama)');
			assert.ok(a.css.selaluSaatKurangiGerak && a.css.mati && a.css.tersembunyi, 'kerangka memuat aturan ketiga mode');
		});
	}

	// Agent menutup diri begitu pipanya ditutup (stdin EOF) dan pamit ke server beberapa puluh
	// milidetik kemudian; aplikasi tidak menunggunya keluar. Terukur: catatan "disconnect" bisa
	// tertulis belasan milidetik SETELAH proses aplikasi selesai, jadi ditunggu sebentar di sini.
	const bacaCatatanAgent = () => JSON.parse(fs.readFileSync(catatanAgent, 'utf8'));
	for (const batas = Date.now() + 5000; Date.now() < batas && !bacaCatatanAgent().some((x) => x.jenis === 'disconnect'); ) await new Promise((r) => setTimeout(r, 100));
	const agent = bacaCatatanAgent();
	periksa('agent: capability ide.stdio.v1, amplop diterima lewat relay, hasil job pipa tidak ke server', () => {
		const connect = agent.find((x) => x.jenis === 'connect');
		assert.ok(connect.capabilities.includes('ide.stdio.v1'));
		assert.equal(connect.deviceId, 'dev-uji');
		const amplop = agent.filter((x) => x.jenis === 'result' && String(x.messageId).startsWith('relay-env-'));
		assert.ok(amplop.length >= 1 && amplop.every((x) => x.status === 'ok'));
		assert.equal(agent.filter((x) => x.jenis === 'job_update').length, 0);
		assert.ok(!JSON.stringify(agent).includes('jangan-bocor'));
	});
	periksa('penutupan rapi: agent pamit ke server saat VS Code ditutup', () => {
		assert.ok(agent.some((x) => x.jenis === 'disconnect'), `catatan agent berakhir dengan: ${agent.slice(-5).map((x) => `${x.jenis}${x.messageId ? `(${x.messageId})` : ''}`).join(', ')}`);
	});
	// --- Pembaruan (ADR-072 §7b) ---------------------------------------------------
	if (h.pembaruan?.didukung && !bolehPerbarui) {
		periksa('pembaruan: aplikasi ini (product.json tanpa sditera.dsworkbench di builtInExtensionsEnabledWithAutoUpdates) menolak memasang versi baru ekstensi bawaannya, seperti diduga', () => {
			assert.ok(h.perintah.includes('dsworkbench.update.check'));
			assert.match(h.pembaruan.pasang, /^gagal: .*built-in extension and not allowed to be updated/);
			assert.deepEqual(fs.readdirSync(extDir).filter((n) => n.toLowerCase().startsWith('sditera.dsworkbench-')), []);
			assert.match(h.pembaruan.versiAplikasi, /^\d+\.\d+\.\d{5,}$/);
		});
		peringatan.push('Aplikasi ini TIDAK dapat memperbarui ekstensinya sendiri: product.json belum memuat "sditera.dsworkbench" di builtInExtensionsEnabledWithAutoUpdates (lihat apps/ide/product/README.md).');
	} else if (h.pembaruan?.didukung) {
		periksa('pembaruan: perintah "Periksa pembaruan" terdaftar; paket .vsix ber-id sama dan versi lebih baru terpasang lewat workbench.extensions.installExtension', () => {
			assert.ok(h.perintah.includes('dsworkbench.update.check'));
			assert.equal(h.pembaruan.pasang, 'terpasang');
			// Aplikasi bermerek: versi produk utuh (1.135.0NNNN), bukan `vscode.version` yang dipangkas fork.
			if (bermerek) assert.match(h.pembaruan.versiAplikasi, /^\d+\.\d+\.\d{5,}$/);
			else assert.equal(h.pembaruan.versiAplikasi, h.vscode);
			const terpasang = fs.readdirSync(extDir).filter((n) => n.toLowerCase().startsWith('sditera.dsworkbench-'));
			assert.deepEqual(terpasang, [`sditera.dsworkbench-${VERSI_VSIX_UJI}`], `isi folder ekstensi pengguna: ${fs.readdirSync(extDir).join(', ')}`);
			assert.equal(JSON.parse(fs.readFileSync(path.join(extDir, terpasang[0], 'package.json'), 'utf8')).version, VERSI_VSIX_UJI);
		});
	} else {
		dilewati.push('pembaruan: ekstensi yang diuji belum punya kait uji pembaruan (versi lama)');
	}

	for (const c of cek) console.log(`  ✔ ${c}`);
	for (const d of dilewati) console.log(`  – dilewati: ${d}`);
	for (const w of peringatan) console.log(`  ! ${w}`);
	console.log(`\nUji integrasi lulus: ${cek.length} pemeriksaan (${h.app} ${h.vscode}, agent ${h.statusSiap.versi}).`);
	ringkasan = { ...ringkasan, lulus: true, agent: h.statusSiap.versi };
	kode = 0;
} catch (e) {
	console.error(`\nUji integrasi GAGAL: ${e?.stack || e}`);
	ringkasan = { ...ringkasan, lulus: false, galat: String(e?.message || e).slice(0, 4000) };
} finally {
	if (process.env.DSW_RINGKASAN) {
		const pkgMuat = JSON.parse(fs.readFileSync(path.join(akarMuat, 'package.json'), 'utf8'));
		fs.writeFileSync(process.env.DSW_RINGKASAN, JSON.stringify({
			...ringkasan,
			platform: process.platform,
			arch: process.arch,
			aplikasi: KODE,
			ekstensi: { folder: akarMuat, versi: pkgMuat.version },
			agentDari: pakaiPayload ? 'agent-payload' : 'sumber',
			jumlahLulus: cek.length,
			pemeriksaan: cek,
			dilewati,
			peringatan,
			temaBawaanProduk: ringkasan.temaBawaan,
		}, null, 1));
	}
	server.closeAllConnections();
	server.close();
	fs.rmSync(tmp, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
}
process.exit(kode);
