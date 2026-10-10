// Penulis ZIP minimal (deflate, tanpa ZIP64) dengan pustaka standar Node.
// Deterministik: cap waktu tetap, urutan entri = urutan masukan.
import { deflateRawSync } from 'node:zlib';

const TABEL = (() => {
	const t = new Uint32Array(256);
	for (let n = 0; n < 256; n++) {
		let c = n;
		for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
		t[n] = c >>> 0;
	}
	return t;
})();

export function crc32(buf) {
	let c = 0xffffffff;
	for (let i = 0; i < buf.length; i++) c = TABEL[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
	return (c ^ 0xffffffff) >>> 0;
}

// 2026-01-01 00:00:00 (format DOS), sama dengan cap waktu ZIP agent di server.
const WAKTU_DOS = 0;
const TANGGAL_DOS = ((2026 - 1980) << 9) | (1 << 5) | 1;

/** `entri`: [{ nama (POSIX), isi: Buffer, mode? }] → Buffer ZIP. */
export function buatZip(entri) {
	const bagian = [];
	const pusat = [];
	let ofset = 0;
	for (const e of entri) {
		const nama = Buffer.from(e.nama, 'utf8');
		const kempis = deflateRawSync(e.isi, { level: 9 });
		const pakaiKempis = kempis.length < e.isi.length;
		const data = pakaiKempis ? kempis : e.isi;
		const crc = crc32(e.isi);
		if (e.isi.length > 0xfffffffe || ofset > 0xfffffffe) throw new Error('berkas terlalu besar untuk ZIP tanpa ZIP64');
		const lokal = Buffer.alloc(30);
		lokal.writeUInt32LE(0x04034b50, 0);
		lokal.writeUInt16LE(20, 4);
		lokal.writeUInt16LE(0x0800, 6); // nama UTF-8
		lokal.writeUInt16LE(pakaiKempis ? 8 : 0, 8);
		lokal.writeUInt16LE(WAKTU_DOS, 10);
		lokal.writeUInt16LE(TANGGAL_DOS, 12);
		lokal.writeUInt32LE(crc, 14);
		lokal.writeUInt32LE(data.length, 18);
		lokal.writeUInt32LE(e.isi.length, 22);
		lokal.writeUInt16LE(nama.length, 26);
		lokal.writeUInt16LE(0, 28);
		bagian.push(lokal, nama, data);
		const c = Buffer.alloc(46);
		c.writeUInt32LE(0x02014b50, 0);
		c.writeUInt16LE((3 << 8) | 20, 4); // dibuat di Unix
		c.writeUInt16LE(20, 6);
		c.writeUInt16LE(0x0800, 8);
		c.writeUInt16LE(pakaiKempis ? 8 : 0, 10);
		c.writeUInt16LE(WAKTU_DOS, 12);
		c.writeUInt16LE(TANGGAL_DOS, 14);
		c.writeUInt32LE(crc, 16);
		c.writeUInt32LE(data.length, 20);
		c.writeUInt32LE(e.isi.length, 24);
		c.writeUInt16LE(nama.length, 28);
		c.writeUInt32LE((((0o100000 | (e.mode ?? 0o644)) << 16) >>> 0), 38);
		c.writeUInt32LE(ofset, 42);
		pusat.push(c, nama);
		ofset += 30 + nama.length + data.length;
	}
	if (entri.length > 0xfffe) throw new Error('terlalu banyak entri untuk ZIP tanpa ZIP64');
	const ukuranPusat = pusat.reduce((a, b) => a + b.length, 0);
	const akhir = Buffer.alloc(22);
	akhir.writeUInt32LE(0x06054b50, 0);
	akhir.writeUInt16LE(entri.length, 8);
	akhir.writeUInt16LE(entri.length, 10);
	akhir.writeUInt32LE(ukuranPusat, 12);
	akhir.writeUInt32LE(ofset, 16);
	return Buffer.concat([...bagian, ...pusat, akhir]);
}
