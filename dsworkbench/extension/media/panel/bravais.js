// Panel Bravais: menggambar keadaan kiriman ekstensi (percakapan, riwayat, konteks)
// dan mengirim tindakan tetap. Jawaban asisten datang sebagai HTML yang sudah
// dirender aman oleh ekstensi; teks lain dipasang lewat textContent.
(function () {
	'use strict';
	const dsw = window.dsw;
	const el = (id) => document.getElementById(id);
	const daftar = el('daftar');
	const riwayat = el('riwayat');
	const komposer = el('komposer');
	const masukan = el('masukan');
	const tombolKirim = el('kirim');
	const tombolRiwayat = el('tombol-riwayat');
	let keadaan = null;
	let tandaAkhir = '';

	function buat(tag, kelas, teks) {
		const e = document.createElement(tag);
		if (kelas) e.className = kelas;
		if (teks !== undefined) e.textContent = teks;
		return e;
	}

	function tombol(teks, tindakan, atribut, kelas) {
		const b = buat('button', kelas || 'tombol tombol--halus', teks);
		b.type = 'button';
		b.setAttribute('data-tindakan', tindakan);
		for (const [k, v] of Object.entries(atribut || {})) b.setAttribute(k, String(v));
		return b;
	}

	function kartu(judul, isi, aksi) {
		const k = buat('div', 'kartu sambutan');
		if (judul) k.appendChild(buat('p', '', judul)).style.fontWeight = '600';
		if (isi) k.appendChild(buat('p', 'redup', isi));
		for (const a of aksi || []) k.appendChild(a);
		return k;
	}

	function gelembung(p, nama) {
		const g = buat('div', `gelembung gelembung--${p.peran}${p.galat ? ' gelembung--galat' : ''}`);
		g.setAttribute('data-pesan', String(p.id));
		g.appendChild(buat('span', 'hanya-pembaca-layar', p.peran === 'user' ? 'Anda: ' : `${nama}: `));
		if (typeof p.html === 'string') {
			// HTML buatan perender ekstensi (semua teks sudah di-escape di sana).
			const isi = buat('div');
			isi.innerHTML = p.html;
			dsw.terapkanGaya(isi);
			g.appendChild(isi);
		} else {
			g.appendChild(buat('span', '', p.teks || ''));
		}
		if (p.peran === 'assistant' && !p.galat) {
			if (p.sumber.length > 0) {
				const s = buat('div', 'gelembung__sumber');
				s.appendChild(buat('span', 'redup', 'Sumber:'));
				p.sumber.forEach((judul, i) => s.appendChild(tombol(judul, 'buka-sumber', { 'data-indeks': i, 'data-tanpa-jeda': '' }, 'tautan-tombol')));
				g.appendChild(s);
			}
			const kaki = buat('div', 'gelembung__kaki');
			if (p.catatan) kaki.appendChild(buat('span', '', p.catatan));
			p.aksi.forEach((label) => kaki.appendChild(tombol(label, 'tanya-lanjut')));
			kaki.appendChild(tombol('Salin jawaban', 'salin'));
			g.appendChild(kaki);
		}
		return g;
	}

	function gambarRiwayat(k) {
		const buka = k.riwayat !== null;
		riwayat.hidden = !buka;
		daftar.hidden = buka;
		tombolRiwayat.textContent = buka ? 'Tutup' : 'Riwayat';
		tombolRiwayat.setAttribute('data-tindakan', buka ? 'tutup-riwayat' : 'riwayat');
		tombolRiwayat.setAttribute('aria-expanded', String(buka));
		riwayat.replaceChildren();
		if (!buka) return;
		riwayat.appendChild(buat('h2', '', 'Riwayat percakapan'));
		if (k.galatRiwayat) riwayat.appendChild(buat('p', 'galat', k.galatRiwayat));
		if (k.riwayat.length === 0 && !k.galatRiwayat) {
			riwayat.appendChild(buat('p', 'redup', `Belum ada percakapan tersimpan. Percakapan dengan ${k.nama} tersimpan di sini dan bisa dilanjutkan kapan saja.`));
		}
		const ul = buat('ul');
		for (const c of k.riwayat) {
			const li = buat('li', c.aktif ? 'aktif' : '');
			const b = buat('button', 'riwayat__buka');
			b.type = 'button';
			b.setAttribute('data-tindakan', 'buka-obrolan');
			b.setAttribute('data-id', c.id);
			b.appendChild(buat('span', 'riwayat__judul', c.judul));
			b.appendChild(buat('span', 'riwayat__ket', c.keterangan));
			li.appendChild(b);
			const hapus = tombol('Hapus', 'hapus-obrolan', { 'data-id': c.id, 'aria-label': `Hapus percakapan ${c.judul}` });
			li.appendChild(hapus);
			ul.appendChild(li);
		}
		riwayat.appendChild(ul);
		riwayat.appendChild(buat('p', 'redup', 'Yang tersimpan hanya isi percakapan. Isi sel, kueri, dan galat yang dilampirkan sebagai konteks tidak disimpan.'));
	}

	function perbaruiKirim() {
		tombolKirim.disabled = !keadaan || keadaan.sibuk || masukan.value.trim() === '';
	}

	function gambar(k) {
		keadaan = k;
		el('nama').textContent = k.nama;
		el('judul').textContent = k.judul || 'Asisten belajar';
		el('judul').title = k.judul || '';
		masukan.placeholder = `Tanya ${k.nama} tentang yang sedang Anda kerjakan…`;
		masukan.setAttribute('aria-label', `Pertanyaan untuk ${k.nama}`);
		daftar.setAttribute('aria-label', `Percakapan dengan ${k.nama}`);
		gambarRiwayat(k);

		const bisaTanya = k.masuk && k.asisten && !k.galatKonfigurasi;
		komposer.hidden = !bisaTanya || k.riwayat !== null;
		tombolRiwayat.hidden = !k.masuk;

		daftar.replaceChildren();
		if (!k.masuk) {
			daftar.appendChild(kartu(`Halo, saya ${k.nama}.`, 'Masuk ke DSWorkbench untuk bertanya.', [tombol('Masuk', 'masuk', {}, 'tombol tombol--utama')]));
		} else if (k.galatKonfigurasi) {
			// Fitur dimatikan / tidak tersedia: pesan server apa adanya.
			daftar.appendChild(kartu('', k.galatKonfigurasi, [tombol('Coba lagi', 'muat-ulang')]));
		} else if (!k.asisten) {
			daftar.appendChild(kartu('Asisten AI belum aktif', 'Admin belum mengonfigurasi layanan AI.'));
		} else if (k.pesan.length === 0) {
			const sambut = kartu(
				`Halo, saya ${k.nama}.`,
				'Tanyakan apa saja tentang yang sedang Anda kerjakan: konsep di modul ini, strategi mengerjakan dan cara mengumpulkan tugas, atau galat yang muncul. Untuk tugas yang dinilai, saya membimbing, bukan memberikan jawaban final. Saya tidak mengubah berkas Anda.',
			);
			k.saran.forEach((s, i) => sambut.appendChild(tombol(s, 'saran', { 'data-indeks': i })));
			daftar.appendChild(sambut);
		}
		for (const p of k.pesan) daftar.appendChild(gelembung(p, k.nama));
		if (k.sibuk) {
			const g = buat('div', 'gelembung gelembung--assistant berpikir');
			g.setAttribute('role', 'status');
			const titik = buat('span', 'titik');
			titik.setAttribute('aria-hidden', 'true');
			titik.append(buat('i'), buat('i'), buat('i'));
			g.append(titik, buat('span', '', `${k.nama} sedang berpikir…`));
			daftar.appendChild(g);
		}
		const tanda = `${k.pesan.length}:${k.sibuk}:${k.pesan.length ? k.pesan[k.pesan.length - 1].id : 0}`;
		if (tanda !== tandaAkhir) {
			tandaAkhir = tanda;
			daftar.scrollTop = daftar.scrollHeight;
		}

		el('konteks-teks').textContent = k.konteks.label;
		el('konteks-teks').title = k.konteks.label;
		el('konteks-aktif').checked = k.konteks.aktif;
		el('konteks').classList.toggle('mati', !k.konteks.aktif);
		el('cari-web').hidden = !k.cariWeb.tersedia;
		el('cari-web-aktif').checked = k.cariWeb.aktif;
		perbaruiKirim();
	}

	function kirim() {
		const teks = masukan.value;
		if (!keadaan || keadaan.sibuk || teks.trim() === '') return;
		dsw.kirim({ tindakan: 'kirim', teks: teks.slice(0, 2000) });
		masukan.value = '';
		dsw.simpanKeadaan({ ...dsw.keadaan(), draf: '' });
		perbaruiKirim();
	}

	tombolKirim.addEventListener('click', kirim);
	masukan.addEventListener('keydown', (e) => {
		// Enter mengirim, Shift+Enter baris baru; Enter saat menyusun aksara (IME) dibiarkan.
		if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
			e.preventDefault();
			kirim();
		}
	});
	masukan.addEventListener('input', () => {
		dsw.simpanKeadaan({ ...dsw.keadaan(), draf: masukan.value });
		perbaruiKirim();
	});
	el('konteks-aktif').addEventListener('change', (e) => dsw.kirim({ tindakan: 'konteks', aktif: Boolean(e.target.checked) }));
	el('cari-web-aktif').addEventListener('change', (e) => dsw.kirim({ tindakan: 'cari-web', aktif: Boolean(e.target.checked) }));
	document.addEventListener('keydown', (e) => {
		if (e.key === 'Escape' && keadaan && keadaan.riwayat !== null) dsw.kirim({ tindakan: 'tutup-riwayat' });
	});

	// -- karakter Bravais (Lottie; gambar diam bila "kurangi gerak" atau gagal) -----
	let animasi = null;
	let dataAnimasi = null;
	const tokoh = el('tokoh');
	const hentikanAnimasi = () => {
		try {
			animasi?.destroy();
		} catch {
			// abaikan
		}
		animasi = null;
		tokoh.classList.remove('hidup');
		el('animasi').replaceChildren();
	};
	function mulaiAnimasi() {
		if (animasi || !dataAnimasi || !window.lottie || dsw.tenang()) return;
		try {
			animasi = window.lottie.loadAnimation({
				container: el('animasi'),
				renderer: 'svg',
				loop: true,
				autoplay: !document.hidden,
				// lottie-web mengubah objek datanya: beri salinan.
				animationData: JSON.parse(JSON.stringify(dataAnimasi)),
				// Kotak isi gabungan seluruh frame, sama dengan gambar diamnya (HeaderCharacter web).
				rendererSettings: { viewBoxOnly: true, viewBoxSize: '259 249 1082 1082', preserveAspectRatio: 'xMidYMid meet' },
			});
			animasi.setSubframe(false);
			animasi.addEventListener('DOMLoaded', () => tokoh.classList.add('hidup'));
			animasi.addEventListener('data_failed', hentikanAnimasi);
			animasi.addEventListener('error', hentikanAnimasi);
		} catch {
			hentikanAnimasi();
		}
	}
	// Animasi berhenti saat panel tersembunyi dan mengikuti pengaturan "kurangi gerak".
	document.addEventListener('visibilitychange', () => {
		document.body.classList.toggle('tersembunyi', document.hidden);
		if (!animasi) return;
		if (document.hidden) animasi.pause();
		else animasi.play();
	});
	window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', () => {
		if (dsw.tenang()) hentikanAnimasi();
		else mulaiAnimasi();
	});

	window.addEventListener('message', (e) => {
		const m = e.data;
		if (!m || typeof m !== 'object') return;
		if (m.jenis === 'keadaan') gambar(m);
		else if (m.jenis === 'animasi' && m.data && typeof m.data === 'object') {
			dataAnimasi = m.data;
			mulaiAnimasi();
		} else if (m.jenis === 'fokus') masukan.focus();
	});

	const draf = dsw.keadaan().draf;
	if (typeof draf === 'string') masukan.value = draf;
	perbaruiKirim();
	dsw.kirim({ tindakan: 'siap' });
})();
