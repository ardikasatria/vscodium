// Uji asap DSWorkbench: jalankan suite integrasi pada APLIKASI TERPASANG.
//
//   node uji-asap.mjs --app <aplikasi> [--ext <folder ekstensi>] [--python <python>]
//                     [--keluar <folder hasil>] [--tmp <folder sementara>]
//                     [--simpanan-rahasia os|memori] [--arg <argumen aplikasi>]...
//                     [--sumber <resources/app>] [--cetak-ekstensi ya]
//
//   --app   biner aplikasi, bundel `.app` (macOS), atau folder pasang yang berisi
//           `resources/app` (Windows/Linux, termasuk hasil ekstraksi AppImage)
//   --ext   ekstensi yang diuji; bawaan: yang TERTANAM di aplikasi
//           (`resources/app/extensions/dsworkbench`). Local Runner diambil dari
//           `agent-payload/` folder itu.
//   --sumber  folder `resources/app` bila tidak dapat diturunkan dari --app (mis.
//           --app menunjuk `AppRun` hasil ekstraksi AppImage)
//   --cetak-ekstensi ya  hanya mencetak folder ekstensi tertanam, lalu keluar
//   --python  interpreter untuk Local Runner dan kernel; cukup pustaka standar
//             (bawaan: python3, atau python di Windows)
//   --simpanan-rahasia memori  → `--use-inmemory-secretstorage` (runner tanpa
//             keyring); bawaan `os`
//
// Menulis `<keluar>/ringkasan-uji-asap.json` + `uji-asap.log`; keluar 0 hanya
// bila semua pemeriksaan lulus. Berkas ini berjalan di luar repo: hanya butuh
// isi folder bundel ini dan Node >= 20.
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

const sini = path.dirname(fileURLToPath(import.meta.url));

function gagal(pesan, kode = 2) {
	console.error(`uji-asap: ${pesan}`);
	process.exit(kode);
}

const o = { arg: [] };
const OPSI = ['app', 'ext', 'python', 'keluar', 'tmp', 'simpanan-rahasia', 'arg', 'sumber', 'cetak-ekstensi'];
{
	const a = process.argv.slice(2);
	for (let i = 0; i < a.length; i++) {
		const k = a[i];
		if (!k.startsWith('--')) gagal(`argumen tidak dikenal: ${k}`);
		const v = a[++i];
		if (v === undefined) gagal(`${k} butuh nilai`);
		if (k === '--arg') o.arg.push(v);
		else o[k.slice(2)] = v;
	}
}
for (const k of Object.keys(o)) if (!OPSI.includes(k)) gagal(`opsi tidak dikenal: --${k}`);
if (!o.app) gagal('wajib: --app <biner, .app, atau folder pasang aplikasi>');

const bacaJson = (j) => JSON.parse(fs.readFileSync(j, 'utf8'));
const sha256 = (j) => createHash('sha256').update(fs.readFileSync(j)).digest('hex');

/** Temukan biner dan `resources/app` dari apa pun yang diberikan lewat --app. */
function temukanAplikasi(masukan, sumber) {
	const p = path.resolve(masukan);
	if (!fs.existsSync(p)) gagal(`aplikasi tidak ada: ${p}`);
	const calon = [];
	if (sumber) {
		if (fs.statSync(p).isDirectory()) gagal('--sumber dipakai bersama --app yang menunjuk berkas biner');
		calon.push({ sumber: path.resolve(sumber), binDir: path.dirname(p), mac: false, biner: p });
	} else if (fs.statSync(p).isDirectory()) {
		calon.push({ sumber: path.join(p, 'Contents', 'Resources', 'app'), binDir: path.join(p, 'Contents', 'MacOS'), mac: true });
		calon.push({ sumber: path.join(p, 'resources', 'app'), binDir: p, mac: false });
	} else {
		const d = path.dirname(p);
		calon.push({ sumber: path.join(d, '..', 'Resources', 'app'), binDir: d, mac: true, biner: p });
		calon.push({ sumber: path.join(d, 'resources', 'app'), binDir: d, mac: false, biner: p });
	}
	for (const c of calon) {
		const produkJson = path.join(c.sumber, 'product.json');
		if (!fs.existsSync(produkJson)) continue;
		const produk = bacaJson(produkJson);
		let biner = c.biner;
		if (!biner) {
			const nama = c.mac ? [produk.nameShort, produk.applicationName] : process.platform === 'win32' ? [`${produk.nameShort}.exe`, `${produk.applicationName}.exe`] : [produk.applicationName, produk.nameShort];
			biner = nama.map((n) => path.join(c.binDir, String(n))).find((j) => fs.existsSync(j));
			if (!biner) gagal(`biner aplikasi tidak ditemukan di ${c.binDir} (dicari: ${nama.join(', ')})`);
		}
		return { biner: path.resolve(biner), sumber: path.resolve(c.sumber), produk };
	}
	gagal(`product.json tidak ditemukan di sekitar ${p} (bukan aplikasi DSWorkbench/VS Code?)`);
}

