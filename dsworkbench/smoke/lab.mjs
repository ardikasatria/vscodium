// Uji integrasi MODE LAB: VS Code sungguhan + ekstensi + Local Runner sungguhan, dalam
// beberapa peluncuran aplikasi terhadap satu profil sementara dan satu server palsu.
//
// Bagian 1 — peluncuran BIASA (bukan proses uji, penyimpanan aplikasi sungguhan di disk),
// digerakkan ekstensi pembantu kecil lewat perintah publik; yang diamati adalah lalu lintas
// ke server palsu:
//   biasa-1 → tanpa mode lab: masuk, tutup
//   biasa-2 → tanpa mode lab: dibuka lagi → sesi PULIH (pembanding: laptop pribadi tidak berubah)
//   lab-1   → mode lab (DSW_LAB_CONFIG): token tersimpan tadi dicabut + dihapus saat mulai;
//             masuk lagi; aplikasi ditutup biasa → penjaga sesi mengakhiri sesi
//   lab-2   → mode lab: dibuka lagi → sesi TIDAK pulih, jendela/folder tidak dipulihkan
// Bagian 2 — proses uji VS Code (kait uji ekstensi):
//   masuk   → sapu sisa perangkat tertinggal, masuk, token tidak disimpan, keluar menganggur
//   paksa   → masuk, lalu aplikasi dimatikan paksa (SIGKILL): penjaga sesi yang membersihkan
//
// Tidak menyentuh `~/.workbench-agent` maupun folder kerja pengguna. Dijalankan oleh
// `npm run test:integration` setelah suite utama; di bundel uji asap sebagai `lab.mjs`.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as http from 'node:http';
import * as os from 'node:os';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

const akarEkstensi = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const akarMuat = process.env.DSW_EXT_PATH ? path.resolve(process.env.DSW_EXT_PATH) : akarEkstensi;
const repo = path.resolve(akarEkstensi, '..', '..', '..');
const KODE = process.env.DSW_VSCODE || {
	darwin: '/Applications/Visual Studio Code.app/Contents/MacOS/Code',
	win32: path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Microsoft VS Code', 'Code.exe'),
}[process.platform] || 'code';
const PYTHON = process.env.DSW_PYTHON || 'python3';
const SUITE = process.env.DSW_SUITE_LAB ? path.resolve(process.env.DSW_SUITE_LAB) : path.join(akarEkstensi, 'out-test', 'integration', 'suiteLab.cjs');
const FIXTURE_AGENT = process.env.DSW_FIXTURE_AGENT ? path.resolve(process.env.DSW_FIXTURE_AGENT) : path.join(akarEkstensi, 'test', 'fixtures', 'agen_sungguhan.py');
const COURSE = 'data-wrangling';
const USERNAME = '122450001';
const RAHASIA_LAMA = 'kredensial-perangkat-lama-0123456789';
// Pasangan dari aplikasi DSWorkbench lama (pairing dengan kode) yang diambil alih saat masuk.
const RAHASIA_PAIRING = 'kredensial-pairing-aplikasi-lama-0123456789';

if (!fs.existsSync(KODE) && KODE !== 'code') {
	console.error(`VS Code tidak ditemukan di ${KODE}. Setel DSW_VSCODE.`);
	process.exit(2);
}
for (const j of [path.join(akarMuat, 'dist', 'extension.js'), path.join(akarMuat, 'dist', 'penjaga.js'), SUITE, FIXTURE_AGENT]) {
	if (!fs.existsSync(j)) {
		console.error(`${j} tidak ada. Jalankan: npm run build && npm run build:test`);
		process.exit(2);
	}
}

// Jalur pendek: soket IPC VS Code dan pipa penjaga dibatasi ±103 karakter.
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dswl-'));
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
fs.writeFileSync(path.join(wsCourse, 'catatan-lab.txt'), 'isi awal\n');
const folderRuntime = path.join(tmp, 'r', 'DSWorkbench');
const catatanAgent = path.join(tmp, 'agent.json');
const catatanPenjaga = path.join(tmp, 'penjaga.jsonl');
const konfigLab = path.join(tmp, 'mode-lab.json');
fs.writeFileSync(konfigLab, JSON.stringify({ schema: 1, aktif: true, menganggurMenit: 5, namaLab: 'Lab Uji', bersihkanGithub: true, bersihkanRiwayat: true }));

const pypath = [];
for (const grup of ['packages', 'providers', 'services', 'agent']) {
	const g = path.join(repo, grup);
	if (!fs.existsSync(g)) continue;
	for (const n of fs.readdirSync(g)) {
		const src = path.join(g, n, 'src');
		if (fs.existsSync(src)) pypath.push(src);
	}
}
const pakaiPayload = process.env.DSW_AGENT_PAYLOAD === '1';
if (pakaiPayload) {
	pypath.length = 0;
	pypath.push(path.join(akarMuat, 'agent-payload', 'src'));
}
const envAgent = { PYTHONPATH: pypath.join(path.delimiter), PYTHONDONTWRITEBYTECODE: '1' };
const perintahAgent = [PYTHON, FIXTURE_AGENT, ws, catatanAgent, pemicu];

