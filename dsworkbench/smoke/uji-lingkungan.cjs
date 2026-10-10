"use strict";
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));

// smoke/uji-lingkungan.ts
var import_node_child_process4 = require("node:child_process");
var fs8 = __toESM(require("node:fs"));
var path7 = __toESM(require("node:path"));

// extension/src/agent/locate.ts
var fs = __toESM(require("node:fs"));
var os = __toESM(require("node:os"));
var path = __toESM(require("node:path"));
var NAMA_PRODUK = "DSWorkbench";
var PENANDA_PINDAH = "WORKBENCH_AGENT_INTERPRETER_TETAP";
var dataRootPaksa;
function lingkunganNyata() {
  return {
    platform: process.platform,
    env: dataRootPaksa ? { ...process.env, DSW_DATA_ROOT: dataRootPaksa } : process.env,
    rumah: os.homedir(),
    ada: (j) => {
      try {
        return fs.statSync(j).isFile();
      } catch {
        return false;
      }
    },
    daftar: (j) => {
      try {
        return fs.readdirSync(j);
      } catch {
        return [];
      }
    }
  };
}
function dataRootBawaan(l) {
  const p = l.platform === "win32" ? path.win32 : path.posix;
  const timpa = (l.env["DSW_DATA_ROOT"] ?? "").trim();
  if (timpa) return timpa === "~" || /^~[\\/]/.test(timpa) ? p.join(l.rumah, timpa.slice(1)) : timpa;
  if (l.platform === "win32") {
    const dasar = l.env["LOCALAPPDATA"] || p.join(l.rumah, "AppData", "Local");
    return p.join(dasar, NAMA_PRODUK);
  }
  if (l.platform === "darwin") {
    return p.join(l.rumah, "Library", "Application Support", NAMA_PRODUK);
  }
  const xdg = (l.env["XDG_DATA_HOME"] ?? "").trim();
  return p.join(xdg || p.join(l.rumah, ".local", "share"), NAMA_PRODUK);
}
var FOLDER_PAYLOAD = "agent-payload";
var VAR_AKAR_LINGKUNGAN = "WORKBENCH_AGENT_ENV_ROOT";
function payloadBawaan(akarEkstensi, l = lingkunganNyata()) {
  if (!akarEkstensi) return void 0;
  const p = l.platform === "win32" ? path.win32 : path.posix;
  const akar = p.join(akarEkstensi, FOLDER_PAYLOAD);
  return l.ada(p.join(akar, "src", "workbench_agent", "cli.py")) ? akar : void 0;
}
function envDasar(l, pythonPath) {
  const env = { ...l.env };
  delete env["PYTHONHOME"];
  env["PYTHONPATH"] = pythonPath;
  env["PYTHONUNBUFFERED"] = "1";
  env["PYTHONIOENCODING"] = "utf-8";
  env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1";
  env[PENANDA_PINDAH] = "1";
  return env;
}
function peluncurBawaan(atur, python, profilBawaan = true, l = lingkunganNyata()) {
  const payload = payloadBawaan(atur.akarEkstensi, l);
  if (!payload) return void 0;
  const p = l.platform === "win32" ? path.win32 : path.posix;
  const dataRoot = dataRootBawaan(l);
  const env = envDasar(l, p.join(payload, "src"));
  env["PYTHONDONTWRITEBYTECODE"] = "1";
  env[VAR_AKAR_LINGKUNGAN] = dataRoot;
  return {
    dasar: [python, "-m", "workbench_agent.cli"],
    env: { ...env, ...atur.env ?? {} },
    cwd: dataRoot,
    sumber: "bawaan",
    profilBawaan,
    serverUrl: atur.serverUrl,
    stateDir: (atur.stateDir ?? "").trim() || void 0
  };
}
function argumenGlobal(p) {
  return [...p.stateDir ? ["--state-dir", p.stateDir] : [], "--url", p.serverUrl];
}
function perintahSekali(p, ...sub) {
  return [...p.dasar, ...argumenGlobal(p), ...sub];
}

// extension/src/agent/oneshot.ts
var import_node_child_process = require("node:child_process");

// extension/src/agent/protocol.ts
var BATAS_BARIS = 1024 * 1024;

