"use strict";
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
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
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// test/integration/suiteLab.ts
var suiteLab_exports = {};
__export(suiteLab_exports, {
  run: () => run
});
module.exports = __toCommonJS(suiteLab_exports);
var fs = __toESM(require("node:fs"));
var path = __toESM(require("node:path"));
var vscode = __toESM(require("vscode"));
var tidur = (ms) => new Promise((r) => setTimeout(r, ms));
async function sampai(syarat, batasMs, pesan) {
  const batas = Date.now() + batasMs;
  while (!await syarat()) {
    if (Date.now() > batas) throw new Error(pesan);
    await tidur(100);
  }
}
async function run() {
  const env2 = process.env;
  const tahap = env2["DSW_LAB_TAHAP"] ?? "";
  const hasil = { tahap, vscode: vscode.version, app: vscode.env.appName };
  const tulis = () => fs.writeFileSync(env2["DSW_OUT"], JSON.stringify(hasil, null, 1));
  try {
    const ext = vscode.extensions.getExtension("sditera.dsworkbench");
    if (!ext) throw new Error("ekstensi sditera.dsworkbench tidak termuat");
    const api = await ext.activate();
    if (!api.uji) throw new Error("kait uji tidak tersedia (bukan ExtensionMode.Test?)");
    hasil["folder"] = (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath);
    hasil["tab"] = vscode.window.tabGroups.all.flatMap((g) => g.tabs).length;
    const lab = api.uji.lab;
    if (!lab) throw new Error("mode lab tidak aktif padahal DSW_LAB_CONFIG diisi");
    await lab.siap();
    hasil["awal"] = { keadaan: lab.keadaan(), tersimpan: await lab.tersimpan() };
    {
      const c = vscode.workspace.getConfiguration("dsworkbench");
      const bawaan = api.uji.suara.pengaturan();
      await c.update("sound.enabled", true, vscode.ConfigurationTarget.Global);
      const eksplisit = api.uji.suara.pengaturan();
      await c.update("sound.enabled", void 0, vscode.ConfigurationTarget.Global);
      hasil["suara"] = { bawaan, eksplisit, akhir: api.uji.suara.pengaturan() };
    }
    const JEJAK_LAMA = ["dsw.sql.var.pergudangan-data.modul-02/muat.sql", "dsworkbench.sql.target.pergudangan-data", "dsworkbench.gudang.modul.pergudangan-data"];
    if (lab.tanam && tahap === "masuk") {
      await lab.tanam(JEJAK_LAMA);
      hasil["jejakLama"] = { ditanam: JEJAK_LAMA, sebelum: (await lab.tersimpan()).kunciRuang };
    }
    void vscode.commands.executeCommand("dsworkbench.login");
    await sampai(() => lab.keadaan().masuk, 45e3, "masuk tidak selesai");
    await sampai(() => api.uji.statusAgent().keadaan === "siap", 3e4, "Local Runner tidak siap setelah masuk");
    hasil["setelahMasuk"] = { keadaan: lab.keadaan(), tersimpan: await lab.tersimpan(), agent: api.uji.statusAgent() };
    if (tahap === "masuk") {
      if (lab.tanam) {
        await sampai(async () => !(await lab.tersimpan()).kunciRuang?.some((k) => JEJAK_LAMA.includes(k)), 1e4, "").catch(() => void 0);
        hasil["jejakLama"]["sesudahMasuk"] = (await lab.tersimpan()).kunciRuang;
        const JEJAK_KINI = ["dsw.sql.target.pergudangan-data", "dsw.sql.var.pergudangan-data.modul-02/muat.sql", "dsworkbench.sql.target.pergudangan-data", "dsworkbench.sql.var.pergudangan-data.modul-02/muat.sql", "dsworkbench.gudang.modul.pergudangan-data", "dsworkbench.gudang.target.pergudangan-data", "kunciTanpaAwalan"];
        await lab.tanam(JEJAK_KINI);
        const t = await lab.tersimpan();
        hasil["jejakKini"] = { ditanam: JEJAK_KINI, global: t.kunci, ruang: t.kunciRuang };
      }
      const beranda = await api.uji.beranda();
      hasil["beranda"] = { pita: beranda.html.includes("Mode lab aktif"), tombolKeluar: beranda.html.includes('data-tindakan="keluar"'), lab: beranda.model?.lab };
      hasil["statusView"] = api.uji.lingkungan().status;
      if (api.uji.menulis && api.uji.berkas) {
        const mn = api.uji.menulis;
        const bk = api.uji.berkas;
        const ws = env2["DSW_WS_COURSE"];
        const utama = await mn.buat("artikel", ws, "makalah-lab");
        await vscode.commands.executeCommand("workbench.action.closeAllEditors");
        const daftar = await bk.muat() ?? [];
        const folderLuar = path.join(path.dirname(ws), "luar-ruang-kerja");
        const imporLuar = await bk.impor(0, folderLuar, false).then(
          () => "DIIMPOR",
          (e) => e instanceof Error ? e.message : String(e)
        );
        const imporDalam = (await bk.impor(0, path.join(ws, "data"), false)).berkas;
        hasil["alat"] = {
          menulisLab: mn.keadaan().keadaan.lab,
          utama,
          proyekSaatMasuk: mn.keadaan().keadaan.proyek.map((p) => p.judul),
          berkasSaatMasuk: daftar.map((o) => o.nama),
          folderLuar,
          imporLuar,
          imporDalam,
          kunciSaatMasuk: (await lab.tersimpan()).kunci
        };
      }
      const uri = vscode.Uri.file(path.join(env2["DSW_WS_COURSE"], "catatan-lab.txt"));
      const doc = await vscode.workspace.openTextDocument(uri);
      await vscode.window.showTextDocument(doc, { preview: false });
      const sunting = new vscode.WorkspaceEdit();
      sunting.insert(uri, new vscode.Position(0, 0), "BELUM-DISIMPAN ");
      await vscode.workspace.applyEdit(sunting);
      const tanpaNama = await vscode.workspace.openTextDocument({ content: 'print("isi tanpa nama")\n', language: "python" });
      await vscode.window.showTextDocument(tanpaNama, { preview: false });
      hasil["sebelumMenganggur"] = { kotor: doc.isDirty, tab: vscode.window.tabGroups.all.flatMap((g) => g.tabs).length };
      const mulai = Date.now();
      lab.persingkat(3e3, 2e3);
      await sampai(() => lab.keadaan().fase === "peringatan" || !lab.keadaan().masuk, 3e4, "peringatan menganggur tidak muncul");
      hasil["fasePeringatan"] = { fase: lab.keadaan().fase, masuk: lab.keadaan().masuk, bilah: lab.keadaan().teksBilah, detik: (Date.now() - mulai) / 1e3 };
      await sampai(() => !lab.keadaan().masuk && lab.keadaan().laporanKeluar.length > 0, 6e4, "keluar karena menganggur tidak terjadi");
      hasil["menganggur"] = {
        detik: (Date.now() - mulai) / 1e3,
        keadaan: lab.keadaan(),
        tersimpan: await lab.tersimpan(),
        tab: vscode.window.tabGroups.all.flatMap((g) => g.tabs).length,
        terminal: vscode.window.terminals.length,
        agent: api.uji.statusAgent()
      };
      if (hasil["alat"] && api.uji.menulis && api.uji.berkas) {
        const k = api.uji.berkas.keadaan();
        hasil["alat"]["sesudah"] = {
          proyek: api.uji.menulis.keadaan().keadaan.proyek.map((p) => p.judul),
          daftarBerkas: k.keadaan.daftar !== void 0,
          htmlBerkasMemuatNama: k.html.includes("dataset-rumah.csv")
        };
      }
    } else if (tahap === "paksa") {
      tulis();
      await tidur(12e4);
      return;
    }
  } catch (e) {
    hasil["galat"] = e instanceof Error ? e.stack ?? e.message : String(e);
  }
  tulis();
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  run
});