// --- server palsu (Control API): masuk aplikasi, pencabutan, relay -------------------
const permintaan = [];
const perangkat = new Map([
	['dev-lama', { credential: RAHASIA_LAMA, dicabut: false }],
	['dev-pairing-1', { credential: RAHASIA_PAIRING, dicabut: false }],
	['dev-pairing-2', { credential: RAHASIA_PAIRING, dicabut: false }],
]);
// `false` = server lama yang belum punya `POST /api/agent/supersede` (dijawab 404 seperti rute tak dikenal).
let dukungGantikan = true;
const token = new Map();
const masuk = []; // { requestId, token, deviceId, lab }
const relayHasil = new Map();
const server = http.createServer((req, res) => {
	const bagian = [];
	req.on('data', (d) => bagian.push(d));
	req.on('end', () => {
		let badan;
		try {
			badan = JSON.parse(Buffer.concat(bagian).toString('utf8') || 'null');
		} catch {
			badan = null;
		}
		const otorisasi = req.headers.authorization;
		permintaan.push({ metode: req.method, jalur: req.url, otorisasi, cookie: req.headers.cookie, badan, pada: Date.now() });
		const jawab = (status, isi) => {
			res.statusCode = status;
			res.setHeader('Content-Type', 'application/json');
			res.end(isi === undefined ? '' : JSON.stringify(isi));
		};
		const k = `${req.method} ${req.url}`;
		if (k === 'POST /api/app/login/start') {
			const n = masuk.length + 1;
			const m = { requestId: `alr-${String(n).padStart(32, '0')}`, token: `token-lab-${n}-${'x'.repeat(24)}`, deviceId: `dev-lab-${n}`, lab: badan?.lab === true };
			masuk.push(m);
			return jawab(201, { requestId: m.requestId, userCode: 'ABCD-EFGH', pollSecret: `rahasia-${n}`, verifyPath: `/app/hubungkan?permintaan=${m.requestId}`, expiresInSeconds: 300, pollIntervalSeconds: 1, lab: m.lab });
		}
		if (k === 'POST /api/app/login/poll') {
			const m = masuk.find((x) => x.requestId === badan?.requestId);
			if (!m) return jawab(401, { error: 'autentikasi_gagal', message: 'x' });
			perangkat.set(m.deviceId, { credential: `kredensial-${m.deviceId}-0123456789`, dicabut: false });
			token.set(m.token, { deviceId: m.deviceId, berlaku: true });
			return jawab(200, { status: 'approved', appToken: m.token, appTokenExpiresAt: new Date(Date.now() + 3600_000).toISOString(), lab: m.lab, device: { id: m.deviceId, credential: perangkat.get(m.deviceId).credential }, user: { id: 'u-uji', username: USERNAME, displayName: 'Mahasiswa Uji' } });
		}
		if (k === 'POST /api/agent/self-revoke') {
			const d = perangkat.get(badan?.deviceId);
			if (!d || d.dicabut || d.credential !== badan?.credential) return jawab(401, { error: 'autentikasi_gagal', message: 'x' });
			d.dicabut = true;
			for (const t of token.values()) if (t.deviceId === badan.deviceId) t.berlaku = false;
			return jawab(204);
		}
		if (k === 'POST /api/agent/supersede' && dukungGantikan) {
			// Seperti server asli: pemanggil = perangkat BARU; perangkat lama hanya dicabut bila
			// kredensialnya cocok (server palsu ini hanya punya satu pengguna).
			const baru = perangkat.get(badan?.deviceId);
			if (!baru || baru.dicabut || baru.credential !== badan?.credential) return jawab(401, { error: 'autentikasi_gagal', message: 'x' });
			const lama = perangkat.get(badan?.replaces?.deviceId);
			const cocok = !!lama && lama !== baru && !lama.dicabut && lama.credential === badan.replaces.credential;
			if (cocok) lama.dicabut = true;
			return jawab(200, { replaced: cocok });
		}
		const t = otorisasi?.startsWith('Bearer ') ? token.get(otorisasi.slice(7)) : undefined;
		if (!t || !t.berlaku) return jawab(401, { error: 'autentikasi_gagal', message: 'Autentikasi gagal.' });
		if (k === 'POST /api/app/logout') {
			t.berlaku = false;
			const d = perangkat.get(t.deviceId);
			if (d) d.dicabut = true;
			return jawab(204);
		}
		if (k === 'GET /api/auth/me') return jawab(200, { user: { id: 'u-uji', username: USERNAME }, session: { kind: 'app', lab: true } });
		if (k === 'GET /api/me/kelas') return jawab(200, { kelas: [] });
		if (k === 'GET /api/devices') {
			return jawab(200, { devices: [...perangkat].map(([id, d]) => ({ id, name: id, state: d.dicabut ? 'revoked' : 'offline', revokedAt: d.dicabut ? new Date().toISOString() : null })) });
		}
		if (k === 'GET /api/catalog/courses') return jawab(200, { courses: [] });
		if (k === 'GET /api/me/tasks') return jawab(200, { tasks: [] });
		if (k === 'GET /api/me/announcements') return jawab(200, { announcements: [] });
		if (k === 'POST /api/relay/dispatch') {
			const messageId = `rly-${relayHasil.size + 1}`;
			relayHasil.set(messageId, { status: 'ok', payload: { revokedLocal: true, revokedRemote: false }, detail: null });
			return jawab(202, { messageId, operation: badan?.operation, queueDepth: 0 });
		}
		const m = /^GET \/api\/relay\/results\/(rly-\d+)$/.exec(k);
		if (m && relayHasil.has(m[1])) return jawab(200, { messageId: m[1], state: 'ready', operation: 'x', result: { messageId: m[1], ...relayHasil.get(m[1]) } });
		return jawab(404, { error: 'tidak_ditemukan', message: 'Tidak ditemukan.' });
	});
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const SERVER = `http://127.0.0.1:${server.address().port}`;

// Windows: `fsPath` VS Code berhuruf drive kecil dan sistem berkasnya tidak peka huruf besar/kecil.
const nj = (p) => (process.platform === 'win32' && typeof p === 'string' ? p.toLowerCase() : p);
const tidur = (ms) => new Promise((r) => setTimeout(r, ms));
async function sampai(syarat, batasMs, pesan) {
	const batas = Date.now() + batasMs;
	while (!syarat()) {
		if (Date.now() > batas) throw new Error(pesan);
		await tidur(100);
	}
}
const dari = (jalur, otorisasi) => permintaan.filter((p) => p.jalur === jalur && (otorisasi === undefined || p.otorisasi === otorisasi));
const penjaga = () => (fs.existsSync(catatanPenjaga) ? fs.readFileSync(catatanPenjaga, 'utf8').split('\n').filter(Boolean).map((b) => JSON.parse(b)) : []);
const adaDevice = () => fs.existsSync(path.join(stateDir, 'device.json'));
/** Cari teks di semua berkas profil aplikasi (termasuk basis data penyimpanan ekstensi). */
function adaDiDisk(akar, teks) {
	const jarum = Buffer.from(teks, 'utf8');
	const jarum16 = Buffer.from(teks, 'utf16le');
	const temuan = [];
	const telusur = (d) => {
		for (const e of fs.readdirSync(d, { withFileTypes: true })) {
			const j = path.join(d, e.name);
			if (e.isDirectory()) telusur(j);
			else if (e.isFile()) {
				try {
					const b = fs.readFileSync(j);
					if (b.includes(jarum) || b.includes(jarum16)) temuan.push(path.relative(akar, j));
				} catch {
					/* berkas terkunci/hilang: dilewati */
				}
			}
		}
	};
	telusur(akar);
	return temuan;
}

// Ekstensi pembantu untuk peluncuran biasa: menjalankan perintah publik DSWorkbench, menunggu
// isyarat skrip ini (berkas), menulis apa yang terlihat dari luar, lalu menutup aplikasi.
const pembantu = dir('x');
fs.writeFileSync(path.join(pembantu, 'package.json'), JSON.stringify({ name: 'pembantu-uji-lab', publisher: 'uji', version: '0.0.1', engines: { vscode: '^1.90.0' }, main: './ext.js', activationEvents: ['onStartupFinished'] }));
fs.writeFileSync(
	path.join(pembantu, 'ext.js'),
	`const vscode = require('vscode'); const fs = require('fs');
exports.activate = async () => {
	const env = process.env; const hasil = { tahap: env.DSW_LAB_TAHAP, app: vscode.env.appName, vscode: vscode.version };
	const tidur = (ms) => new Promise((r) => setTimeout(r, ms));
	try {
		const ext = vscode.extensions.getExtension('sditera.dsworkbench');
		const api = await ext.activate();
		hasil.kaitUji = api.uji !== undefined;
		hasil.folder = (vscode.workspace.workspaceFolders || []).map((f) => f.uri.fsPath);
		// 'masuk-buka-folder': setelah masuk, buka folder di jendela yang sama — jendela dimuat ulang
		// (extension host baru); aktivasi kedua dikenali dari berkas penanda.
		const penanda = env.DSW_LAB_ISYARAT + '.dimuat-ulang';
		const kedua = fs.existsSync(penanda);
		hasil.aktivasiKedua = kedua;
		if (env.DSW_LAB_AKSI === 'masuk' || (env.DSW_LAB_AKSI === 'masuk-buka-folder' && !kedua)) void vscode.commands.executeCommand('dsworkbench.login');
		const batas = Date.now() + 90000;
		while (!fs.existsSync(env.DSW_LAB_ISYARAT) && Date.now() < batas) await tidur(100);
		// Keadaan penyimpanan tanda masuk menurut ekstensi (perintah diagnostik; tidak ada di versi lama).
		const rahasia = () => Promise.resolve(vscode.commands.executeCommand('dsworkbench.auth.storageState')).then((r) => r, () => null);
		if (env.DSW_LAB_AKSI === 'masuk-buka-folder' && !kedua) {
			fs.writeFileSync(penanda, String(process.ppid));
			fs.rmSync(env.DSW_LAB_ISYARAT, { force: true });
			await vscode.commands.executeCommand('vscode.openFolder', vscode.Uri.file(env.DSW_WS_COURSE), { forceReuseWindow: true });
			return;
		}
		// 'lihat-muat-ulang': catat keadaan, muat ulang jendela (buka folder), catat lagi di aktivasi kedua.
		if (env.DSW_LAB_AKSI === 'lihat-muat-ulang' && !kedua) {
			fs.writeFileSync(penanda, JSON.stringify({ ppid: process.ppid, rahasia: await rahasia() }));
			fs.rmSync(env.DSW_LAB_ISYARAT, { force: true });
			await vscode.commands.executeCommand('vscode.openFolder', vscode.Uri.file(env.DSW_WS_COURSE), { forceReuseWindow: true });
			return;
		}
		if (env.DSW_LAB_AKSI === 'lihat-muat-ulang') hasil.pertama = JSON.parse(fs.readFileSync(penanda, 'utf8'));
		hasil.rahasia = await rahasia();
		hasil.ppid = process.ppid;
		const tab = vscode.window.tabGroups.all.flatMap((g) => g.tabs);
		hasil.tab = tab.map((t) => t.label);
		hasil.tabBerkas = tab.filter((t) => t.input instanceof vscode.TabInputText || t.input instanceof vscode.TabInputNotebook || t.input instanceof vscode.TabInputTextDiff).map((t) => t.label);
		hasil.pengaturan = Object.fromEntries(['window.restoreWindows', 'files.hotExit'].map((k) => [k, vscode.workspace.getConfiguration().inspect(k).globalValue]));
	} catch (e) { hasil.galat = String(e && e.stack || e); }
	fs.writeFileSync(env.DSW_OUT, JSON.stringify(hasil));
	await vscode.commands.executeCommand('workbench.action.quit');
};
`,
);

/**
 * Jalankan satu tahap. `biasa`: peluncuran biasa dengan ekstensi pembantu (`aksi`, `siap` =
 * syarat di sisi server sebelum aplikasi boleh ditutup). `bunuhSetelahHasil`: SIGKILL proses
 * utama begitu suite menulis hasil.
 */
async function tahap(nama, { lab, folder, bunuhSetelahHasil = false, biasa = false, aksi = 'lihat', siap = () => true, profil = userData, argTambahan = [] }) {
	const out = path.join(tmp, `hasil-${nama}.json`);
	const isyarat = path.join(tmp, `isyarat-${nama}`);
	const argumen = [
		`--extensionDevelopmentPath=${akarMuat}`,
		biasa ? `--extensionDevelopmentPath=${pembantu}` : `--extensionTestsPath=${SUITE}`,
		`--user-data-dir=${profil}`,
		`--extensions-dir=${extDir}`,
		'--disable-workspace-trust',
		'--skip-welcome',
		'--skip-release-notes',
		'--disable-updates',
		'--new-window',
		...argTambahan,
		...(process.env.DSW_ARG_APLIKASI ? JSON.parse(process.env.DSW_ARG_APLIKASI) : []),
		...(folder ? [wsCourse] : []),
	];
	const env = { ...process.env, DSW_DATA_ROOT: folderRuntime, DSW_OUT: out, DSW_SERVER: SERVER, DSW_USERNAME: USERNAME, DSW_WS_COURSE: wsCourse, DSW_LAB_TAHAP: nama, DSW_LAB_CATATAN_PENJAGA: catatanPenjaga, DSW_LAB_AKSI: aksi, DSW_LAB_ISYARAT: isyarat };
	if (lab) env.DSW_LAB_CONFIG = konfigLab;
	else delete env.DSW_LAB_CONFIG;
	const mulai = Date.now();
	const keluar = await new Promise((selesai) => {
		const anak = spawn(KODE, argumen, { stdio: ['ignore', 'pipe', 'pipe'], env });
		let log = '';
		let dibunuh = false;
		anak.stdout.on('data', (d) => (log += d));
		anak.stderr.on('data', (d) => (log += d));
		const pewaktu = setTimeout(() => anak.kill('SIGKILL'), 180_000);
		const mulaiTahap = Date.now();
		const pantau = setInterval(() => {
			if (bunuhSetelahHasil && !dibunuh && fs.existsSync(out)) {
				dibunuh = true;
				// Penutupan paksa: tidak ada `deactivate`, tidak ada kesempatan membersihkan.
				anak.kill('SIGKILL');
			}
			// Peluncuran biasa: beri isyarat "boleh tutup" setelah syarat di sisi server terpenuhi
			// (paling cepat 5 detik, agar lalu lintas saat mulai sempat terjadi).
			if (biasa && !fs.existsSync(isyarat) && Date.now() - mulaiTahap > 5_000 && siap()) fs.writeFileSync(isyarat, '1');
		}, 200);
		anak.on('close', (k) => {
			clearTimeout(pewaktu);
			clearInterval(pantau);
			selesai({ kode: k, log, dibunuh, pid: anak.pid });
		});
	});
	console.log(`tahap ${nama}: VS Code selesai (kode ${keluar.kode}${keluar.dibunuh ? ', dimatikan paksa' : ''}) dalam ${((Date.now() - mulai) / 1000).toFixed(1)} dtk`);
	if (!fs.existsSync(out)) {
		console.error(keluar.log.slice(-4000));
		throw new Error(`tahap ${nama}: uji di dalam VS Code tidak menulis hasil`);
	}
	const h = JSON.parse(fs.readFileSync(out, 'utf8'));
	hasilTahap[nama] = h;
	if (h.galat) {
		console.error(JSON.stringify(h, null, 1).slice(0, 6000));
		throw new Error(`tahap ${nama} gagal di dalam VS Code: ${h.galat}`);
	}
	return h;
}

const hasilTahap = {};
const dilewati = [];
const cek = [];
const periksa = (nama, f) => {
	f();
	cek.push(nama);
};
let ringkasan = { lulus: false };
let kode = 1;
try {
	// Profil kedua untuk bagian "penyimpanan rahasia di memori" (lihat akhir skrip): pengaturan yang sama.
	const profilMemori = dir('um');
	fs.mkdirSync(path.join(profilMemori, 'User'), { recursive: true });
	fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
	const tulisPengaturan = (isi) => {
		for (const p of [userData, profilMemori]) fs.writeFileSync(path.join(p, 'User', 'settings.json'), isi);
	};
	tulisPengaturan(
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
	console.log(`VS Code: ${KODE}\nekstensi dari: ${akarMuat}\nagent dari: ${pakaiPayload ? 'agent-payload ekstensi' : 'sumber repo'}\nprofil sementara: ${tmp}\nserver palsu: ${SERVER}`);
	const sudahMasuk = (i) => () => masuk.length === i + 1 && dari('/api/me/kelas', `Bearer ${masuk[i].token}`).length >= 1 && adaDevice();

	/** Komputer ini masih menyimpan pasangan aplikasi lama (`device.json` hasil pairing, akun yang sama). */
	const pasangLama = (deviceId) => {
		const r = spawnSync(PYTHON, [FIXTURE_AGENT, ws, catatanAgent, pemicu, '--state-dir', stateDir, '--url', SERVER, 'adopt'], {
			input: JSON.stringify({ deviceId, credential: RAHASIA_PAIRING, name: 'Laptop lama', os: os.type(), arch: os.arch(), account: USERNAME }),
			env: { ...process.env, ...envAgent },
			encoding: 'utf8',
		});
		assert.equal(r.status, 0, `adopt gagal: ${r.stderr}`);
	};
	const idDevice = () => JSON.parse(fs.readFileSync(path.join(stateDir, 'device.json'), 'utf8')).device_id;

	// === Bagian 1: peluncuran biasa, penyimpanan aplikasi sungguhan ===========================
	pasangLama('dev-pairing-1');
	const b1 = await tahap('biasa-1', { lab: false, folder: false, biasa: true, aksi: 'masuk', siap: sudahMasuk(0) });
	const tA = masuk[0];
	periksa('tanpa mode-lab.json (laptop pribadi): masuk tidak meminta sesi lab, pengaturan pengguna tidak diubah, dan menutup aplikasi tidak mencabut apa pun', () => {
		assert.ok(tA, 'masuk tidak terjadi');
		assert.equal(b1.kaitUji, false, 'peluncuran biasa, bukan proses uji');
		assert.equal(dari('/api/app/login/start')[0].badan.lab, undefined);
		assert.equal(tA.lab, false);
		assert.deepEqual(b1.pengaturan, {});
		assert.equal(dari('/api/app/logout').length, 0);
		assert.equal(dari('/api/agent/self-revoke').length, 0);
		assert.equal(adaDevice(), true);
		assert.equal(penjaga().length, 0, 'tidak ada penjaga sesi di luar mode lab');
	});
	periksa('ambil alih pasangan aplikasi lama (akun yang sama): setelah perangkat baru terbit dan tersimpan, Local Runner mencabut perangkat lama di server dengan kredensial lama itu; pengingat "cabut di web" tidak diperlukan', () => {
		const ganti = dari('/api/agent/supersede');
		assert.equal(ganti.length, 1);
		assert.deepEqual(ganti[0].badan, { deviceId: tA.deviceId, credential: perangkat.get(tA.deviceId).credential, replaces: { deviceId: 'dev-pairing-1', credential: RAHASIA_PAIRING } });
		assert.equal(ganti[0].otorisasi, undefined, 'tanpa token pengguna');
		assert.ok(permintaan.indexOf(ganti[0]) > permintaan.indexOf(dari('/api/app/login/poll').at(-1)), 'pencabutan setelah perangkat baru terbit');
		assert.equal(perangkat.get('dev-pairing-1').dicabut, true);
		assert.equal(perangkat.get(tA.deviceId).dicabut, false);
		assert.equal(idDevice(), tA.deviceId);
		assert.equal(dari('/api/devices').length, 0, 'daftar perangkat (pengingat) tidak diperiksa');
	});
	const n2 = permintaan.length;
	const mulai2 = Date.now();
	const pulih = () => permintaan.slice(n2).some((p) => p.jalur === '/api/auth/me' && p.otorisasi === `Bearer ${tA.token}`);
	const b2 = await tahap('biasa-2', { lab: false, folder: false, biasa: true, siap: () => pulih() || Date.now() - mulai2 > 20_000 });
	// Tanpa penyimpanan rahasia OS (runner tanpa keyring, `--use-inmemory-secretstorage`) token
	// memang tidak bertahan antar-peluncuran: pembanding dan uji pencabutannya dilewati.
	const tersimpanDiOs = pulih();
	if (tersimpanDiOs) {
		periksa('pembanding: tanpa mode lab sesi PULIH saat aplikasi dibuka lagi (token tersimpan dipakai tanpa masuk ulang)', () => {
			assert.equal(masuk.length, 1);
			assert.equal(token.get(tA.token).berlaku, true);
		});
	} else {
		dilewati.push('pembanding "tanpa mode lab sesi pulih" dan pencabutan token tersimpan saat mode lab mulai: penyimpanan rahasia OS tidak tersedia di sini');
	}
	if (b1.rahasia && b2.rahasia) {
		periksa(`penyimpanan tanda masuk dikenali dari perilaku lintas peluncuran (aplikasi sungguhan, ${tersimpanDiOs ? 'penyimpanan rahasia OS tersedia: "tersimpan", tanpa peringatan' : 'tanpa penyimpanan rahasia OS: "tidak tersimpan", dijelaskan'})`, () => {
			// Masuk pertama: belum ada bukti ke arah mana pun (tulis-lalu-baca di jendela yang sama bukan bukti).
			assert.deepEqual([b1.rahasia.putusan, b1.rahasia.tidakTersimpan, b1.rahasia.dijelaskan], ['belum_tahu', false, false]);
			// Peluncuran berikutnya: rahasia uji dari peluncuran sebelumnya terbaca lagi atau tidak — sejalan dengan pulih/tidaknya sesi.
			assert.deepEqual([b2.rahasia.putusan, b2.rahasia.tidakTersimpan, b2.rahasia.dijelaskan], tersimpanDiOs ? ['tersimpan', false, false] : ['tidak_tersimpan', true, true]);
		});
	} else {
		dilewati.push('penyimpanan tanda masuk: ekstensi yang diuji belum punya perintah diagnostik (versi lama)');
	}
	const n3 = permintaan.length;
	const b3 = await tahap('lab-1', { lab: true, folder: false, biasa: true, aksi: 'masuk', siap: sudahMasuk(1) });
	const tB = masuk[1];
	periksa(`mode lab diaktifkan di komputer yang sudah dipakai: ${tersimpanDiOs ? 'token tersimpan dicabut di server dan dihapus saat mulai, ' : ''}perangkat tertinggalnya disapu, lalu masuk meminta sesi lab`, () => {
		assert.ok(tB, 'masuk di mode lab tidak terjadi');
		const baru = permintaan.slice(n3);
		// Token lama hanya dipakai SEKALI: untuk mencabut dirinya.
		assert.deepEqual(baru.filter((p) => p.otorisasi === `Bearer ${tA.token}`).map((p) => [p.jalur, p.badan]), tersimpanDiOs ? [['/api/app/logout', { revokeDevice: true }]] : []);
		if (tersimpanDiOs) assert.equal(token.get(tA.token).berlaku, false);
		assert.equal(perangkat.get(tA.deviceId).dicabut, true);
		const sapu = baru.filter((p) => p.jalur === '/api/agent/self-revoke' && p.badan?.deviceId === tA.deviceId);
		assert.equal(sapu.length, 1, 'device.json sesi lama disapu lewat Local Runner');
		const mulai = baru.find((p) => p.jalur === '/api/app/login/start');
		assert.equal(mulai.badan.lab, true);
		assert.ok(baru.indexOf(sapu[0]) < baru.indexOf(mulai), 'sapuan mendahului masuk');
		assert.equal(tB.lab, true);
		assert.deepEqual(b3.pengaturan, { 'window.restoreWindows': 'none', 'files.hotExit': 'off' });
	});
	await sampai(() => dari('/api/app/logout', `Bearer ${tB.token}`).length === 1 && !adaDevice(), 40_000, `penjaga tidak membersihkan setelah aplikasi ditutup: ${JSON.stringify(penjaga().slice(-6))}`);
	await sampai(() => penjaga().filter((p) => p.peristiwa === 'selesai').length >= 1, 30_000, 'penjaga tidak selesai');
	periksa('mode lab, aplikasi ditutup biasa selagi masuk: penjaga sesi mencabut token + perangkat di server dan menghapus device.json', () => {
		assert.equal(token.get(tB.token).berlaku, false);
		assert.equal(perangkat.get(tB.deviceId).dicabut, true);
		assert.equal(adaDevice(), false);
		assert.deepEqual(penjaga().filter((p) => p.peristiwa === 'bersihkan').map((p) => p.alasan), ['aplikasi_ditutup']);
		assert.ok(!fs.readFileSync(catatanPenjaga, 'utf8').includes(tB.token));
	});
	const n4 = permintaan.length;
	const b4 = await tahap('lab-2', { lab: true, folder: false, biasa: true });
	periksa('mode lab, aplikasi dibuka lagi (penyimpanan sungguhan): sesi TIDAK pulih — tidak ada satu pun permintaan ber-token, tidak ada jendela/folder yang dipulihkan, token tidak ada di profil aplikasi', () => {
		const baru = permintaan.slice(n4);
		assert.deepEqual(baru.filter((p) => p.otorisasi).map((p) => p.jalur), []);
		assert.equal(masuk.length, 2);
		assert.deepEqual(b4.folder, []);
		assert.deepEqual(b4.tabBerkas, [], `tab: ${b4.tab.join(', ')}`);
		assert.deepEqual(adaDiDisk(userData, tB.token), [], 'token sesi lab tertulis di profil aplikasi');
		assert.deepEqual(adaDiDisk(stateDir, tB.token), []);
		assert.equal(adaDevice(), false);
	});

	// Membuka folder mata kuliah memuat ulang jendela: sesi (yang tidak pernah ditulis ke disk)
	// harus tetap ada di jendela pengganti — diwarisi dari penjaga sesi.
	const n5 = permintaan.length;
	const penandaMuat = path.join(tmp, 'isyarat-lab-3.dimuat-ulang');
	const dipakaiSetelahMuat = () => fs.existsSync(penandaMuat) && permintaan.some((p) => p.jalur === '/api/auth/me' && masuk[2] && p.otorisasi === `Bearer ${masuk[2].token}` && p.pada > fs.statSync(penandaMuat).mtimeMs);
	const b5 = await tahap('lab-3', { lab: true, folder: false, biasa: true, aksi: 'masuk-buka-folder', siap: () => (fs.existsSync(penandaMuat) ? dipakaiSetelahMuat() : sudahMasuk(2)()) });
	const tC = masuk[2];
	periksa('mode lab, membuka folder (jendela dimuat ulang, extension host baru): sesi DIWARISI dari penjaga sesi tanpa masuk ulang dan tanpa menulis token ke disk', () => {
		assert.ok(tC, 'masuk tidak terjadi');
		assert.equal(b5.aktivasiKedua, true, 'hasil ditulis aktivasi kedua (setelah muat ulang)');
		assert.equal(b5.folder.length, 1);
		assert.equal(nj(fs.realpathSync.native(b5.folder[0])), nj(fs.realpathSync.native(wsCourse)), `folder: ${b5.folder}`);
		assert.equal(String(b5.ppid), fs.readFileSync(penandaMuat, 'utf8'), 'proses utama aplikasi sama sebelum dan sesudah muat ulang');
		const baru = permintaan.slice(n5);
		assert.equal(baru.filter((p) => p.jalur === '/api/app/login/start').length, 1, 'tidak ada masuk kedua');
		assert.ok(dipakaiSetelahMuat(), 'token dipakai lagi oleh jendela pengganti');
		assert.deepEqual(adaDiDisk(userData, tC.token), []);
		assert.ok(penjaga().filter((p) => p.peristiwa === 'jendela').some((p) => p.jumlah === 0), 'jendela lama lepas dari penjaga');
	});
	await sampai(() => dari('/api/app/logout', `Bearer ${tC.token}`).length === 1 && !adaDevice(), 40_000, `penjaga tidak membersihkan setelah aplikasi ditutup: ${JSON.stringify(penjaga().slice(-6))}`);
	await sampai(() => penjaga().filter((p) => p.peristiwa === 'selesai').length >= 2, 30_000, 'penjaga tidak selesai');
	periksa('… dan setelah aplikasi itu ditutup, penjaga mengakhiri sesi yang diwariskan tadi', () => {
		assert.equal(token.get(tC.token).berlaku, false);
		assert.equal(perangkat.get(tC.deviceId).dicabut, true);
	});

	// === Bagian 2: proses uji VS Code (kait uji ekstensi) ======================================
	// --- masuk: mode lab ---------------------------------------------------------------------
	// Sisa sesi sebelumnya di komputer ini: kredensial perangkat + tautan GitHub (tanpa token,
	// supaya uji tidak pernah menghubungi GitHub) — seperti setelah listrik padam.
	const adopt = spawnSync(PYTHON, [FIXTURE_AGENT, ws, catatanAgent, pemicu, '--state-dir', stateDir, '--url', SERVER, 'adopt'], {
		input: JSON.stringify({ deviceId: 'dev-lama', credential: RAHASIA_LAMA, name: 'PC Lab', os: os.type(), arch: os.arch(), account: USERNAME }),
		env: { ...process.env, ...envAgent },
		encoding: 'utf8',
	});
	assert.equal(adopt.status, 0, `adopt gagal: ${adopt.stderr}`);
	fs.writeFileSync(path.join(stateDir, 'github.json'), JSON.stringify({ account: { login: 'mahasiswa-lama' } }));
	const nMasuk = permintaan.length;
	const m = await tahap('masuk', { lab: true, folder: true });
	const awal = m.awal.keadaan;
	periksa('mode lab aktif dari DSW_LAB_CONFIG; tampil di bilah status, Beranda, dan view Status', () => {
		assert.deepEqual([awal.aktif, awal.sumber, awal.konfig.namaLab, awal.konfig.menganggurMenit], [true, 'berkas', 'Lab Uji', 5]);
		assert.equal(awal.teksBilah[0], '$(shield) Mode lab: Lab Uji');
		assert.match(awal.info.ringkas, /Mode lab aktif \(Lab Uji\) · keluar otomatis setelah 5 menit/);
		assert.deepEqual(m.beranda, { pita: true, tombolKeluar: true, lab: { nama: 'Lab Uji', menganggurMenit: 5 } });
		assert.deepEqual(m.statusView, ['lab', 'akun', 'agent', 'lingkungan', 'disk', 'folder', 'versi']);
		assert.equal(m.setelahMasuk.keadaan.teksBilah[1], '$(sign-out) Keluar');
	});
	periksa('mode lab: suara panel bawaan MATI; menyala hanya bila pengguna menulis dsworkbench.sound.enabled', () => {
		assert.deepEqual(m.suara, { bawaan: { aktif: false, volume: 0.35 }, eksplisit: { aktif: true, volume: 0.35 }, akhir: { aktif: false, volume: 0.35 } });
	});
	periksa('pengaturan pengguna mode lab ditetapkan: jendela tidak dipulihkan, hot exit mati, terminal tidak dihidupkan lagi', () => {
		assert.deepEqual(awal.pengaturan, { 'window.restoreWindows': 'none', 'files.hotExit': 'off', 'terminal.integrated.enablePersistentSessions': false });
	});
	periksa('sapu sisa saat mulai: perangkat tertinggal dicabut di server dengan kredensialnya sendiri (tanpa token pengguna); device.json dan tautan GitHub-nya dihapus', () => {
		assert.deepEqual(awal.hasilMulai, { sesi: 'tidak_ada', rahasiaLama: 'tidak_ada', sapu: { jenis: 'dicabut', deviceId: 'dev-lama', akun: USERNAME, server: 'revoked', github: true } });
		const minta = permintaan.slice(nMasuk).filter((p) => p.jalur === '/api/agent/self-revoke');
		assert.equal(minta.length >= 1, true);
		assert.deepEqual(minta[0].badan, { deviceId: 'dev-lama', credential: RAHASIA_LAMA });
		assert.equal(minta[0].otorisasi, undefined, 'tanpa token pengguna');
		assert.ok(permintaan.indexOf(minta[0]) < permintaan.indexOf(permintaan.slice(nMasuk).find((p) => p.jalur === '/api/app/login/start')), 'sapuan mendahului masuk');
		assert.deepEqual([m.awal.tersimpan.rahasia, m.awal.tersimpan.akun], [false, false]);
		assert.equal(perangkat.get('dev-lama').dicabut, true);
		assert.equal(fs.existsSync(path.join(stateDir, 'github.json')), false);
	});
	const t1 = masuk[3];
	periksa('masuk di mode lab: login/start membawa lab:true; token dan akun TIDAK ada di SecretStorage maupun globalState', () => {
		const mulai = permintaan.slice(nMasuk).filter((p) => p.jalur === '/api/app/login/start');
		assert.equal(mulai.length, 1);
		assert.equal(mulai[0].badan.lab, true);
		assert.equal(mulai[0].otorisasi, undefined);
		const s = m.setelahMasuk;
		assert.equal(s.keadaan.masuk, true);
		assert.deepEqual([s.tersimpan.rahasia, s.tersimpan.akun], [false, false]);
		assert.ok(!s.tersimpan.kunci.some((k) => k === 'dsworkbench.akun'), s.tersimpan.kunci.join(', '));
		assert.equal(s.keadaan.serverTanpaLab, false);
		assert.equal(s.agent.keadaan, 'siap');
	});
	periksa('penjaga sesi berjalan untuk aplikasi ini (induk extension host = proses utama aplikasi)', () => {
		assert.equal(m.setelahMasuk.keadaan.punyaPenjaga, true, m.setelahMasuk.keadaan.tanpaPenjaga);
		const siap = penjaga().filter((p) => p.peristiwa === 'siap').pop();
		assert.ok(siap, 'penjaga mencatat siap');
		assert.equal(siap.pidUtama, m.setelahMasuk.keadaan.ppid);
	});
	periksa('menganggur: peringatan dengan hitung mundur di bilah status, lalu keluar otomatis', () => {
		assert.equal(m.fasePeringatan.fase, 'peringatan');
		assert.equal(m.fasePeringatan.masuk, true);
		assert.ok(m.fasePeringatan.detik >= 2.9, `peringatan setelah ${m.fasePeringatan.detik} dtk`);
		assert.match(m.fasePeringatan.bilah[1], /Keluar otomatis dalam \d+ detik — klik bila masih di sini/);
		assert.equal(m.menganggur.keadaan.masuk, false);
		assert.ok(m.menganggur.detik >= 4.5 && m.menganggur.detik < 50, `keluar setelah ${m.menganggur.detik} dtk`);
	});
	periksa('keluar karena menganggur: tautan GitHub dicabut lewat relay, token + perangkat dicabut di server, Local Runner berhenti, device.json dihapus', () => {
		const lap = Object.fromEntries(m.menganggur.keadaan.laporanKeluar.map((l) => [l.nama, l.hasil]));
		assert.deepEqual(lap, { simpan: 'ok', github: 'ok', server: 'ok', penjaga: 'ok', agent: 'ok', sesi: 'ok', sapu: 'ok', jejak: 'ok', editor: 'ok', folder: 'ok' }, JSON.stringify(m.menganggur.keadaan.laporanKeluar));
		const urut = permintaan.map((p) => `${p.metode} ${p.jalur}`);
		const kirim = permintaan.slice(nMasuk).filter((p) => p.jalur === '/api/relay/dispatch');
		assert.deepEqual(kirim.map((p) => [p.badan.operation, p.badan.deviceId, p.otorisasi]), [['github.auth_revoke', t1.deviceId, `Bearer ${t1.token}`]]);
		const keluar = dari('/api/app/logout', `Bearer ${t1.token}`);
		assert.equal(keluar.length, 1);
		assert.ok(urut.indexOf('POST /api/relay/dispatch') < permintaan.indexOf(keluar[0]), 'GitHub dicabut sebelum token dicabut');
		assert.equal(token.get(t1.token).berlaku, false);
		assert.equal(perangkat.get(t1.deviceId).dicabut, true);
		assert.equal(adaDevice(), false);
		assert.notEqual(m.menganggur.agent.keadaan, 'siap');
		const agent = JSON.parse(fs.readFileSync(catatanAgent, 'utf8'));
		assert.ok(agent.some((x) => x.jenis === 'disconnect'), 'Local Runner pamit');
	});
	periksa('keluar karena menganggur: berkas folder kerja disimpan otomatis, dokumen tanpa nama diselamatkan ke folder kerja, semua editor tertutup, folder kerja ditandai ditutup', () => {
		assert.equal(m.sebelumMenganggur.kotor, true);
		assert.ok(m.sebelumMenganggur.tab >= 2);
		assert.equal(fs.readFileSync(path.join(wsCourse, 'catatan-lab.txt'), 'utf8'), 'BELUM-DISIMPAN isi awal\n');
		const selamat = fs.readdirSync(wsCourse).filter((n) => n.startsWith('_belum-disimpan-'));
		assert.equal(selamat.length, 1, fs.readdirSync(wsCourse).join(', '));
		assert.deepEqual(fs.readdirSync(path.join(wsCourse, selamat[0])), ['tanpa-nama-1.py']);
		assert.equal(fs.readFileSync(path.join(wsCourse, selamat[0], 'tanpa-nama-1.py'), 'utf8'), 'print("isi tanpa nama")\n');
		assert.equal(m.menganggur.tab, 0, 'semua editor tertutup');
		assert.equal(m.menganggur.terminal, 0);
		// Menutup folder memuat ulang jendela dan itu mengakhiri proses uji; di dalam uji hanya ditandai.
		assert.equal(m.menganggur.keadaan.folderAkanDitutup, true);
		assert.equal(fs.existsSync(path.join(wsCourse, 'catatan-lab.txt')), true, 'folder kerja mahasiswa TIDAK dihapus');
	});
	periksa('setelah keluar tidak ada jejak pengguna di penyimpanan ekstensi, dan token tidak ada di berkas mana pun di profil aplikasi', () => {
		const s = m.menganggur.tersimpan;
		assert.deepEqual([s.rahasia, s.akun], [false, false]);
		const jejak = s.kunci.filter((k) => k.startsWith('dsworkbench.') && !k.startsWith('dsworkbench.update.') && !k.startsWith('dsworkbench.lab.') && !['dsworkbench.temaBawaanDiterapkan', 'dsworkbench.sambutanDitampilkan'].includes(k));
		assert.deepEqual(jejak, []);
		// Tolak-bawaan sepenuhnya (0.1.7): yang tersisa HANYA kunci milik komputer, apa pun awalannya.
		const milikKomputer = (k) => k.startsWith('dsworkbench.update.') || k.startsWith('dsworkbench.lab.') || ['dsworkbench.temaBawaanDiterapkan', 'dsworkbench.sambutanDitampilkan'].includes(k);
		assert.deepEqual(s.kunci.filter((k) => !milikKomputer(k)), [], 'globalState: tidak ada kunci selain milik komputer');
		assert.deepEqual((s.kunciRuang ?? []).filter((k) => !milikKomputer(k)), [], 'workspaceState: tidak ada kunci selain milik komputer');
		assert.deepEqual(adaDiDisk(userData, t1.token), [], 'token sesi lab tertulis di profil aplikasi');
		assert.deepEqual(adaDiDisk(stateDir, t1.token), []);
		assert.ok(!fs.readFileSync(catatanPenjaga, 'utf8').includes(t1.token));
		assert.ok(penjaga().some((p) => p.peristiwa === 'dilupakan' && p.alasan === 'menganggur'), 'penjaga melupakan token saat jendela keluar');
	});

	if (m.jejakKini) {
		periksa('celah pembersihan mode lab tertutup: pilihan server SQL dan NILAI variabel psql (kunci `dsw.sql.*` lama maupun `dsworkbench.sql.*`), pilihan panel Pergudangan Data, dan kunci berawalan lain ikut terhapus saat keluar — di globalState dan workspaceState', () => {
			const j = m.jejakKini;
			// Sebelum keluar jejak itu memang ada di kedua penyimpanan…
			for (const k of j.ditanam) assert.ok(j.global.includes(k) && j.ruang.includes(k), `${k} tertanam`);
			// …dan sesudahnya tidak satu pun tersisa.
			const s = m.menganggur.tersimpan;
			for (const k of j.ditanam) assert.ok(!s.kunci.includes(k) && !s.kunciRuang.includes(k), `${k} masih ada setelah keluar`);
			assert.ok(!s.kunciRuang.includes('dsworkbench.lab.sesiRuang'), 'penanda sesi folder kerja ikut dibuang setelah jejaknya bersih');
			assert.deepEqual(adaDiDisk(userData, 'JEJAK-PENGGUNA'), [], 'nilai jejak tidak tertinggal di berkas profil aplikasi');
		});
		periksa('jejak workspaceState milik sesi lab LAIN di folder kerja yang sama dibuang begitu sesi baru mulai (folder itu tidak terbuka saat pemiliknya keluar)', () => {
			const l = m.jejakLama;
			for (const k of l.ditanam) assert.ok(l.sebelum.includes(k), `${k} tertanam sebelum masuk`);
			for (const k of l.ditanam) assert.ok(!l.sesudahMasuk.includes(k), `${k} milik sesi lain masih terbaca setelah masuk`);
			assert.ok(l.sesudahMasuk.includes('dsworkbench.lab.sesiRuang'), 'folder kerja ditandai dengan sesi yang sekarang');
		});
	} else {
		dilewati.push('pembersihan kunci SQL: ekstensi yang diuji belum punya kait uji (versi lama)');
	}

	// --- paksa: aplikasi dimatikan paksa ---------------------------------------------------------
	const p = await tahap('paksa', { lab: true, folder: true, bunuhSetelahHasil: true });
	const t3 = masuk[4];
	periksa('penutupan paksa (SIGKILL proses utama, tanpa deactivate): saat itu sesi sedang masuk dan device.json ada', () => {
		assert.equal(p.setelahMasuk.keadaan.masuk, true);
		assert.equal(p.setelahMasuk.keadaan.punyaPenjaga, true);
		assert.deepEqual([p.setelahMasuk.tersimpan.rahasia, p.setelahMasuk.tersimpan.akun], [false, false]);
	});
	await sampai(() => dari('/api/app/logout', `Bearer ${t3.token}`).length === 1 && !adaDevice(), 30_000, `penjaga tidak membersihkan setelah aplikasi dimatikan paksa: ${JSON.stringify(penjaga().slice(-6))}`);
	await sampai(() => penjaga().filter((x) => x.peristiwa === 'selesai').length >= 3, 30_000, 'penjaga tidak selesai setelah penutupan paksa');
	periksa('… penjaga sesi tetap mengakhiri sesi: token + perangkat dicabut di server, device.json dihapus, token tidak tertinggal di disk', () => {
		assert.equal(token.get(t3.token).berlaku, false);
		assert.equal(perangkat.get(t3.deviceId).dicabut, true);
		assert.equal(adaDevice(), false);
		assert.deepEqual(adaDiDisk(userData, t3.token), []);
		assert.deepEqual(adaDiDisk(stateDir, t3.token), []);
		assert.deepEqual(adaDiDisk(tmp, t3.token).filter((f) => !f.startsWith('hasil-')), []);
	});
	// --- server lama: belum punya rute pencabutan pendahulu --------------------------------------
	// Laptop pribadi lagi (tanpa mode-lab.json), dengan pasangan aplikasi lama di komputer ini.
	dukungGantikan = false;
	pasangLama('dev-pairing-2');
	const nLama = permintaan.length;
	const iLama = masuk.length;
	await tahap('server-lama', { lab: false, folder: false, biasa: true, aksi: 'masuk', siap: () => sudahMasuk(iLama)() && permintaan.slice(nLama).some((x) => x.jalur === '/api/devices') });
	const tL = masuk[iLama];
	periksa('server lama (tanpa /api/agent/supersede): masuk tetap berhasil, perangkat lama tidak tersentuh, dan ekstensi kembali ke pengingat — daftar perangkat diperiksa dengan token aplikasi', () => {
		assert.ok(tL, 'masuk tidak terjadi');
		const baru = permintaan.slice(nLama);
		const ganti = baru.filter((x) => x.jalur === '/api/agent/supersede');
		assert.equal(ganti.length, 1, 'dicoba sekali, dijawab 404');
		assert.equal(perangkat.get('dev-pairing-2').dicabut, false);
		assert.equal(perangkat.get(tL.deviceId).dicabut, false);
		assert.equal(idDevice(), tL.deviceId);
		const daftar = baru.filter((x) => x.jalur === '/api/devices');
		assert.ok(daftar.length >= 1, 'pengingat memeriksa daftar perangkat');
		assert.equal(daftar[0].otorisasi, `Bearer ${tL.token}`);
		assert.ok(baru.indexOf(daftar[0]) > baru.indexOf(ganti[0]));
	});
	// === Penyimpanan rahasia hanya di memori (seperti aplikasi yang tidak dapat memakai Keychain/keyring) ===
	// Profil baru + `--use-inmemory-secretstorage`: VS Code menyimpan rahasia per jendela di memori, persis
	// keadaan "An OS keyring couldn't be identified…". Tanda masuk hilang tiap aplikasi/jendela dimuat ulang.
	if (b1.rahasia) {
		dukungGantikan = true;
		const MEMORI = ['--use-inmemory-secretstorage'];
		const iM = masuk.length;
		const m1 = await tahap('memori-1', { lab: false, folder: false, biasa: true, aksi: 'masuk', siap: sudahMasuk(iM), profil: profilMemori, argTambahan: MEMORI });
		const tM = masuk[iM];
		const nM = permintaan.length;
		const mulaiM = Date.now();
		const m2 = await tahap('memori-2', { lab: false, folder: false, biasa: true, aksi: 'lihat-muat-ulang', siap: () => Date.now() - mulaiM > 7_000, profil: profilMemori, argTambahan: MEMORI });
		periksa('tanda masuk tidak tersimpan (rahasia hanya di memori): dikenali pada peluncuran berikutnya, dijelaskan SEKALI per sesi aplikasi (tidak diulang setelah jendela dimuat ulang), tanpa alarm saat masuk pertama', () => {
			assert.ok(tM, 'masuk tidak terjadi');
			// Masuk pertama di profil baru: belum ada penanda → tidak ada alarm.
			assert.deepEqual([m1.rahasia.putusan, m1.rahasia.tidakTersimpan, m1.rahasia.dijelaskan], ['belum_tahu', false, false]);
			// Aplikasi dibuka lagi: token tidak ada (tidak satu pun permintaan ber-token)…
			assert.equal(permintaan.slice(nM).filter((x) => x.otorisasi === `Bearer ${tM.token}`).length, 0, 'sesi tidak pulih');
			assert.equal(masuk.length, iM + 1, 'tidak ada masuk baru');
			// …penanda dari peluncuran sebelumnya ada, rahasia ujinya hilang → "tidak tersimpan", dijelaskan.
			assert.deepEqual([m2.pertama.rahasia.putusan, m2.pertama.rahasia.tidakTersimpan, m2.pertama.rahasia.dijelaskan], ['tidak_tersimpan', true, true]);
			// Jendela dimuat ulang (extension host baru, proses utama yang sama): tetap dikenali, TIDAK dijelaskan lagi.
			assert.equal(m2.aktivasiKedua, true);
			assert.equal(m2.ppid, m2.pertama.ppid, 'induk extension host = proses utama aplikasi yang sama');
			assert.deepEqual([m2.rahasia.putusan, m2.rahasia.tidakTersimpan, m2.rahasia.dijelaskan], ['tidak_tersimpan', true, false]);
			assert.deepEqual(adaDiDisk(profilMemori, tM.token), [], 'token tidak tertulis di profil aplikasi');
		});
	}
	periksa('semua permintaan ber-token memakai bearer tanpa cookie; kredensial perangkat hanya dikirim ke rute agent', () => {
		for (const x of permintaan) {
			assert.equal(x.cookie, undefined);
			const teks = JSON.stringify(x.badan ?? null);
			if (!x.jalur.startsWith('/api/agent/')) assert.ok(!teks.includes('kredensial-'), x.jalur);
		}
	});

	for (const c of cek) console.log(`  ✔ ${c}`);
	for (const d of dilewati) console.log(`  – dilewati: ${d}`);
	console.log(`\nUji integrasi mode lab lulus: ${cek.length} pemeriksaan (${m.app} ${m.vscode}).`);
	ringkasan = { lulus: true, vscode: m.vscode, app: m.app };
	kode = 0;
} catch (e) {
	for (const c of cek) console.log(`  ✔ ${c}`);
	console.error(`\nUji integrasi mode lab GAGAL: ${e?.stack || e}`);
	if (process.env.DSW_LAB_RINCI) {
		console.error(permintaan.map((x) => `${((x.pada - permintaan[0].pada) / 1000).toFixed(1)} ${x.metode} ${x.jalur} ${x.otorisasi ? (token.get(x.otorisasi.slice(7))?.deviceId ?? 'token-tak-dikenal') : '-'}`).join('\n'));
		console.error(JSON.stringify({ hasilTahap, penjaga: penjaga() }, null, 1).slice(0, 12000));
	}
	ringkasan = { lulus: false, galat: String(e?.message || e).slice(0, 4000) };
} finally {
	if (process.env.DSW_RINGKASAN_LAB) {
		fs.writeFileSync(process.env.DSW_RINGKASAN_LAB, JSON.stringify({ ...ringkasan, platform: process.platform, arch: process.arch, aplikasi: KODE, jumlahLulus: cek.length, pemeriksaan: cek, dilewati }, null, 1));
	}
	if (process.env.DSW_SIMPAN_HASIL_LAB) fs.writeFileSync(process.env.DSW_SIMPAN_HASIL_LAB, JSON.stringify({ permintaan: permintaan.map((x) => ({ ...x, otorisasi: x.otorisasi ? 'Bearer …' : undefined })), penjaga: penjaga() }, null, 1));
	server.closeAllConnections();
	server.close();
	// Penjaga yang masih hidup (uji gagal di tengah) berhenti sendiri; beri waktu sebentar.
	await tidur(500);
	fs.rmSync(tmp, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
}
process.exit(kode);