const app = temukanAplikasi(o.app, o.sumber);
const tertanam = path.join(app.sumber, 'extensions', 'dsworkbench');
if (o['cetak-ekstensi']) {
	if (!fs.existsSync(path.join(tertanam, 'package.json'))) gagal(`ekstensi tertanam tidak ada di ${tertanam}`);
	console.log(tertanam);
	process.exit(0);
}
const ext = path.resolve(o.ext ?? tertanam);
for (const wajib of ['package.json', path.join('dist', 'extension.js'), path.join('agent-payload', 'src', 'workbench_agent', 'cli.py')]) {
	if (!fs.existsSync(path.join(ext, wajib))) gagal(`${wajib} tidak ada di ekstensi yang diuji (${ext})`);
}
const manifesBundel = bacaJson(path.join(sini, 'MANIFEST.json'));
const pkgExt = bacaJson(path.join(ext, 'package.json'));
const sidikExt = sha256(path.join(ext, 'dist', 'extension.js'));
const ekstensi = {
	folder: ext,
	sumber: o.ext ? 'pilihan (--ext)' : 'tertanam di aplikasi',
	versi: pkgExt.version,
	sha256: sidikExt,
	// Suite di bundel ini dibangun bersama satu build ekstensi; build lain bisa belum punya fitur yang diperiksa.
	cocokDenganBundel: sidikExt === manifesBundel.ekstensi.sha256,
	tertanam: fs.existsSync(path.join(tertanam, 'package.json')) ? { versi: bacaJson(path.join(tertanam, 'package.json')).version, sha256: fs.existsSync(path.join(tertanam, 'dist', 'extension.js')) ? sha256(path.join(tertanam, 'dist', 'extension.js')) : null } : null,
};

const python = o.python ?? (process.platform === 'win32' ? 'python' : 'python3');
const versiPy = spawnSync(python, ['-c', 'import sys, platform; print(sys.version.split()[0], platform.machine(), sys.executable)'], { encoding: 'utf8' });
if (versiPy.status !== 0) gagal(`Python tidak dapat dijalankan (${python}): ${versiPy.stderr || versiPy.error?.message}`);

const keluar = path.resolve(o.keluar ?? path.join(process.cwd(), 'hasil-uji-asap'));
fs.mkdirSync(keluar, { recursive: true });
const berkasRingkasan = path.join(keluar, 'ringkasan-uji-asap.json');
const berkasInti = path.join(keluar, 'ringkasan-inti.json');
const berkasLog = path.join(keluar, 'uji-asap.log');
fs.rmSync(berkasInti, { force: true });

const argAplikasi = [...o.arg];
if ((o['simpanan-rahasia'] ?? 'os') === 'memori') argAplikasi.push('--use-inmemory-secretstorage');
else if (o['simpanan-rahasia'] && o['simpanan-rahasia'] !== 'os') gagal('--simpanan-rahasia harus "os" atau "memori"');

const env = {
	...process.env,
	DSW_VSCODE: app.biner,
	DSW_EXT_PATH: ext,
	DSW_AGENT_PAYLOAD: '1',
	DSW_PYTHON: python,
	DSW_SUITE: path.join(sini, 'suite.cjs'),
	DSW_FIXTURE_AGENT: path.join(sini, 'fixtures', 'agen_sungguhan.py'),
	DSW_OPS_JSON: path.join(sini, 'ide-local-ops.json'),
	DSW_RINGKASAN: berkasInti,
	DSW_PRODUK_JSON: path.join(app.sumber, 'product.json'),
	DSW_SIMPAN_HASIL: path.join(keluar, 'hasil-suite.json'),
	DSW_ARG_APLIKASI: JSON.stringify(argAplikasi),
	// Jangan mewarisi pengaturan pengembang yang mengubah perilaku aplikasi.
	ELECTRON_RUN_AS_NODE: undefined,
	VSCODE_IPC_HOOK: undefined,
};
if (o.tmp) {
	// Jalur sementara pendek dan tanpa nama 8.3 (soket IPC dibatasi ±100 karakter).
	const tmp = path.resolve(o.tmp);
	if (process.platform !== 'win32' && tmp.length > 60) gagal(`--tmp terlalu panjang (${tmp.length} karakter): soket IPC aplikasi dibatasi ±100 karakter; pakai jalur pendek`);
	fs.mkdirSync(tmp, { recursive: true });
	Object.assign(env, { TMPDIR: tmp, TMP: tmp, TEMP: tmp });
}
for (const k of Object.keys(env)) if (env[k] === undefined) delete env[k];

