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
const envAgent = { PYTHONPATH: pypath.join(path.delimiter), PYTHONDONTWRITEBYTECODE: '1' };
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
						{ id: MODUL2, order: 2, runtimeProfile: 'python-data-science', starterFiles: ['notebooks/M02_starter.ipynb'] },
					],
				}],
			});
		}
		if (req.method === 'GET' && req.url === '/api/auth/me') return jawab(200, { user: { id: 'u-uji', username: USERNAME } });
		if (req.method === 'GET' && req.url === '/api/me/kelas') return jawab(200, { kelas: [] });
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
			return jawab(200, { tasks: [{ assignmentId: 'a-m02', courseId: COURSE, moduleId: MODUL2, title: 'Tugas Modul 2', dueAt: '2030-01-01T00:00:00+00:00', attemptStatus: 'none', attemptId: null, gradeReleased: false, grade: null, href: `/courses/${COURSE}/modules/${MODUL2}/tasks?kelasId=k1`, release: { locked: false, releaseAt: null, lockSource: null } }] });
		}
		if (kunci === `GET ${modul2}/assignments`) {
			return jawab(200, { assignments: [{ id: 'a-m02', courseId: COURSE, moduleId: MODUL2, title: 'Tugas Modul 2', requiredArtifacts: [{ name: 'M02_NIM.ipynb', label: 'Notebook', contentTypes: ['application/x-ipynb+json'] }, { name: 'catatan.md', label: 'Catatan', contentTypes: ['text/markdown'] }], analysisQuestions: [] }], submission: { manualUpload: false } });
		}
		const percobaan = `/api/courses/${COURSE}/assignments/a-m02/attempts`;
		if (kunci === `GET ${percobaan}`) return jawab(200, { attempts: [] });
		if (kunci === `POST ${percobaan}`) return jawab(201, { attempt: { id: 'att-uji', assignmentId: 'a-m02', status: 'draft', artifacts: [], answers: {} } });
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
		if (kunci === 'POST /api/relay/dispatch') {
			const messageId = `rly-${relayHasil.size + 1}`;
			let result = { status: 'rejected', payload: {}, detail: 'operasi tidak dikenal uji' };
			if (badan.operation === 'git.status') {
				gitDiperiksa = true;
				result = { status: 'ok', payload: { initialized: true, clean: true, ahead: 0, behind: 0, branch: 'main' }, detail: null };
			} else if (badan.operation === 'checkpoint.run') {
				result = { status: 'ok', payload: { result: { status: 'LULUS', summary: '2 butir lulus', passed: true, durationSeconds: 0.4, findings: [{ status: 'LULUS', title: 'Berkas ada' }, { status: 'LULUS', title: 'Sel pertama berjalan' }] } }, detail: null };
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
			env: { ...process.env, DSW_DATA_ROOT: folderRuntime, DSW_OUT: out, DSW_SERVER: SERVER, DSW_TOKEN: TOKEN, DSW_USERNAME: USERNAME, DSW_COURSE: COURSE, DSW_WS_COURSE: wsCourse, DSW_NOTEBOOK: NAMA_NB, DSW_NOTEBOOK2: NAMA_NB2, DSW_NOTEBOOK_LUAR: nbLuar, DSW_MODUL2: MODUL2, DSW_UJI_VSIX: vsixUji },
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
		assert.deepEqual(h.lingkungan.status, ['akun', 'agent', 'lingkungan', 'disk', 'folder', 'versi']);
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
		const iGit = urut.lastIndexOf('POST /api/relay/dispatch');
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
		assert.deepEqual(b.model.tenggat.map((t) => t.judul), ['Tugas Modul 2']);
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

	const agent = JSON.parse(fs.readFileSync(catatanAgent, 'utf8'));
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
		assert.ok(agent.some((x) => x.jenis === 'disconnect'));
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