// extension/src/agent/oneshot.ts
function jalankanSekali(peluncur, sub, masukan, batasMs = 6e4) {
  const [program, ...argumen2] = perintahSekali(peluncur, ...sub);
  return new Promise((selesai, gagal) => {
    const anak = (0, import_node_child_process.spawn)(program, argumen2, {
      cwd: peluncur.cwd,
      env: peluncur.env,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    const pewaktu = setTimeout(() => anak.kill(), batasMs);
    anak.stdout.on("data", (d) => stdout += d.toString("utf8"));
    anak.stderr.on("data", (d) => stderr += d.toString("utf8"));
    anak.on("error", (e) => {
      clearTimeout(pewaktu);
      gagal(e);
    });
    anak.on("close", (kode) => {
      clearTimeout(pewaktu);
      selesai({ kode, stdout, stderr });
    });
    anak.stdin.on("error", () => void 0);
    anak.stdin.end(masukan ?? "");
  });
}

// extension/src/lingkungan/pasang.ts
var import_node_child_process3 = require("node:child_process");
var fs7 = __toESM(require("node:fs"));
var os2 = __toESM(require("node:os"));
var path6 = __toESM(require("node:path"));

// extension/src/lingkungan/folder.ts
var import_node_crypto = require("node:crypto");
var fs2 = __toESM(require("node:fs"));
var path2 = __toESM(require("node:path"));
var BERKAS_PENANDA_IDE = "ide.json";
var BERKAS_KUNCI_PASANG = "ide-pasang.lock";
var BERKAS_KUNCI_APLIKASI_LAMA = "dsworkbench.lock";
var PENGELOLA = "dsworkbench-ide";
var BERKAS_APLIKASI_LAMA = Object.freeze([
  "src",
  "vendor",
  "updates",
  "logs",
  "install.json",
  "selection.json",
  "settings.json",
  BERKAS_KUNCI_APLIKASI_LAMA
]);
function tataLetak(l = lingkunganNyata()) {
  const p = l.platform === "win32" ? path2.win32 : path2.posix;
  const akar = dataRootBawaan(l);
  const dirPy = p.join(akar, ".python");
  return {
    akar,
    requirements: p.join(akar, "requirements"),
    requirementsTxt: p.join(akar, "requirements.txt"),
    dirPythonAplikasi: dirPy,
    pythonAplikasi: l.platform === "win32" ? p.join(dirPy, "python.exe") : p.join(dirPy, "bin", "python"),
    penandaIde: p.join(akar, BERKAS_PENANDA_IDE),
    kunciPasang: p.join(akar, BERKAS_KUNCI_PASANG),
    kunciAplikasiLama: p.join(akar, BERKAS_KUNCI_APLIKASI_LAMA),
    penandaPayloadLama: p.join(akar, "src", ".dsw-payload.json")
  };
}
function bandingVersi(a, b) {
  const urai = (s) => (s.match(/\d+/g) ?? []).map((x2) => Number.parseInt(x2, 10));
  const x = urai(a);
  const y = urai(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] ?? 0) - (y[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
}
function termasukRequirements(rel) {
  return rel === "requirements.txt" || rel.startsWith("requirements/") && !rel.slice("requirements/".length).includes("/");
}
function rencanaSalin(payload, terpasang, versi) {
  const ditahan = versi.terpasang !== void 0 && bandingVersi(versi.terpasang, versi.payload) > 0;
  const butir = [];
  for (const rel of [...payload.keys()].filter(termasukRequirements).sort()) {
    const ada = terpasang.get(rel);
    if (ada === void 0) butir.push({ rel, aksi: "baru" });
    else if (ada === payload.get(rel)) butir.push({ rel, aksi: "sama" });
    else butir.push({ rel, aksi: ditahan ? "sama" : "perbarui" });
  }
  return { butir, ditahan, berubah: butir.some((b) => b.aksi !== "sama") };
}
var sha256 = (b) => (0, import_node_crypto.createHash)("sha256").update(b).digest("hex");
function bacaJson(jalur) {
  try {
    const d = JSON.parse(fs2.readFileSync(jalur, "utf8"));
    return d && typeof d === "object" && !Array.isArray(d) ? d : void 0;
  } catch {
    return void 0;
  }
}
function sidikRequirements(akar) {
  const hasil = /* @__PURE__ */ new Map();
  const tambah = (rel) => {
    const j = path2.join(akar, ...rel.split("/"));
    try {
      if (fs2.lstatSync(j).isFile()) hasil.set(rel, sha256(fs2.readFileSync(j)));
    } catch {
    }
  };
  tambah("requirements.txt");
  let isi = [];
  try {
    isi = fs2.readdirSync(path2.join(akar, "requirements"));
  } catch {
  }
  for (const n of isi.sort()) tambah(`requirements/${n}`);
  return hasil;
}
function bacaPenandaIde(akar) {
  const d = bacaJson(path2.join(akar, BERKAS_PENANDA_IDE));
  if (!d || d["pengelola"] !== PENGELOLA || typeof d["versiAgent"] !== "string") return void 0;
  return {
    schema: 1,
    pengelola: PENGELOLA,
    versiEkstensi: typeof d["versiEkstensi"] === "string" ? d["versiEkstensi"] : "",
    versiAgent: d["versiAgent"],
    diperbarui: typeof d["diperbarui"] === "string" ? d["diperbarui"] : ""
  };
}
function versiRequirementsTerpasang(akar) {
  const calon = [];
  const ide = bacaPenandaIde(akar);
  if (ide) calon.push(ide.versiAgent);
  const lama = bacaJson(path2.join(akar, "src", ".dsw-payload.json"));
  if (lama && typeof lama["version"] === "string") calon.push(lama["version"]);
  return calon.sort(bandingVersi).pop();
}
function versiPayload(akarPayload2) {
  const m = bacaJson(path2.join(akarPayload2, "MANIFEST.json"));
  return m && typeof m["agentVersion"] === "string" ? m["agentVersion"] : void 0;
}
function akarPayload(akarEkstensi) {
  return path2.join(akarEkstensi, FOLDER_PAYLOAD);
}
function rencanaUntuk(akarRuntime, payload) {
  return rencanaSalin(sidikRequirements(payload), sidikRequirements(akarRuntime), {
    payload: versiPayload(payload) ?? "0",
    terpasang: versiRequirementsTerpasang(akarRuntime)
  });
}
function akarRequirementsBerlaku(akarRuntime, payload) {
  const adaDiRuntime = fs2.existsSync(path2.join(akarRuntime, "requirements", "profiles.json"));
  if (!fs2.existsSync(path2.join(payload, "requirements", "profiles.json"))) return akarRuntime;
  return adaDiRuntime && rencanaUntuk(akarRuntime, payload).ditahan ? akarRuntime : payload;
}
function siapkanFolder(akarRuntime, payload, versiEkstensi, sekarang = () => /* @__PURE__ */ new Date()) {
  if (!fs2.existsSync(path2.join(payload, "requirements", "profiles.json"))) {
    throw new Error(`Payload Local Runner tidak lengkap (${path2.join(payload, "requirements", "profiles.json")} tidak ada).`);
  }
  const dibuat = !fs2.existsSync(akarRuntime);
  fs2.mkdirSync(path2.join(akarRuntime, "requirements"), { recursive: true });
  const rencana = rencanaUntuk(akarRuntime, payload);
  const ditulis = [];
  for (const b of rencana.butir) {
    if (b.aksi === "sama") continue;
    const tujuan = path2.join(akarRuntime, ...b.rel.split("/"));
    const sementara = `${tujuan}.ide-baru`;
    fs2.writeFileSync(sementara, fs2.readFileSync(path2.join(payload, ...b.rel.split("/"))));
    fs2.renameSync(sementara, tujuan);
    ditulis.push(b.rel);
  }
  const lama = bacaPenandaIde(akarRuntime);
  const versiAgent = rencana.ditahan ? versiRequirementsTerpasang(akarRuntime) ?? "0" : versiPayload(payload) ?? "0";
  if (!lama || ditulis.length > 0 || lama.versiEkstensi !== versiEkstensi || lama.versiAgent !== versiAgent) {
    const penanda = {
      schema: 1,
      pengelola: PENGELOLA,
      versiEkstensi,
      versiAgent,
      diperbarui: sekarang().toISOString()
    };
    const tujuan = path2.join(akarRuntime, BERKAS_PENANDA_IDE);
    fs2.writeFileSync(`${tujuan}.ide-baru`, JSON.stringify(penanda, null, 2) + "\n");
    fs2.renameSync(`${tujuan}.ide-baru`, tujuan);
  }
  return { akar: akarRuntime, dibuat, rencana, ditulis };
}
function ruangDiskBebas(jalur) {
  let p = jalur;
  while (!fs2.existsSync(p) && path2.dirname(p) !== p) p = path2.dirname(p);
  try {
    const s = fs2.statfsSync(p);
    return Number(s.bavail) * Number(s.bsize);
  } catch {
    return void 0;
  }
}

// extension/src/lingkungan/kunci.ts
var fs3 = __toESM(require("node:fs"));
function pidHidup(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === "EPERM";
  }
}
function uraiIsiKunci(teks) {
  if (!teks) return void 0;
  try {
    const d = JSON.parse(teks);
    if (d && typeof d === "object" && Number.isInteger(d["pid"]) && d["pid"] > 0) {
      return {
        pid: d["pid"],
        waktu: typeof d["waktu"] === "string" ? d["waktu"] : "",
        profil: typeof d["profil"] === "string" ? d["profil"] : void 0
      };
    }
  } catch {
  }
  return void 0;
}
var BASI_TANPA_ISI_MS = 6e4;
function ambilKunci(jalur, opsi = {}) {
  const pid = opsi.pid ?? process.pid;
  const hidup = opsi.hidup ?? pidHidup;
  const sekarang = opsi.sekarang ?? Date.now;
  for (let coba = 0; coba < 2; coba++) {
    try {
      const fd = fs3.openSync(jalur, "wx");
      try {
        const isi = { pid, waktu: new Date(sekarang()).toISOString(), profil: opsi.profil };
        fs3.writeSync(fd, JSON.stringify(isi) + "\n");
      } finally {
        fs3.closeSync(fd);
      }
      return {
        ok: true,
        lepas: () => {
          try {
            if (uraiIsiKunci(fs3.readFileSync(jalur, "utf8"))?.pid === pid) fs3.rmSync(jalur, { force: true });
          } catch {
          }
        }
      };
    } catch (e) {
      if (e.code !== "EEXIST") throw e;
    }
    let pemegang;
    let umur = 0;
    try {
      pemegang = uraiIsiKunci(fs3.readFileSync(jalur, "utf8"));
      umur = sekarang() - fs3.statSync(jalur).mtimeMs;
    } catch {
      continue;
    }
    const basi = pemegang ? pemegang.pid !== pid && !hidup(pemegang.pid) : umur > BASI_TANPA_ISI_MS;
    if (!basi) return { ok: false, pemegang };
    fs3.rmSync(jalur, { force: true });
  }
  return { ok: false };
}

// extension/src/lingkungan/ambilAlih.ts
var PESAN_TUTUP_APLIKASI_LAMA = "Tutup aplikasi DSWorkbench lama; DSWorkbench yang baru menggantikannya.";
var PESAN_AGENT_LAIN = "Local Runner lain sedang berjalan di komputer ini (aplikasi DSWorkbench lama, agent ZIP, atau jendela DSWorkbench lain). " + PESAN_TUTUP_APLIKASI_LAMA;

// extension/src/lingkungan/profil.ts
var import_node_crypto2 = require("node:crypto");
var fs4 = __toESM(require("node:fs"));
var path3 = __toESM(require("node:path"));
var PENANDA_REQS = ".workbench-reqs.sha256";
var PROFIL_BAWAAN = "python-data-science";
var SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;
var DISK_MIN_GIB = 2;
var GiB = 1024 ** 3;
function uraiManifestProfil(teks) {
  let data;
  try {
    data = JSON.parse(teks);
  } catch {
    throw new Error("Manifest profil lingkungan (profiles.json) tidak dapat dibaca.");
  }
  const profil = data?.profiles;
  const hasil = [];
  if (profil && typeof profil === "object") {
    for (const [id, isi] of Object.entries(profil)) {
      if (!SLUG.test(id) || !isi || typeof isi !== "object") continue;
      const o = isi;
      const req = typeof o["requirements"] === "string" && o["requirements"] ? o["requirements"] : `${id}.txt`;
      if (req.includes("/") || req.includes("\\") || req === ".." || req === ".") continue;
      const est = o["estimate"] && typeof o["estimate"] === "object" ? o["estimate"] : {};
      const teksArr = (v) => Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
      hasil.push({
        id,
        nama: typeof o["name"] === "string" && o["name"] ? o["name"] : id,
        requirements: req,
        tataLetakLama: o["legacyLayout"] === true && id === PROFIL_BAWAAN,
        bawaan: id === PROFIL_BAWAAN,
        platformDidukung: teksArr(o["supportedPlatforms"]),
        layanan: teksArr(o["services"]),
        perkiraan: {
          unduhMiB: typeof est["downloadMiB"] === "number" ? est["downloadMiB"] : void 0,
          diskGiB: typeof est["diskGiB"] === "number" ? est["diskGiB"] : void 0,
          menit: typeof est["minutes"] === "string" ? est["minutes"] : void 0
        }
      });
    }
  }
  return hasil.sort((a, b) => Number(b.bawaan) - Number(a.bawaan) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}
function kunciPlatform(platform, arch) {
  const plat = platform.startsWith("linux") ? "linux" : platform;
  let mesin = arch.toLowerCase();
  if (mesin === "x64" || mesin === "amd64") mesin = plat === "win32" ? "amd64" : "x86_64";
  else if (mesin === "arm64" || mesin === "aarch64") mesin = plat === "linux" ? "aarch64" : "arm64";
  return `${plat}-${mesin}`;
}
function namaPlatform(kunci) {
  return {
    "darwin-x86_64": "macOS Intel",
    "darwin-arm64": "macOS Apple Silicon",
    "win32-amd64": "Windows 64-bit",
    "win32-arm64": "Windows ARM",
    "linux-x86_64": "Linux 64-bit",
    "linux-aarch64": "Linux ARM64"
  }[kunci] ?? kunci;
}
var bacaNyata = (j) => {
  try {
    return fs4.readFileSync(j);
  } catch {
    return void 0;
  }
};
function berkasRequirements(dir, nama, baca = bacaNyata) {
  const urutan = [];
  const kunjungi = (n) => {
    if (urutan.some((u) => u.nama === n)) return true;
    const isi = baca(path3.join(dir, n));
    if (!isi) return false;
    urutan.push({ nama: n, isi });
    for (const baris of isi.toString("utf8").split(/\r\n|\r|\n/)) {
      const mentah = (baris.split("#", 1)[0] ?? "").trim();
      for (const awalan of ["-r ", "--requirement ", "--requirement="]) {
        if (!mentah.startsWith(awalan)) continue;
        const rujukan = mentah.slice(awalan.length).trim();
        if (!rujukan || rujukan.includes("/") || rujukan.includes("\\") || rujukan === "..") return false;
        if (!kunjungi(rujukan)) return false;
      }
    }
    return true;
  };
  return kunjungi(nama) ? urutan : void 0;
}
function hashRequirements(dir, nama, baca = bacaNyata) {
  const berkas = berkasRequirements(dir, nama, baca);
  if (!berkas || berkas.length === 0) return void 0;
  if (berkas.length === 1) return (0, import_node_crypto2.createHash)("sha256").update(berkas[0].isi).digest("hex");
  const h = (0, import_node_crypto2.createHash)("sha256");
  for (const b of berkas) {
    h.update(Buffer.from(b.nama, "utf8"));
    h.update(Buffer.from([0]));
    h.update(b.isi);
    h.update(Buffer.from([0]));
  }
  return h.digest("hex");
}
function keadaanProfil(p, kunci, f) {
  const cocok = f.adaInterpreter && f.penanda !== void 0 && f.hash !== void 0 && f.penanda.trim() === f.hash;
  if (cocok) return "siap";
  if (p.platformDidukung.length > 0 && !p.platformDidukung.includes(kunci)) return "tidak_didukung";
  if (!f.adaInterpreter || f.penanda === void 0) return "belum_dipasang";
  return "perlu_diperbarui";
}
function pythonDi(venv, win) {
  return win ? path3.join(venv, "Scripts", "python.exe") : path3.join(venv, "bin", "python");
}
var adaBerkas = (j) => {
  try {
    return fs4.statSync(j).isFile();
  } catch {
    return false;
  }
};
var adaDir = (j) => {
  try {
    return fs4.statSync(j).isDirectory();
  } catch {
    return false;
  }
};
function jalurPythonProfil(akar, p, win) {
  if (!p.tataLetakLama) {
    const py = pythonDi(path3.join(akar, `.venv-${p.id}`), win);
    return adaBerkas(py) ? py : void 0;
  }
  const venv = pythonDi(path3.join(akar, ".venv"), win);
  if (adaBerkas(venv)) return venv;
  const aplikasi = win ? path3.join(akar, ".python", "python.exe") : path3.join(akar, ".python", "bin", "python");
  return adaBerkas(aplikasi) ? aplikasi : void 0;
}
function jalurPenanda(akar, p) {
  if (!p.tataLetakLama) return path3.join(akar, `.venv-${p.id}`, PENANDA_REQS);
  return adaDir(path3.join(akar, ".venv")) ? path3.join(akar, ".venv", PENANDA_REQS) : path3.join(akar, ".python", PENANDA_REQS);
}
function statusSemuaProfil(akarRuntime, akarReq, platform, kunci) {
  const dirReq = path3.join(akarReq, "requirements");
  const manifest = bacaNyata(path3.join(dirReq, "profiles.json"));
  if (!manifest) return [];
  const win = platform === "win32";
  return uraiManifestProfil(manifest.toString("utf8")).map((profil) => {
    const penanda = bacaNyata(jalurPenanda(akarRuntime, profil))?.toString("utf8");
    return {
      profil,
      keadaan: keadaanProfil(profil, kunci, {
        adaInterpreter: jalurPythonProfil(akarRuntime, profil, win) !== void 0,
        penanda,
        hash: hashRequirements(dirReq, profil.requirements)
      })
    };
  });
}
function uraiKeluaranProfiles(stdout) {
  const hasil = /* @__PURE__ */ new Map();
  for (const baris of stdout.split(/\r?\n/)) {
    const m = /^([a-z0-9][a-z0-9-]{0,62})\s+(siap|belum dipasang|perlu dipasang ulang)(?:\s+\(([^)]*)\))?/.exec(baris.trim());
    if (!m) continue;
    const keadaan = m[2] === "siap" ? "siap" : m[2] === "belum dipasang" ? "belum_dipasang" : "perlu_dipasang_ulang";
    const rinci = keadaan === "perlu_dipasang_ulang" ? m[3] ?? "" : "";
    hasil.set(m[1], {
      keadaan,
      hilang: rinci && rinci !== "penanda tidak cocok" ? rinci.split(",").map((s) => s.trim()).filter(Boolean) : []
    });
  }
  return hasil;
}
function teksUkuran(byte) {
  if (byte === void 0) return "tidak diketahui";
  return byte >= GiB ? `${(byte / GiB).toFixed(1)} GB` : `${Math.max(0, Math.round(byte / 1024 ** 2))} MB`;
}
function kebutuhanDisk(p) {
  const g = p.perkiraan.diskGiB;
  return (typeof g === "number" && g > 0 ? g : DISK_MIN_GIB) * GiB;
}
function nilaiDisk(p, bebas) {
  const perlu = kebutuhanDisk(p);
  if (bebas === void 0) return { cukup: true, pesan: `Ruang disk tersedia tidak dapat dibaca; dibutuhkan sekitar ${teksUkuran(perlu)}.` };
  if (bebas < perlu) {
    return {
      cukup: false,
      pesan: `Ruang disk tidak cukup untuk lingkungan ${p.nama}: tersedia ${teksUkuran(bebas)}, dibutuhkan sekitar ${teksUkuran(perlu)}. Kosongkan ruang disk, lalu coba lagi.`
    };
  }
  return { cukup: true, pesan: `Ruang disk tersedia ${teksUkuran(bebas)}; dibutuhkan sekitar ${teksUkuran(perlu)}.` };
}

// extension/src/lingkungan/pythonDasar.ts
var import_node_child_process2 = require("node:child_process");
var fs5 = __toESM(require("node:fs"));
var path4 = __toESM(require("node:path"));
var VERSI_MIN = [3, 12];
var VERSI_MAKS = [3, 14];
var PY_VERSI = "3.12.10";
var PY_URL_DASAR = `https://www.python.org/ftp/python/${PY_VERSI}/python-${PY_VERSI}-`;
var PY_INSTALLER = Object.freeze({
  amd64: { sha256: "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb", ukuran: 26964224 },
  arm64: { sha256: "377ac8fd478987940088e879441e702a71b53164d2a1e6f1d51ff77a7e470258", ukuran: 26202936 }
});
var HOST_UNDUHAN = Object.freeze(["www.python.org"]);
var KODE_UJI = "import json,struct,sys,sysconfig,venv,ensurepip;print(json.dumps({'exe':sys.executable,'v':list(sys.version_info[:3]),'bits':struct.calcsize('P')*8,'plat':sysconfig.get_platform()}))";
var KODE_LENGKAP = "import os,sys,sysconfig;sys.exit(0 if os.path.isfile(os.path.join(sysconfig.get_path('include'),'pyconfig.h')) else 1)";
function archWindows(env, archProses) {
  for (const v of ["PROCESSOR_ARCHITEW6432", "PROCESSOR_ARCHITECTURE"]) {
    if ((env[v] ?? "").toUpperCase() === "ARM64") return "arm64";
  }
  return ["arm64", "aarch64"].includes(archProses.toLowerCase()) ? "arm64" : "amd64";
}
function pemasangWindows(arch) {
  const p = PY_INSTALLER[arch];
  return { url: `${PY_URL_DASAR}${arch}.exe`, namaBerkas: `python-${PY_VERSI}-${arch}.exe`, sha256: p.sha256, ukuran: p.ukuran };
}
function argumenInstaller(exe, target) {
  return [
    exe,
    "/quiet",
    "InstallAllUsers=0",
    `TargetDir=${target}`,
    "PrependPath=0",
    "Include_launcher=0",
    "Include_test=0",
    "AssociateFiles=0",
    "Shortcuts=0",
    "Include_pip=1",
    "Include_dev=1"
  ];
}
function uraiUji(stdout, platform, sumber) {
  const baris = stdout.trim().split(/\r?\n/).pop() ?? "";
  let info;
  try {
    info = JSON.parse(baris);
  } catch {
    return void 0;
  }
  if (!info || typeof info !== "object" || typeof info.exe !== "string" || !info.exe) return void 0;
  if (!Array.isArray(info.v) || info.v.length < 3 || !info.v.every((x) => Number.isInteger(x))) return void 0;
  const v = info.v;
  const duaAngka = (a, b) => a[0] - b[0] || a[1] - b[1];
  if (duaAngka(v, VERSI_MIN) < 0 || duaAngka(v, VERSI_MAKS) > 0 || info.bits !== 64) return void 0;
  if (platform === "win32" && info.plat !== "win-amd64" && info.plat !== "win-arm64") return void 0;
  return { jalur: info.exe, versi: [v[0], v[1], v[2]], sumber };
}
var NAMA_POSIX = ["python3.12", "python3.13", "python3.14", "python3"];
async function kandidatPython(d) {
  const hasil = [];
  const dariPath = (nama) => {
    for (const n of nama) {
      const j = d.which(n);
      if (j) hasil.push({ jalur: j, sumber: "PATH" });
    }
  };
  if (d.platform === "win32") {
    if (d.adaBerkas(d.pythonAplikasi)) hasil.push({ jalur: d.pythonAplikasi, sumber: "aplikasi" });
    const py = d.which("py");
    if (py) {
      for (const v of ["3.12", "3.13", "3.14"]) {
        const h = await d.jalankan([py, `-${v}`, "-c", "import sys;print(sys.executable)"], 1e4);
        const jalur = h && h.kode === 0 ? h.stdout.trim().split(/\r?\n/).pop() : void 0;
        if (jalur) hasil.push({ jalur, sumber: "py-launcher" });
      }
    }
    dariPath(["python"]);
  } else if (d.platform === "darwin") {
    dariPath(NAMA_POSIX);
    for (const dasar of ["/opt/homebrew/bin", "/usr/local/bin"]) {
      for (const n of NAMA_POSIX) {
        const j = path4.posix.join(dasar, n);
        if (d.adaBerkas(j)) hasil.push({ jalur: j, sumber: "lokasi-umum" });
      }
    }
    for (const minor of [12, 13, 14]) {
      const j = `/Library/Frameworks/Python.framework/Versions/3.${minor}/bin/python3`;
      if (d.adaBerkas(j)) hasil.push({ jalur: j, sumber: "lokasi-umum" });
    }
    const clt = await d.jalankan(["/usr/bin/xcode-select", "-p"], 5e3);
    if (clt && clt.kode === 0 && d.adaBerkas("/usr/bin/python3")) hasil.push({ jalur: "/usr/bin/python3", sumber: "lokasi-umum" });
  } else if (d.platform.startsWith("linux")) {
    dariPath(NAMA_POSIX);
    for (const n of NAMA_POSIX) {
      const j = path4.posix.join("/usr/bin", n);
      if (d.adaBerkas(j)) hasil.push({ jalur: j, sumber: "lokasi-umum" });
    }
  }
  const dilihat = /* @__PURE__ */ new Set();
  return hasil.filter((k) => dilihat.has(k.jalur) ? false : (dilihat.add(k.jalur), true));
}
async function ujiPython(d, jalur, sumber) {
  const h = await d.jalankan([jalur, "-c", KODE_UJI], 2e4);
  return h && h.kode === 0 ? uraiUji(h.stdout, d.platform, sumber) : void 0;
}
async function pythonLengkap(d, jalur) {
  const h = await d.jalankan([jalur, "-c", KODE_LENGKAP], 2e4);
  return h !== void 0 && h.kode === 0;
}
async function cariPythonDasar(d) {
  for (const k of await kandidatPython(d)) {
    const hasil = await ujiPython(d, k.jalur, k.sumber);
    if (!hasil) continue;
    if (d.platform === "win32" && k.sumber === "aplikasi" && !await pythonLengkap(d, d.pythonAplikasi)) continue;
    return hasil;
  }
  return void 0;
}
var dapatMemasangPython = (platform) => platform === "win32";
function petunjukPython(platform) {
  if (platform === "win32") {
    return `Python yang cocok tidak ditemukan. DSWorkbench dapat memasang Python ${PY_VERSI} ke folder runtime-nya sendiri (\xB127 MB dari python.org, tanpa hak administrator, tidak mengubah PATH).`;
  }
  if (platform === "darwin") {
    return 'Python 3.12 belum ditemukan. Pasang dari https://www.python.org/downloads/macos/ (pilih macOS 64-bit universal2 installer), atau jalankan `xcode-select --install` di Terminal, lalu tekan "Coba lagi".';
  }
  if (platform.startsWith("linux")) {
    return 'Python 3.12+ dengan modul venv belum ditemukan. Pasang lewat pengelola paket distro, mis. Debian/Ubuntu: `sudo apt install python3 python3-venv`, lalu tekan "Coba lagi".';
  }
  return 'Pasang Python 3.12 (64-bit) dari https://www.python.org/downloads/ lalu tekan "Coba lagi".';
}
function pesanPasangPythonGagal(kode) {
  return `Pemasangan Python tidak selesai (kode ${kode ?? "?"}). Pasang Python 3.12 64-bit dari https://www.python.org/downloads/ lalu tekan "Coba lagi".`;
}
function whichNyata(platform, env) {
  return (nama) => {
    const win = platform === "win32";
    const pemisah = win ? ";" : ":";
    const ext = win ? (env["PATHEXT"] || ".COM;.EXE;.BAT;.CMD").split(";").filter(Boolean) : [""];
    for (const dir of (env["PATH"] ?? env["Path"] ?? "").split(pemisah)) {
      if (!dir) continue;
      for (const e of win && path4.extname(nama) ? [""] : ext) {
        const j = path4.join(dir, nama + e);
        try {
          if (fs5.statSync(j).isFile()) {
            if (!win) fs5.accessSync(j, fs5.constants.X_OK);
            return j;
          }
        } catch {
        }
      }
    }
    return void 0;
  };
}
function jalankanNyata(env = process.env) {
  return (argv, batasMs) => new Promise((selesai) => {
    const [program, ...argumen2] = argv;
    if (!program) return selesai(void 0);
    const bersih = { ...env };
    delete bersih["PYTHONHOME"];
    delete bersih["PYTHONPATH"];
    (0, import_node_child_process2.execFile)(program, argumen2, { timeout: batasMs, windowsHide: true, env: bersih, maxBuffer: 1 << 20 }, (galat, stdout) => {
      if (galat && typeof galat.code !== "number") {
        return selesai(void 0);
      }
      selesai({ kode: galat ? galat.code ?? 1 : 0, stdout: String(stdout) });
    });
  });
}
function deteksiNyata(pythonAplikasi, platform = process.platform, env = process.env) {
  return {
    platform,
    which: whichNyata(platform, env),
    adaBerkas: (j) => {
      try {
        return fs5.statSync(j).isFile();
      } catch {
        return false;
      }
    },
    jalankan: jalankanNyata(env),
    pythonAplikasi
  };
}

// extension/src/lingkungan/unduh.ts
var import_node_crypto3 = require("node:crypto");
var fs6 = __toESM(require("node:fs"));
var http = __toESM(require("node:http"));
var https = __toESM(require("node:https"));
var path5 = __toESM(require("node:path"));
var GalatUnduh = class extends Error {
  constructor(kode, pesan) {
    super(pesan);
    this.kode = kode;
    this.name = "GalatUnduh";
  }
};
function periksaUrlUnduhan(alamat, hostDiizinkan, izinkanHttp = false) {
  let u;
  try {
    u = new URL(alamat);
  } catch {
    throw new GalatUnduh("url", "Alamat unduhan tidak sah.");
  }
  if (u.protocol !== "https:" && !(izinkanHttp && u.protocol === "http:")) {
    throw new GalatUnduh("url", "Unduhan hanya lewat HTTPS.");
  }
  if (u.username || u.password) throw new GalatUnduh("url", "Alamat unduhan tidak boleh memuat nama pengguna atau sandi.");
  if (!hostDiizinkan.includes(u.hostname.toLowerCase())) {
    throw new GalatUnduh("url", `Unduhan dari ${u.hostname} tidak diizinkan.`);
  }
  return u;
}
var PESAN_RUSAK = "Berkas unduhan rusak atau diubah (SHA-256 tidak cocok). Tidak ada yang dipasang; coba lagi dengan jaringan lain.";
function ambil(u, o, dari = 0) {
  return new Promise((selesai, gagal) => {
    const modul = u.protocol === "https:" ? https : http;
    const kepala = { "User-Agent": "DSWorkbench", Accept: "*/*" };
    if (dari > 0) kepala["Range"] = `bytes=${dari}-`;
    const req = modul.get(u, { headers: kepala, signal: o.sinyal }, selesai);
    req.setTimeout(o.batasDiamMs ?? 6e4, () => req.destroy(new GalatUnduh("jaringan", "Server unduhan tidak menjawab.")));
    req.on("error", gagal);
  });
}
async function unduhTerverifikasi(o) {
  const sementara = `${o.tujuan}.part`;
  const batal = () => new GalatUnduh("batal", "Pengunduhan dibatalkan.");
  let u = periksaUrlUnduhan(o.url, o.hostDiizinkan, o.izinkanHttp);
  fs6.mkdirSync(path5.dirname(o.tujuan), { recursive: true });
  fs6.rmSync(o.tujuan, { force: true });
  let berkas;
  const h = (0, import_node_crypto3.createHash)("sha256");
  let diterima = 0;
  try {
    if (o.sinyal?.aborted) throw batal();
    if (o.lanjut) {
      let ada = 0;
      try {
        ada = fs6.statSync(sementara).size;
      } catch {
      }
      if (ada > 0 && ada <= o.ukuran) {
        await new Promise((selesai, gagal) => {
          const baca = fs6.createReadStream(sementara);
          baca.on("data", (b) => h.update(b));
          baca.on("error", gagal);
          baca.on("end", () => selesai());
        });
        diterima = ada;
      } else if (ada > 0) fs6.rmSync(sementara, { force: true });
    } else fs6.rmSync(sementara, { force: true });
    if (diterima === o.ukuran) {
      if (h.digest("hex") !== o.sha256.toLowerCase()) throw new GalatUnduh("sha256", PESAN_RUSAK);
      fs6.renameSync(sementara, o.tujuan);
      return o.tujuan;
    }
    const mulaiDari = diterima;
    if (mulaiDari > 0) o.progres?.(diterima, o.ukuran);
    let resp;
    for (let alih = 0; ; alih++) {
      resp = await ambil(u, o, mulaiDari);
      const status = resp.statusCode ?? 0;
      if (![301, 302, 303, 307, 308].includes(status)) break;
      resp.resume();
      const lokasi = resp.headers.location;
      if (!lokasi || alih >= (o.maksAlih ?? 3)) throw new GalatUnduh("alih", "Server unduhan mengalihkan terlalu banyak kali.");
      let tujuanAlih;
      try {
        tujuanAlih = periksaUrlUnduhan(new URL(lokasi, u).toString(), o.hostDiizinkan, o.izinkanHttp);
      } catch {
        throw new GalatUnduh("alih", "Server unduhan mengalihkan ke alamat yang tidak diizinkan. Tidak ada yang diunduh.");
      }
      if (u.protocol === "https:" && tujuanAlih.protocol !== "https:") {
        throw new GalatUnduh("alih", "Server unduhan mengalihkan ke alamat tanpa HTTPS. Tidak ada yang diunduh.");
      }
      u = tujuanAlih;
    }
    let sambung = false;
    if (mulaiDari > 0 && resp.statusCode === 206) sambung = true;
    else if (mulaiDari > 0 && resp.statusCode === 200) {
      diterima = 0;
    } else if (resp.statusCode !== 200) {
      resp.resume();
      throw new GalatUnduh("http", `Server unduhan menjawab HTTP ${resp.statusCode}.`);
    }
    const hh = sambung ? h : (0, import_node_crypto3.createHash)("sha256");
    berkas = fs6.createWriteStream(sementara, { flags: sambung ? "a" : "w" });
    const tulis = berkas;
    await new Promise((selesai, gagal) => {
      tulis.on("error", gagal);
      resp.on("error", gagal);
      resp.on("aborted", () => gagal(new GalatUnduh("jaringan", "Sambungan unduhan terputus.")));
      resp.on("data", (blok) => {
        diterima += blok.length;
        if (diterima > o.ukuran) {
          gagal(new GalatUnduh("ukuran", "Berkas unduhan lebih besar dari yang diharapkan."));
          resp.destroy();
          return;
        }
        hh.update(blok);
        if (!tulis.write(blok)) {
          resp.pause();
          tulis.once("drain", () => resp.resume());
        }
        o.progres?.(diterima, o.ukuran);
      });
      resp.on("end", () => tulis.end(() => selesai()));
    });
    berkas = void 0;
    if (diterima !== o.ukuran || hh.digest("hex") !== o.sha256.toLowerCase()) throw new GalatUnduh("sha256", PESAN_RUSAK);
    fs6.renameSync(sementara, o.tujuan);
    return o.tujuan;
  } catch (e) {
    berkas?.destroy();
    if (berkas) await new Promise((r) => berkas.closed ? r() : berkas.once("close", () => r()));
    const simpan = o.lanjut && !(e instanceof GalatUnduh && (e.kode === "sha256" || e.kode === "ukuran" || e.kode === "url" || e.kode === "alih"));
    if (!simpan) fs6.rmSync(sementara, { force: true });
    fs6.rmSync(o.tujuan, { force: true });
    if (o.sinyal?.aborted || e?.name === "AbortError") throw batal();
    if (e instanceof GalatUnduh) throw e;
    throw new GalatUnduh("jaringan", `Unduhan gagal: ${e instanceof Error ? e.message : String(e)}`);
  }
}

// extension/src/lingkungan/pasang.ts
var GalatPasang = class extends Error {
  constructor(kode, pesan) {
    super(pesan);
    this.kode = kode;
    this.name = "GalatPasang";
  }
};
function pemecahBaris(keluar) {
  let sisa = "";
  return {
    tulis: (d) => {
      sisa += typeof d === "string" ? d : d.toString("utf8");
      const bagian = sisa.split(/\r\n|\n|\r/);
      sisa = bagian.pop() ?? "";
      for (const b of bagian) keluar(b);
    },
    habis: () => {
      if (sisa) keluar(sisa);
      sisa = "";
    }
  };
}
function hentikanPohon(pid, platform = process.platform) {
  if (!pid) return () => void 0;
  if (platform === "win32") {
    (0, import_node_child_process3.execFile)("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true }, () => void 0);
    return () => void 0;
  }
  const kirim = (sinyal) => {
    try {
      process.kill(-pid, sinyal);
    } catch {
    }
  };
  kirim("SIGINT");
  const pewaktu = [setTimeout(() => kirim("SIGTERM"), 4e3), setTimeout(() => kirim("SIGKILL"), 8e3)];
  for (const w of pewaktu) w.unref();
  return () => pewaktu.forEach((w) => clearTimeout(w));
}
function jalankanMengalir(argv, opsi) {
  return new Promise((selesai, gagal) => {
    const [program, ...argumen2] = argv;
    if (!program) return gagal(new Error("perintah kosong"));
    const platform = opsi.platform ?? process.platform;
    const anak = (0, import_node_child_process3.spawn)(program, argumen2, {
      cwd: opsi.cwd,
      env: opsi.env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
      // Grup proses sendiri supaya pembatalan ikut menghentikan pip.
      detached: platform !== "win32"
    });
    const ekor = [];
    const terima = (b) => {
      if (b.trim()) {
        ekor.push(b);
        if (ekor.length > 8) ekor.shift();
      }
      opsi.baris(b);
    };
    const keluar = pemecahBaris(terima);
    const galat = pemecahBaris(terima);
    anak.stdout.on("data", keluar.tulis);
    anak.stderr.on("data", galat.tulis);
    let hentiEskalasi = () => void 0;
    const batal = () => {
      hentiEskalasi = hentikanPohon(anak.pid, platform);
    };
    if (opsi.sinyal?.aborted) batal();
    opsi.sinyal?.addEventListener("abort", batal, { once: true });
    anak.on("error", (e) => {
      opsi.sinyal?.removeEventListener("abort", batal);
      gagal(e);
    });
    anak.on("close", (kode) => {
      opsi.sinyal?.removeEventListener("abort", batal);
      hentiEskalasi();
      keluar.habis();
      galat.habis();
      selesai({ kode, ekor });
    });
  });
}
async function pasangPythonWindows(o) {
  o.lapor.tahap("mengunduh Python dari python.org\u2026");
  o.lapor.baris(`Mengunduh pemasang Python resmi: ${o.pemasang.url}`);
  const tujuan = path6.join(o.dirSementara, o.pemasang.namaBerkas);
  let exe;
  try {
    exe = await o.unduh({
      url: o.pemasang.url,
      tujuan,
      sha256: o.pemasang.sha256,
      ukuran: o.pemasang.ukuran,
      progres: o.lapor.unduh,
      sinyal: o.sinyal
    });
  } catch (e) {
    if (e instanceof GalatUnduh) throw new GalatPasang(e.kode === "batal" ? "batal" : "unduhan", e.message);
    throw e;
  }
  let kode;
  try {
    o.lapor.baris("SHA-256 pemasang cocok dengan nilai yang dipatok.");
    if (o.sinyal?.aborted) throw new GalatPasang("batal", "Pemasangan dibatalkan.");
    o.lapor.tahap("memasang Python ke folder runtime\u2026");
    fs7.rmSync(o.dirPython, { recursive: true, force: true });
    kode = await o.jalankanPemasang(argumenInstaller(exe, o.dirPython));
  } finally {
    fs7.rmSync(exe, { force: true });
  }
  const dasar = await o.uji(o.pythonAplikasi);
  if (kode !== 0 || !dasar || !await o.lengkap(o.pythonAplikasi)) {
    throw new GalatPasang("tanpa_python", pesanPasangPythonGagal(kode));
  }
  o.lapor.baris(`Python ${dasar.versi.join(".")} terpasang di ${o.dirPython}.`);
  return dasar;
}
var PemasangLingkungan = class {
  constructor(ambilKonteks) {
    this.ambilKonteks = ambilKonteks;
  }
  sedang;
  get k() {
    return this.ambilKonteks();
  }
  get l() {
    return this.k.l ?? lingkunganNyata();
  }
  get payload() {
    return akarPayload(this.k.akarEkstensi);
  }
  get akarRuntime() {
    return tataLetak(this.l).akar;
  }
  /** Profil yang sedang dipasang jendela ini, bila ada. */
  get sedangMemasang() {
    return this.sedang?.profil;
  }
  kunciPlatform() {
    const l = this.l;
    const arch = this.k.arch ?? process.arch;
    return kunciPlatform(l.platform, l.platform === "win32" ? archWindows(l.env, arch) : arch);
  }
  /** Keadaan semua profil (murah; hanya membaca disk). */
  status() {
    const akar = this.akarRuntime;
    return statusSemuaProfil(akar, akarRequirementsBerlaku(akar, this.payload), this.l.platform, this.kunciPlatform());
  }
  ruangDisk() {
    return ruangDiskBebas(this.akarRuntime);
  }
  deteksi() {
    return this.k.deteksi ?? deteksiNyata(tataLetak(this.l).pythonAplikasi, this.l.platform, this.l.env);
  }
  /** Python dasar yang layak di komputer ini (menjalankan kandidatnya). */
  cariPython() {
    return cariPythonDasar(this.deteksi());
  }
  /** Buat folder runtime bila perlu dan selaraskan `requirements*` dari payload. */
  siapkanFolder() {
    if (!fs7.existsSync(path6.join(this.payload, "src", "workbench_agent", "cli.py"))) {
      throw new GalatPasang("payload", "Salinan Local Runner bawaan aplikasi ini tidak ditemukan. Pasang ulang aplikasi DSWorkbench.");
    }
    try {
      return siapkanFolder(this.akarRuntime, this.payload, this.k.versiEkstensi);
    } catch (e) {
      throw new GalatPasang("gagal", `Folder runtime ${this.akarRuntime} tidak dapat disiapkan: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  /** Peluncur CLI agent dari payload dengan interpreter `python` (untuk `ensure-env`, `adopt`, `profiles`). */
  peluncur(python) {
    const p = peluncurBawaan({ ...this.k.atur, akarEkstensi: this.k.akarEkstensi }, python, true, this.l);
    if (!p) throw new GalatPasang("payload", "Salinan Local Runner bawaan aplikasi ini tidak ditemukan. Pasang ulang aplikasi DSWorkbench.");
    return p;
  }
  /** Apa yang dibutuhkan sebelum memasang `profilId` — untuk ditampilkan dan disetujui pengguna. Tidak menulis apa pun. */
  async prasyarat(profilId) {
    const s = this.status().find((x) => x.profil.id === profilId);
    if (!s) throw new GalatPasang("profil", `Profil lingkungan "${profilId}" tidak dikenal aplikasi ini.`);
    const python = await this.cariPython();
    const diskBebas = this.ruangDisk();
    const bisaPasang = dapatMemasangPython(this.l.platform);
    return {
      profil: s.profil,
      keadaan: s.keadaan,
      python,
      perluPasangPython: !python && bisaPasang,
      petunjukPython: python || bisaPasang ? void 0 : petunjukPython(this.l.platform),
      diskBebas,
      disk: nilaiDisk(s.profil, diskBebas),
      kunciPlatform: this.kunciPlatform()
    };
  }
  /**
   * Windows: unduh pemasang resmi python.org (sha256 dipatok, diverifikasi
   * SEBELUM dijalankan), pasang ke `DATA_ROOT\.python` tanpa admin dan tanpa
   * mengubah PATH, lalu periksa kelengkapannya.
   */
  async pasangPython(lapor, sinyal) {
    const l = this.l;
    if (!dapatMemasangPython(l.platform)) throw new GalatPasang("tanpa_python", petunjukPython(l.platform));
    const t = tataLetak(l);
    const d = this.deteksi();
    const tmp = fs7.mkdtempSync(path6.join(os2.tmpdir(), "dsw-python-"));
    try {
      return await pasangPythonWindows({
        pemasang: pemasangWindows(archWindows(l.env, this.k.arch ?? process.arch)),
        dirPython: t.dirPythonAplikasi,
        pythonAplikasi: t.pythonAplikasi,
        dirSementara: tmp,
        unduh: (o) => unduhTerverifikasi({ ...o, hostDiizinkan: HOST_UNDUHAN }),
        jalankanPemasang: (argv) => new Promise((selesai) => {
          const [program, ...argumen2] = argv;
          (0, import_node_child_process3.execFile)(program, argumen2, { timeout: 9e5, windowsHide: true }, (galat) => {
            const k = galat?.code;
            selesai(galat ? typeof k === "number" ? k : null : 0);
          });
        }),
        uji: (jalur) => ujiPython(d, jalur, "aplikasi"),
        lengkap: (jalur) => pythonLengkap(d, jalur),
        lapor,
        sinyal
      });
    } finally {
      fs7.rmSync(tmp, { recursive: true, force: true });
    }
  }
  /**
   * Pasang (atau perbarui) satu profil. Satu pemasangan pada satu waktu per
   * jendela, dan satu per folder runtime lewat kunci berkas.
   * `aplikasiLama`: hasil deteksi aplikasi lama yang berjalan (ditolak bila ada).
   */
  async pasang(profilId, lapor, opsi = {}) {
    if (this.sedang) {
      throw new GalatPasang("kunci", `Lingkungan "${this.sedang.profil}" sedang dipasang. Tunggu sampai selesai.`);
    }
    const janji = this.lakukanPasang(profilId, lapor, opsi);
    this.sedang = { profil: profilId, janji };
    try {
      await janji;
    } finally {
      this.sedang = void 0;
    }
  }
  async lakukanPasang(profilId, lapor, opsi) {
    const batal = () => new GalatPasang("batal", "Pemasangan dibatalkan.");
    if (opsi.aplikasiLama?.().aplikasiLama) throw new GalatPasang("aplikasi_lama", PESAN_TUTUP_APLIKASI_LAMA);
    lapor.tahap("menyiapkan folder runtime\u2026");
    const siap = this.siapkanFolder();
    lapor.baris(`Folder runtime: ${siap.akar}${siap.dibuat ? " (baru dibuat)" : ""}`);
    if (siap.ditulis.length > 0) lapor.baris(`Berkas requirements diselaraskan dari aplikasi: ${siap.ditulis.join(", ")}`);
    if (siap.rencana.ditahan) {
      lapor.baris("Berkas requirements di folder runtime berasal dari Local Runner yang lebih baru; tidak ditimpa.");
    }
    const s = this.status().find((x) => x.profil.id === profilId);
    if (!s) throw new GalatPasang("profil", `Profil lingkungan "${profilId}" tidak dikenal aplikasi ini.`);
    if (s.keadaan === "tidak_didukung") {
      throw new GalatPasang(
        "tidak_didukung",
        `Lingkungan ${s.profil.nama} tidak dapat dipasang di ${namaPlatform(this.kunciPlatform())}: pustaka yang dibutuhkannya tidak menyediakan paket siap-pasang untuk komputer ini. Hubungi asisten praktikum.`
      );
    }
    if (s.keadaan !== "siap") {
      const disk = nilaiDisk(s.profil, this.ruangDisk());
      lapor.baris(disk.pesan);
      if (!disk.cukup) throw new GalatPasang("disk", disk.pesan);
    }
    const kunci = ambilKunci(tataLetak(this.l).kunciPasang, { profil: profilId });
    if (!kunci.ok) {
      throw new GalatPasang(
        "kunci",
        "Jendela DSWorkbench lain sedang memasang lingkungan" + (kunci.pemegang?.profil ? ` "${kunci.pemegang.profil}"` : "") + ". Tunggu sampai selesai, lalu coba lagi."
      );
    }
    try {
      if (opsi.sinyal?.aborted) throw batal();
      let python = opsi.python ?? await this.cariPython();
      if (!python) {
        if (!dapatMemasangPython(this.l.platform) || !opsi.izinkanPasangPython) {
          throw new GalatPasang("tanpa_python", petunjukPython(this.l.platform));
        }
        python = await this.pasangPython(lapor, opsi.sinyal);
      }
      lapor.baris(`Python dasar: ${python.jalur} (${python.versi.join(".")}, ${python.sumber})`);
      if (opsi.sinyal?.aborted) throw batal();
      lapor.tahap(`memasang ${s.profil.nama}\u2026`);
      const peluncur = this.peluncur(python.jalur);
      const argv = perintahSekali(peluncur, "ensure-env", "--profile", profilId);
      lapor.baris(`$ ${argv.map((a) => /\s/.test(a) ? JSON.stringify(a) : a).join(" ")}`);
      let hasil;
      try {
        hasil = await jalankanMengalir(argv, { cwd: peluncur.cwd, env: peluncur.env, baris: lapor.baris, sinyal: opsi.sinyal, platform: this.l.platform });
      } catch (e) {
        throw new GalatPasang("gagal", `Pemasang lingkungan tidak dapat dijalankan: ${e instanceof Error ? e.message : String(e)}`);
      }
      if (opsi.sinyal?.aborted) throw batal();
      if (hasil.kode !== 0) {
        throw new GalatPasang(
          "gagal",
          `Lingkungan ${s.profil.nama} gagal dipasang (kode ${hasil.kode ?? "?"}).
${hasil.ekor.slice(-6).join("\n")}`
        );
      }
      lapor.baris(`Lingkungan ${s.profil.nama} siap.`);
    } finally {
      kunci.lepas();
    }
  }
};

// smoke/uji-lingkungan.ts
function argumen() {
  const hasil = {};
  const a = process.argv.slice(2);
  for (let i = 0; i < a.length; i++) {
    const k = a[i];
    if (!k.startsWith("--")) throw new Error(`argumen tidak dikenal: ${k}`);
    const berikut = a[i + 1];
    if (berikut === void 0 || berikut.startsWith("--")) hasil[k.slice(2)] = true;
    else {
      hasil[k.slice(2)] = berikut;
      i++;
    }
  }
  return hasil;
}
async function utama() {
  const o = argumen();
  const ext = path7.resolve(String(o["ext"] ?? ""));
  const runtime = path7.resolve(String(o["runtime"] ?? ""));
  const profilId = typeof o["profil"] === "string" ? o["profil"] : "python-data-science";
  const kering = o["kering"] === true;
  if (typeof o["ext"] !== "string" || typeof o["runtime"] !== "string") throw new Error("wajib: --ext <folder ekstensi> --runtime <folder runtime sementara>");
  if (!fs8.existsSync(path7.join(ext, "agent-payload", "src", "workbench_agent", "cli.py"))) throw new Error(`agent-payload tidak ada di ${ext}`);
  if (fs8.existsSync(runtime) && fs8.readdirSync(runtime).length > 0 && !fs8.existsSync(path7.join(runtime, ".uji-asap"))) {
    throw new Error(`${runtime} sudah berisi sesuatu yang bukan milik uji ini; beri folder kosong`);
  }
  fs8.mkdirSync(runtime, { recursive: true });
  fs8.writeFileSync(path7.join(runtime, ".uji-asap"), "folder runtime sementara uji asap DSWorkbench\n");
  process.env["DSW_DATA_ROOT"] = runtime;
  const pkg = JSON.parse(fs8.readFileSync(path7.join(ext, "package.json"), "utf8"));
  const pemasang = new PemasangLingkungan(() => ({
    akarEkstensi: ext,
    versiEkstensi: pkg.version,
    // Alamat server tidak dipakai `ensure-env`/`profiles`; diisi nilai yang tidak dapat dijangkau.
    atur: { serverUrl: "https://server-uji.invalid", perintah: [], pakaiBawaan: true }
  }));
  if (pemasang.akarRuntime !== runtime) throw new Error(`folder runtime tidak teralihkan: ${pemasang.akarRuntime}`);
  const langkah = [];
  const log = [];
  const lapor = {
    baris: (b) => {
      log.push(b);
      console.log(`    ${b}`);
    },
    tahap: (t) => console.log(`  \xB7 ${t}`)
  };
  let gagal = false;
  const jalan = async (nama, f) => {
    if (gagal) return false;
    const mulai = Date.now();
    console.log(`
\u25B6 ${nama}`);
    try {
      const rinci = await f();
      langkah.push({ nama, lulus: true, detik: (Date.now() - mulai) / 1e3, rinci });
      console.log(`  \u2714 ${nama} (${((Date.now() - mulai) / 1e3).toFixed(1)} dtk)`);
      return true;
    } catch (e) {
      gagal = true;
      const pesan = e instanceof GalatPasang ? `${e.kode}: ${e.message}` : e instanceof Error ? e.stack ?? e.message : String(e);
      langkah.push({ nama, lulus: false, detik: (Date.now() - mulai) / 1e3, galat: pesan });
      console.error(`  \u2718 ${nama}: ${pesan}`);
      return false;
    }
  };
  const win = process.platform === "win32";
  let python;
  await jalan("folder runtime disiapkan dari payload ekstensi", async () => {
    const h = pemasang.siapkanFolder();
    if (!fs8.existsSync(path7.join(runtime, "requirements", "profiles.json"))) throw new Error("requirements/profiles.json tidak tersalin");
    return { akar: h.akar, dibuat: h.dibuat, ditulis: h.ditulis.length };
  });
  await jalan(`profil ${profilId} dikenal dan didukung di ${pemasang.kunciPlatform()}`, async () => {
    const s = pemasang.status();
    const p = s.find((x) => x.profil.id === profilId);
    if (!p) throw new Error(`profil tidak dikenal; yang ada: ${s.map((x) => x.profil.id).join(", ")}`);
    if (p.keadaan === "tidak_didukung") throw new Error("profil tidak didukung di platform ini");
    return s.map((x) => ({ id: x.profil.id, keadaan: x.keadaan }));
  });
  if (o["pasang-python"] === true) {
    if (!win) throw new Error("--pasang-python hanya untuk Windows");
    const arch = archWindows(process.env, process.arch);
    const p = pemasangWindows(arch);
    if (kering) {
      await jalan("rencana pemasangan Python (kering)", async () => ({ ...p, host: HOST_UNDUHAN, argv: argumenInstaller("<pemasang>", tataLetak().dirPythonAplikasi) }));
    } else {
      await jalan(`Python resmi ${p.namaBerkas} diunduh dari ${HOST_UNDUHAN.join(", ")} (SHA-256 dipatok), dipasang ke .python, dan lengkap`, async () => {
        python = await pemasang.pasangPython(lapor);
        const t = tataLetak();
        if (path7.resolve(python.jalur).toLowerCase() !== path7.resolve(t.pythonAplikasi).toLowerCase()) throw new Error(`Python terpasang di ${python.jalur}, bukan ${t.pythonAplikasi}`);
        for (const wajib of ["python.exe", path7.join("include", "pyconfig.h"), path7.join("Lib", "venv", "__init__.py"), path7.join("Lib", "ensurepip", "__init__.py")]) {
          if (!fs8.existsSync(path7.join(t.dirPythonAplikasi, wajib))) throw new Error(`${wajib} tidak ada di ${t.dirPythonAplikasi}`);
        }
        return { jalur: python.jalur, versi: python.versi.join(".") };
      });
    }
  }
  await jalan("Python dasar yang layak ditemukan (3.12\u20133.14, 64-bit, venv + ensurepip)", async () => {
    python ??= await pemasang.cariPython();
    if (!python) throw new Error("tidak ada Python dasar yang layak");
    return { jalur: python.jalur, versi: python.versi.join("."), sumber: python.sumber };
  });
  if (kering) {
    await jalan("rencana ensure-env (kering; tidak dijalankan)", async () => {
      const peluncur = pemasang.peluncur(python.jalur);
      const pra = await pemasang.prasyarat(profilId);
      return { argv: perintahSekali(peluncur, "ensure-env", "--profile", profilId), cwd: peluncur.cwd, keadaan: pra.keadaan, disk: pra.disk.pesan };
    });
  } else {
    await jalan(`ensure-env --profile ${profilId} sungguhan (pip dari PyPI)`, async () => {
      await pemasang.pasang(profilId, lapor, { python, izinkanPasangPython: false });
      return { ekor: log.slice(-3) };
    });
    await jalan("status dari penanda: profil siap", async () => {
      const p = pemasang.status().find((x) => x.profil.id === profilId);
      if (p.keadaan !== "siap") throw new Error(`keadaan ${p.keadaan}`);
      return { keadaan: p.keadaan };
    });
    let pyProfil;
    await jalan("`workbench-agent profiles` (impor paket) melaporkan profil siap", async () => {
      const profil = pemasang.status().find((x) => x.profil.id === profilId).profil;
      pyProfil = jalurPythonProfil(runtime, profil, win);
      if (!pyProfil) throw new Error("interpreter profil tidak ditemukan di folder runtime");
      const h = await jalankanSekali(pemasang.peluncur(pyProfil), ["profiles"], void 0, 3e5);
      if (h.kode !== 0) throw new Error(`profiles keluar ${h.kode}: ${h.stderr.slice(-800)}`);
      const baris = uraiKeluaranProfiles(h.stdout).get(profilId);
      if (!baris || baris.keadaan !== "siap" || baris.hilang.length > 0) throw new Error(`profiles: ${JSON.stringify(baris)}
${h.stdout}`);
      return { python: pyProfil, keluaran: h.stdout.trim().split(/\r?\n/) };
    });
    await jalan("paket wajib profil benar-benar terimpor oleh interpreter profil", async () => {
      const manifest = JSON.parse(fs8.readFileSync(path7.join(runtime, "requirements", "profiles.json"), "utf8"));
      const modul = manifest.profiles[profilId]?.requiredImports ?? [];
      if (modul.length === 0) throw new Error("manifest profil tidak memuat requiredImports");
      const kode = `import importlib, json, sys
v = {}
for m in ${JSON.stringify(modul)}:
    v[m] = getattr(importlib.import_module(m), "__version__", "?")
print(json.dumps({"python": sys.version.split()[0], "modul": v}))`;
      const keluar = await new Promise(
        (selesai, tolak) => (0, import_node_child_process4.execFile)(pyProfil, ["-c", kode], { timeout: 3e5, windowsHide: true }, (g, stdout, stderr) => g ? tolak(new Error(`${g.message}
${stderr}`)) : selesai(stdout))
      );
      return JSON.parse(keluar.trim().split(/\r?\n/).pop());
    });
  }
  const ringkasan = {
    lulus: !gagal,
    kering,
    platform: process.platform,
    arch: process.arch,
    kunciPlatform: pemasang.kunciPlatform(),
    profil: profilId,
    ekstensi: { folder: ext, versi: pkg.version },
    runtime,
    langkah
  };
  if (typeof o["keluar"] === "string") {
    fs8.mkdirSync(path7.dirname(path7.resolve(o["keluar"])), { recursive: true });
    fs8.writeFileSync(o["keluar"], JSON.stringify(ringkasan, null, 1));
  }
  console.log(`
${gagal ? "GAGAL" : "LULUS"}: ${langkah.filter((l) => l.lulus).length}/${langkah.length} langkah${kering ? " (kering)" : ""}.`);
  return gagal ? 1 : 0;
}
utama().then(
  (k) => process.exit(k),
  (e) => {
    console.error(e instanceof Error ? e.message : String(e));
    process.exit(2);
  }
);