console.log(`aplikasi : ${app.produk.nameLong} ${app.produk.version} (commit ${String(app.produk.commit).slice(0, 10)}) — ${app.biner}`);
console.log(`ekstensi : ${ekstensi.versi} — ${ekstensi.sumber}${ekstensi.cocokDenganBundel ? '' : '  [build BERBEDA dari yang dipakai membangun suite ini]'}`);
console.log(`python   : ${versiPy.stdout.trim()}`);
console.log(`bundel   : dibuat ${manifesBundel.dibuat}, suite untuk ekstensi ${manifesBundel.ekstensi.versi}\n`);

const mulai = Date.now();
const log = fs.createWriteStream(berkasLog);
const kode = await new Promise((selesai) => {
	const anak = spawn(process.execPath, [path.join(sini, 'inti.mjs')], { env, stdio: ['ignore', 'pipe', 'pipe'] });
	for (const aliran of [anak.stdout, anak.stderr]) {
		aliran.on('data', (d) => {
			process.stdout.write(d);
			log.write(d);
		});
	}
	const pewaktu = setTimeout(() => anak.kill(), 420_000);
	anak.on('error', (e) => {
		clearTimeout(pewaktu);
		log.write(`\n${e.stack}\n`);
		selesai(2);
	});
	anak.on('close', (k) => {
		clearTimeout(pewaktu);
		selesai(k ?? 1);
	});
});
await new Promise((r) => log.end(r));

const inti = fs.existsSync(berkasInti) ? bacaJson(berkasInti) : { lulus: false, galat: 'inti tidak menulis ringkasan (aplikasi tidak menyala?)' };
const ringkasan = {
	lulus: kode === 0 && inti.lulus === true,
	kodeKeluar: kode,
	detik: Math.round((Date.now() - mulai) / 100) / 10,
	os: { platform: process.platform, arch: process.arch, rilis: os.release(), versi: os.version?.() },
	aplikasi: { nama: app.produk.nameLong, versi: app.produk.version, commit: app.produk.commit, biner: app.biner, argumen: argAplikasi },
	ekstensi,
	python: versiPy.stdout.trim(),
	bundel: { dibuat: manifesBundel.dibuat, ekstensi: manifesBundel.ekstensi },
	vscode: inti.vscode,
	agent: inti.agent,
	jumlahLulus: inti.jumlahLulus ?? 0,
	pemeriksaan: inti.pemeriksaan ?? [],
	dilewati: inti.dilewati ?? [],
	peringatan: inti.peringatan ?? [],
	// Nilai bawaan `workbench.colorTheme` yang dilihat ekstensi ("DSWorkbench Gelap" bila tema bawaan produk bekerja).
	temaBawaan: inti.temaBawaanProduk,
	galat: inti.galat ?? inti.galatSuite,
	galatSuite: inti.galatSuite,
};
fs.writeFileSync(berkasRingkasan, JSON.stringify(ringkasan, null, 1));
fs.rmSync(berkasInti, { force: true });
for (const w of ringkasan.peringatan) console.log(`PERINGATAN: ${w}`);
console.log(`\n${ringkasan.lulus ? 'LULUS' : 'GAGAL'}: ${ringkasan.jumlahLulus} pemeriksaan lulus — ${berkasRingkasan}`);
if (!ringkasan.lulus && !ekstensi.cocokDenganBundel) {
	console.log('Catatan: ekstensi yang diuji bukan build yang dipakai membangun suite ini; kegagalan bisa berasal dari fitur yang belum ada di build itu.');
}
process.exit(ringkasan.lulus ? 0 : kode || 1);
