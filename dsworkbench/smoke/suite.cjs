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

// test/integration/suite.ts
var suite_exports = {};
__export(suite_exports, {
  run: () => run
});
module.exports = __toCommonJS(suite_exports);
var fs = __toESM(require("node:fs"));
var path = __toESM(require("node:path"));
var vscode = __toESM(require("vscode"));
var tidur = (ms) => new Promise((r) => setTimeout(r, ms));
async function sampai(syarat, batasMs, pesan) {
  const batas = Date.now() + batasMs;
  while (!syarat()) {
    if (Date.now() > batas) throw new Error(pesan);
    await tidur(100);
  }
}
async function run() {
  const hasil = { vscode: vscode.version, app: vscode.env.appName };
  const env2 = process.env;
  try {
    hasil["jupyterExt"] = vscode.extensions.getExtension("ms-toolsai.jupyter") !== void 0;
    hasil["ipynbBawaan"] = vscode.extensions.getExtension("vscode.ipynb") !== void 0;
    const ext = vscode.extensions.getExtension("sditera.dsworkbench");
    if (!ext) throw new Error("ekstensi sditera.dsworkbench tidak termuat");
    const api = await ext.activate();
    hasil["idPengendali"] = api.idPengendali;
    if (!api.uji) throw new Error("kait uji tidak tersedia (bukan ExtensionMode.Test?)");
    const perintah = await vscode.commands.getCommands(true);
    hasil["perintah"] = perintah.filter((p) => p.startsWith("dsworkbench.")).sort();
    const KUNCI_TEMA = "workbench.colorTheme";
    const sumbangan = ext.packageJSON.contributes?.themes ?? [];
    const terap = async (nama, jenis) => {
      await vscode.workspace.getConfiguration().update(KUNCI_TEMA, nama, vscode.ConfigurationTarget.Global);
      await sampai(() => vscode.window.activeColorTheme.kind === jenis, 15e3, `tema ${nama} tidak diterapkan`);
      return { nama, jenis: vscode.window.activeColorTheme.kind, setelan: vscode.workspace.getConfiguration().get(KUNCI_TEMA) };
    };
    const bermerek = /^\s*DSWorkbench\b/i.test(vscode.env.appName);
    const nilaiGlobal = () => vscode.workspace.getConfiguration().inspect(KUNCI_TEMA)?.globalValue;
    let bawaanDiterapkan = null;
    if (api.uji.temaBawaan) bawaanDiterapkan = await api.uji.temaBawaan();
    else if (bermerek) await sampai(() => nilaiGlobal() !== void 0, 15e3, "").catch(() => void 0);
    const tema = {
      bermerek,
      bawaanDiterapkan,
      nilaiBawaan: vscode.workspace.getConfiguration().inspect(KUNCI_TEMA)?.defaultValue ?? null,
      terdaftar: sumbangan.map((t) => [t.label, t.uiTheme]),
      berkasAda: sumbangan.map((t) => fs.existsSync(path.join(ext.extensionPath, t.path))),
      nilaiAwal: nilaiGlobal() ?? null
    };
    tema["terap"] = [await terap("DSWorkbench Terang", vscode.ColorThemeKind.Light), await terap("DSWorkbench Gelap", vscode.ColorThemeKind.Dark)];
    tema["aktifSetelahTema"] = ext.isActive;
    hasil["tema"] = tema;
    await api.uji.tetapkanSesi(
      { id: "u-uji", username: env2["DSW_USERNAME"], displayName: "Mahasiswa Uji", deviceId: "dev-uji", serverUrl: env2["DSW_SERVER"] },
      env2["DSW_TOKEN"]
    );
    hasil["statusAwal"] = await api.uji.mulaiAgent();
    const mk = await api.uji.siapkanMataKuliah(env2["DSW_COURSE"]);
    hasil["mataKuliah"] = mk;
    hasil["statusSiap"] = api.uji.statusAgent();
    hasil["lingkungan"] = api.uji.lingkungan();
    const uri = vscode.Uri.file(path.join(env2["DSW_WS_COURSE"], env2["DSW_NOTEBOOK"]));
    const doc = await vscode.workspace.openNotebookDocument(uri);
    await vscode.window.showNotebookDocument(doc);
    hasil["jenisNotebook"] = doc.notebookType;
    hasil["jumlahSel"] = doc.cellCount;
    await vscode.commands.executeCommand("notebook.selectKernel", { id: api.idPengendali, extension: "sditera.dsworkbench" });
    await vscode.commands.executeCommand("notebook.execute");
    await sampai(
      () => doc.getCells().every((c) => c.executionSummary?.success !== void 0),
      9e4,
      "sel tidak selesai dalam 90 detik"
    );
    hasil["sel"] = doc.getCells().map((c) => ({
      urutan: c.executionSummary?.executionOrder,
      berhasil: c.executionSummary?.success,
      keluaran: c.outputs.map((o) => ({
        metadata: o.metadata,
        butir: o.items.map((i) => `${i.mime}:${i.mime.startsWith("image/png") ? `${i.data.length}B` : Buffer.from(i.data).toString().slice(0, 80)}`)
      }))
    }));
    hasil["tersimpan"] = await doc.save();
    hasil["berkas"] = JSON.parse(fs.readFileSync(uri.fsPath, "utf8"));
    const uri2 = vscode.Uri.file(path.join(env2["DSW_WS_COURSE"], env2["DSW_NOTEBOOK2"]));
    const doc2 = await vscode.workspace.openNotebookDocument(uri2);
    await vscode.window.showNotebookDocument(doc2);
    await vscode.commands.executeCommand("notebook.selectKernel", { id: api.idPengendali, extension: "sditera.dsworkbench" });
    void vscode.commands.executeCommand("notebook.execute", uri2);
    await sampai(() => doc2.cellAt(0).executionSummary?.success !== void 0, 6e4, "sel pertama notebook kedua tidak selesai");
    const teksSel = (c) => c.outputs.map((o) => o.items.map((i) => Buffer.from(i.data).toString().slice(0, 200)));
    const kedua = {
      sebelum: {
        urutan: doc2.cellAt(0).executionSummary?.executionOrder,
        berhasil: doc2.cellAt(0).executionSummary?.success,
        teks: teksSel(doc2.cellAt(0)).flat().join("")
      }
    };
    await tidur(1500);
    const mulaiInterupsi = Date.now();
    await vscode.commands.executeCommand("notebook.cancelExecution", uri2);
    await sampai(() => doc2.cellAt(1).executionSummary?.success !== void 0, 6e4, "interupsi tidak menghentikan sel");
    kedua["interupsi"] = {
      berhasil: doc2.cellAt(1).executionSummary?.success,
      detik: (Date.now() - mulaiInterupsi) / 1e3,
      keluaran: teksSel(doc2.cellAt(1))
    };
    await vscode.commands.executeCommand("dsworkbench.notebook.restartKernel", uri2);
    const tambah = new vscode.WorkspaceEdit();
    tambah.set(uri2, [vscode.NotebookEdit.insertCells(2, [new vscode.NotebookCellData(vscode.NotebookCellKind.Code, "y", "python")])]);
    await vscode.workspace.applyEdit(tambah);
    await vscode.commands.executeCommand("notebook.cell.execute", { ranges: [{ start: 2, end: 3 }], document: uri2 });
    await sampai(() => doc2.cellAt(2).executionSummary?.success !== void 0, 6e4, "sel setelah mulai ulang tidak selesai");
    kedua["setelahUlang"] = {
      urutan: doc2.cellAt(2).executionSummary?.executionOrder,
      berhasil: doc2.cellAt(2).executionSummary?.success,
      keluaran: teksSel(doc2.cellAt(2))
    };
    hasil["kedua"] = kedua;
    await vscode.commands.executeCommand("workbench.action.files.revert");
    const luar = vscode.Uri.file(env2["DSW_NOTEBOOK_LUAR"]);
    const docLuar = await vscode.workspace.openNotebookDocument(luar);
    await vscode.window.showNotebookDocument(docLuar);
    await vscode.commands.executeCommand("notebook.selectKernel", { id: api.idPengendali, extension: "sditera.dsworkbench" });
    await vscode.commands.executeCommand("notebook.execute");
    await sampai(() => docLuar.cellAt(0).executionSummary?.success !== void 0, 3e4, "sel luar tidak selesai");
    hasil["luar"] = {
      berhasil: docLuar.cellAt(0).executionSummary?.success,
      keluaran: docLuar.cellAt(0).outputs.map((o) => o.items.map((i) => Buffer.from(i.data).toString()))
    };
    const siap = await api.uji.siapkanBerkasModul(env2["DSW_COURSE"], env2["DSW_MODUL2"], true);
    const jalurNyata = (j) => {
      if (!j) return "";
      let n = j;
      try {
        n = fs.realpathSync.native(j);
      } catch {
      }
      return process.platform === "win32" ? n.toLowerCase() : n;
    };
    await sampai(() => jalurNyata(vscode.window.activeNotebookEditor?.notebook.uri.fsPath) === jalurNyata(siap.starter), 2e4, "notebook starter tidak terbuka");
    hasil["siapkan"] = {
      hasil: siap.hasil,
      starter: siap.starter,
      aktif: vscode.window.activeNotebookEditor?.notebook.uri.fsPath,
      jumlahSel: vscode.window.activeNotebookEditor?.notebook.cellCount
    };
    const C = env2["DSW_COURSE"];
    const M2 = env2["DSW_MODUL2"];
    await api.uji.bravais.fokus();
    await sampai(() => api.uji.bravais.keadaan().terpasang, 2e4, "view Bravais tidak terpasang");
    await sampai(() => api.uji.bravais.keadaan().pesanPanel >= 1, 2e4, "").catch(() => void 0);
    const kirimBravais = await api.uji.bravais.pesan({ tindakan: "kirim", teks: "Jelaskan sel yang sedang saya buka." });
    const kb = api.uji.bravais.keadaan();
    hasil["bravais"] = {
      perintahFokus: (await vscode.commands.getCommands(true)).includes("dsworkbench.bravais.focus"),
      kirim: kirimBravais,
      html: kb.html,
      keadaan: kb.keadaan,
      pesanPanel: kb.pesanPanel,
      ditolak: [
        await api.uji.bravais.pesan({ tindakan: "jalankan", perintah: "workbench.action.terminal.new" }),
        await api.uji.bravais.pesan({ tindakan: "sisipkan-kode", pesan: 999, indeks: 0 }),
        await api.uji.bravais.pesan({ tindakan: "kirim", teks: "x", view: { file: "/etc/passwd" } })
      ]
    };
    const naskah = await api.uji.bukaNaskah({ courseId: C, moduleId: M2, judul: "Bacaan Modul 2", namaMataKuliah: "Data Wrangling", kelasId: "k1", lessonBaca: { id: "l-baca", selesai: false } });
    await sampai(() => (api.uji.keadaanNaskah(C, M2)?.pesanPanel ?? 0) >= 1, 2e4, "").catch(() => void 0);
    const tabNaskah = vscode.window.tabGroups.all.flatMap((g) => g.tabs).find((tab) => tab.input instanceof vscode.TabInputWebview && tab.input.viewType.includes("dsworkbench.naskah"));
    hasil["naskah"] = {
      ...naskah,
      tab: tabNaskah?.label,
      pesanPanel: api.uji.keadaanNaskah(C, M2)?.pesanPanel,
      aksi: {
        simpan: await api.uji.pesanNaskah(C, M2, { tindakan: "simpan-berkas", indeks: 0 }),
        simpanLagi: await api.uji.pesanNaskah(C, M2, { tindakan: "simpan-berkas", indeks: 0 }),
        jalankan: await api.uji.pesanNaskah(C, M2, { tindakan: "jalankan-berkas", indeks: 0 }),
        tandai: await api.uji.pesanNaskah(C, M2, { tindakan: "tandai-selesai" }),
        gulir: await api.uji.pesanNaskah(C, M2, { tindakan: "gulir", y: 321 }),
        ditolak: [
          await api.uji.pesanNaskah(C, M2, { tindakan: "jalankan-perintah", perintah: "workbench.action.terminal.new" }),
          await api.uji.pesanNaskah(C, M2, { tindakan: "simpan-berkas", indeks: 7 }),
          await api.uji.pesanNaskah(C, M2, { tindakan: "buka-berkas", indeks: 0, path: "/etc/passwd" })
        ]
      }
    };
    const beranda = await api.uji.beranda();
    hasil["beranda"] = { ...beranda, ditolak: await api.uji.pesanBeranda({ tindakan: "jalankan", perintah: "x" }) };
    const daftarTugas = await api.uji.daftarTugas();
    hasil["tugas"] = daftarTugas;
    hasil["checkpoint"] = await api.uji.checkpoint(env2["DSW_COURSE"], env2["DSW_MODUL2"], true);
    hasil["kumpul"] = await api.uji.kumpulkan(daftarTugas[0]);
    hasil["sqlTanpaLayanan"] = await api.uji.sql(env2["DSW_COURSE"], "sql.execute", { alias: "dw", database: "nusamart_dw", sql: "select 1", maxRows: 10 });
    if (api.uji.gudang && env2["DSW_COURSE_PGD"] && env2["DSW_MODUL_PGD"]) {
      const g = api.uji.gudang;
      const P = env2["DSW_COURSE_PGD"];
      const MP = env2["DSW_MODUL_PGD"];
      const potret = () => {
        const k = g.keadaan();
        return { model: k.model, bagian: k.bagian, sibuk: k.sibuk };
      };
      const berlayanan = await g.berlayanan();
      await g.buka(C);
      const bukanLayanan = g.keadaan().terbuka;
      const mkPgd = await api.uji.siapkanMataKuliah(P);
      fs.mkdirSync(path.join(mkPgd.akar, "modul-02"), { recursive: true });
      fs.writeFileSync(path.join(mkPgd.akar, "modul-02", "01_ddl.sql"), "-- uji panel\nSELECT 1;\n");
      await g.buka(P, MP);
      await sampai(() => g.keadaan().pesanPanel >= 1, 2e4, "").catch(() => void 0);
      await sampai(() => g.keadaan().model?.dataset.jenis === "ada" && g.keadaan().model?.layanan.jenis === "siap", 2e4, "").catch(() => void 0);
      const tab = vscode.window.tabGroups.all.flatMap((x) => x.tabs).find((t) => t.input instanceof vscode.TabInputWebview && t.input.viewType.includes("dsworkbench.gudang"));
      const awal = potret();
      const html = g.keadaan().html;
      const muatSebelumSiap = await g.pesan({ tindakan: "muat-dataset" });
      const nyalakan = await g.pesan({ tindakan: "nyalakan" });
      await sampai(() => g.keadaan().model?.katalog.jenis === "siap", 3e4, "").catch(() => void 0);
      const menyala = potret();
      const siapkan = await g.pesan({ tindakan: "siapkan-dataset" });
      const setelahSiapkan = potret();
      const muat = await g.pesan({ tindakan: "muat-dataset" });
      await sampai(() => (g.keadaan().bagian?.["b-layanan"] ?? "").includes("12.345 baris") && g.keadaan().model?.katalog.jenis === "siap", 2e4, "").catch(() => void 0);
      const setelahMuat = potret();
      const hasilSkema = await g.pesan({ tindakan: "pilih-skema", indeks: 1 });
      const dSkema = g.keadaan().model?.er.diagram;
      const pilihSkema = { hasil: hasilSkema, kotak: dSkema?.kotak.length, luar: dSkema?.kotak.filter((k) => k.luar).map((k) => k.label), garis: dSkema?.garis.length };
      const hasilSaring = await g.pesan({ tindakan: "saring-er", teks: "dim_produk" });
      const saring = { hasil: hasilSaring, nama: g.keadaan().model?.er.diagram?.kotak.map((k) => k.nama) };
      const hasilBuka = await g.pesan({ tindakan: "buka-berkas", indeks: 0 });
      const bukaBerkas = { hasil: hasilBuka, aktif: vscode.window.activeTextEditor?.document.uri.fsPath };
      const bukaBerkasBelumAda = await g.pesan({ tindakan: "buka-berkas", indeks: 1 });
      const ditolak = [
        await g.pesan({ tindakan: "jalankan", perintah: "workbench.action.terminal.new" }),
        await g.pesan({ tindakan: "hentikan", courseId: "lain" }),
        await g.pesan({ tindakan: "siapkan-dataset", moduleId: "module-99" }),
        await g.pesan({ tindakan: "muat-dataset", job: true }),
        await g.pesan({ tindakan: "buka-berkas", indeks: 0, path: "/etc/passwd" }),
        await g.pesan({ tindakan: "buka-berkas", indeks: 99 }),
        await g.pesan({ tindakan: "pratinjau", indeks: 0, sql: "drop table x" }),
        await g.pesan({ tindakan: "pratinjau", indeks: 999 }),
        await g.pesan({ tindakan: "buka-web", indeks: 0, url: "https://jahat.example" }),
        await g.pesan({ tindakan: "pilih-modul", indeks: 7 }),
        await g.pesan("nyalakan")
      ];
      const hasilModul = await g.pesan({ tindakan: "pilih-modul", indeks: 1 });
      const modulTerkunci = { hasil: hasilModul, kerja: g.keadaan().bagian?.["b-kerja"] ?? "", dataset: g.keadaan().bagian?.["b-dataset"] ?? "" };
      hasil["gudang"] = { berlayanan, bukanLayanan, tab: tab?.label, pesanPanel: g.keadaan().pesanPanel, siapPanel: g.keadaan().siapPanel, html, awal, muatSebelumSiap, nyalakan, menyala, siapkan, setelahSiapkan, muat, setelahMuat, pilihSkema, saring, bukaBerkas, bukaBerkasBelumAda, ditolak, modulTerkunci };
    }
    {
      const s = api.uji.sosial;
      await s.fokus();
      await sampai(() => s.keadaan().terpasang, 2e4, "view Sosial tidak terpasang");
      await sampai(() => s.keadaan().pesanPanel >= 1, 2e4, "").catch(() => void 0);
      await sampai(() => s.keadaan().keadaan.keadaan === "siap" && s.keadaan().keadaan.online.length > 0, 2e4, "").catch(() => void 0);
      await sampai(() => Boolean(s.keadaan().keadaan.online[0]?.avatar.uri), 1e4, "").catch(() => void 0);
      const awal = s.keadaan();
      const buka = await s.pesan({ tindakan: "buka", id: "usr-rani" });
      const kirim = await s.pesan({ tindakan: "kirim", id: "usr-rani", teks: "Halo dari IDE" });
      await sampai(() => s.keadaan().lencana === 1, 15e3, "").catch(() => void 0);
      hasil["sosial"] = {
        perintahFokus: (await vscode.commands.getCommands(true)).includes("dsworkbench.sosial.focus"),
        html: awal.html,
        tampak: awal.tampak,
        pesanPanel: s.keadaan().pesanPanel,
        sebelum: { keadaan: awal.keadaan.keadaan, online: awal.keadaan.online, offline: awal.keadaan.offline, lencana: awal.lencana },
        avatarRani: awal.keadaan.online[0]?.avatar.uri,
        akarSkema: awal.akarSkema,
        buka,
        kirim,
        sesudah: s.keadaan(),
        ditolak: [
          await s.pesan({ tindakan: "jalankan", perintah: "workbench.action.terminal.new" }),
          await s.pesan({ tindakan: "kirim", id: "usr-rani", teks: "x", url: "https://jahat.example" }),
          await s.pesan({ tindakan: "kirim", id: "usr-asing", teks: "bukan teman di daftar" }),
          await s.pesan({ tindakan: "buka-tautan", id: "smsg-9999", indeks: 0 })
        ]
      };
    }
    {
      const su = api.uji.suara;
      const c = vscode.workspace.getConfiguration("dsworkbench");
      const G = vscode.ConfigurationTarget.Global;
      const awal = su.pengaturan();
      const terkirim = su.terkirim();
      await c.update("sound.enabled", false, G);
      const mati = su.pengaturan();
      const klikMati = await api.uji.bravais.pesan({ tindakan: "konteks", aktif: true });
      const terkirimMati = su.terkirim().length - terkirim.length;
      await c.update("sound.enabled", true, G);
      await c.update("sound.volume", 0.8, G);
      const nyala = su.pengaturan();
      await c.update("sound.enabled", void 0, G);
      await c.update("sound.volume", void 0, G);
      hasil["suara"] = { awal, terkirim, mati, klikMati, terkirimMati, nyala, akhir: su.pengaturan(), htmlSosial: api.uji.sosial.keadaan().html };
    }
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    await tidur(1500);
    hasil["statusAkhir"] = api.uji.statusAgent();
    hasil["tema"]["akhir"] = {
      setelan: vscode.workspace.getConfiguration().get("workbench.colorTheme"),
      jenis: vscode.window.activeColorTheme.kind,
      aktif: ext.isActive
    };
    hasil["pembaruan"] = {
      didukung: api.uji.pembaruan !== void 0,
      versiAplikasi: api.uji.pembaruan?.versiAplikasi?.() ?? null,
      pasang: api.uji.pembaruan && env2["DSW_UJI_VSIX"] ? await api.uji.pembaruan.pasangVsix(env2["DSW_UJI_VSIX"]) : null
    };
  } catch (e) {
    hasil["galat"] = e instanceof Error ? e.stack ?? e.message : String(e);
  }
  fs.writeFileSync(env2["DSW_OUT"], JSON.stringify(hasil, null, 1));
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  run
});
