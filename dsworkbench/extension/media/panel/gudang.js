// Panel "Pergudangan Data": mengganti bagian yang berubah (HTML buatan ekstensi, semua
// teks sudah di-escape di sana), meneruskan pilihan <select> dan saringan sebagai tindakan
// tetap, dan menggeser/memperbesar diagram ER. Tidak ada jaringan dan tidak ada token:
// satu-satunya jalur keluar adalah postMessage lewat window.dsw.kirim.
(function () {
	'use strict';
	const dsw = window.dsw;
	const PILIH = new Set(['pilih-modul', 'pilih-target', 'pilih-skema']);
	const BAGIAN = new Set(['b-kepala', 'b-layanan', 'b-dataset', 'b-kerja', 'b-jelajah', 'b-er', 'b-web']);
	const galat = [];

	// -- diagram ER: geser, perbesar, sorot ------------------------------------------------
	// Tampilan (viewBox) dan tabel terpilih diingat per isi diagram (`data-kunci`).
	const er = { kunci: '', vb: null, pilih: -1, seret: null };

	const svg = () => document.getElementById('er-svg');
	const ukuran = (s) => ({ w: Number(s.getAttribute('data-lebar')) || 1, h: Number(s.getAttribute('data-tinggi')) || 1 });

	function terapkan(s) {
		if (!er.vb) return;
		s.setAttribute('viewBox', [er.vb.x, er.vb.y, er.vb.w, er.vb.h].map((n) => Math.round(n * 100) / 100).join(' '));
	}

	function paskan(s) {
		const u = ukuran(s);
		er.vb = { x: 0, y: 0, w: u.w, h: u.h };
		terapkan(s);
	}

	// Tampilan awal: seluruh diagram bila masih terbaca; skema besar dibuka pada ukuran yang
	// terbaca (≥ 70%) di tengah diagram, tempat tabel paling terhubung berada. "Paskan" tetap
	// menampilkan semuanya.
	const SKALA_TERBACA = 0.7;
	function tampilanAwal(s) {
		const u = ukuran(s);
		const r = s.getBoundingClientRect();
		const pas = r.width > 0 && r.height > 0 ? Math.min(r.width / u.w, r.height / u.h) : 1;
		if (pas >= SKALA_TERBACA) return paskan(s);
		const w = r.width / SKALA_TERBACA;
		const h = r.height / SKALA_TERBACA;
		er.vb = { x: u.w / 2 - w / 2, y: u.h / 2 - h / 2, w, h };
		terapkan(s);
	}

	function zum(s, faktor, pusat) {
		if (!er.vb) return;
		const u = ukuran(s);
		// Batas: 8× lebih dekat sampai 2× lebih jauh dari seluruh diagram.
		const w = Math.min(u.w * 2, Math.max(u.w / 8, er.vb.w * faktor));
		const k = w / er.vb.w;
		const cx = pusat ? pusat.x : er.vb.x + er.vb.w / 2;
		const cy = pusat ? pusat.y : er.vb.y + er.vb.h / 2;
		er.vb = { x: cx - (cx - er.vb.x) * k, y: cy - (cy - er.vb.y) * k, w, h: er.vb.h * k };
		terapkan(s);
	}

	function geser(s, dx, dy) {
		if (!er.vb) return;
		er.vb.x += dx;
		er.vb.y += dy;
		terapkan(s);
	}

	/** Titik layar → koordinat diagram. */
	function titik(s, e) {
		const r = s.getBoundingClientRect();
		if (!er.vb || r.width === 0 || r.height === 0) return null;
		// preserveAspectRatio xMidYMid meet: skala seragam, diagram di tengah.
		const skala = Math.max(er.vb.w / r.width, er.vb.h / r.height);
		const x0 = er.vb.x + er.vb.w / 2 - (r.width * skala) / 2;
		const y0 = er.vb.y + er.vb.h / 2 - (r.height * skala) / 2;
		return { x: x0 + (e.clientX - r.left) * skala, y: y0 + (e.clientY - r.top) * skala, skala };
	}

	function sorot(n) {
		const s = svg();
		if (!s) return;
		er.pilih = n;
		const info = document.getElementById('er-info');
		const tabel = [...s.querySelectorAll('.er-tabel')];
		const garis = [...s.querySelectorAll('.er-garis')];
		const terpilih = tabel.find((t) => t.getAttribute('data-kotak') === String(n));
		const tetangga = new Set();
		for (const g of garis) {
			const a = g.getAttribute('data-dari');
			const b = g.getAttribute('data-ke');
			const aktif = Boolean(terpilih) && (a === String(n) || b === String(n));
			g.classList.toggle('er-garis--aktif', aktif);
			if (aktif) {
				tetangga.add(a);
				tetangga.add(b);
			}
		}
		for (const t of tabel) {
			const k = t.getAttribute('data-kotak');
			t.classList.toggle('er-tabel--pilih', t === terpilih);
			t.classList.toggle('er-tabel--tetangga', Boolean(terpilih) && t !== terpilih && tetangga.has(k));
			t.setAttribute('aria-pressed', String(t === terpilih));
		}
		s.classList.toggle('er--memilih', Boolean(terpilih));
		if (!info) return;
		if (!terpilih) {
			er.pilih = -1;
			info.hidden = true;
			return;
		}
		// Teks dari atribut buatan ekstensi, dipasang sebagai teks (bukan HTML).
		document.getElementById('er-info-teks').textContent = terpilih.getAttribute('aria-label') || '';
		const pratinjau = document.getElementById('er-pratinjau');
		if (pratinjau) pratinjau.setAttribute('data-indeks', terpilih.getAttribute('data-indeks') || '0');
		info.hidden = false;
	}

	function pasangEr() {
		const s = svg();
		if (!s) {
			er.kunci = '';
			return;
		}
		const kunci = s.getAttribute('data-kunci') || '';
		if (kunci === er.kunci && er.vb) {
			terapkan(s);
			sorot(er.pilih);
		} else {
			er.kunci = kunci;
			er.pilih = -1;
			tampilanAwal(s);
		}
	}

	document.addEventListener(
		'wheel',
		(e) => {
			const s = svg();
			if (!s || !(e.target instanceof Element) || !e.target.closest('#er-kanvas')) return;
			// Gulir biasa tetap menggulir panel; perbesar hanya dengan Ctrl/⌘ (cubit di trackpad).
			if (!e.ctrlKey && !e.metaKey) return;
			e.preventDefault();
			zum(s, e.deltaY > 0 ? 1.12 : 1 / 1.12, titik(s, e));
		},
		{ passive: false },
	);

	document.addEventListener('pointerdown', (e) => {
		const s = svg();
		const kanvas = e.target instanceof Element ? e.target.closest('#er-kanvas') : null;
		if (!s || !kanvas || e.button !== 0) return;
		er.seret = { x: e.clientX, y: e.clientY, jauh: 0, tabel: e.target.closest('.er-tabel'), id: e.pointerId, kanvas };
	});
	document.addEventListener('pointermove', (e) => {
		const s = svg();
		const d = er.seret;
		if (!s || !d || e.pointerId !== d.id) return;
		const p = titik(s, e);
		if (!p) return;
		const dx = e.clientX - d.x;
		const dy = e.clientY - d.y;
		d.jauh += Math.abs(dx) + Math.abs(dy);
		if (d.jauh > 4) d.kanvas.classList.add('er-kanvas--seret');
		d.x = e.clientX;
		d.y = e.clientY;
		geser(s, -dx * p.skala, -dy * p.skala);
	});
	const lepas = (e) => {
		const d = er.seret;
		if (!d || (e.pointerId !== undefined && e.pointerId !== d.id)) return;
		er.seret = null;
		d.kanvas.classList.remove('er-kanvas--seret');
		// Klik (bukan seret): sorot tabel; klik tabel yang sama atau latar melepas sorotan.
		if (d.jauh <= 4 && e.type === 'pointerup') {
			const n = d.tabel ? Number(d.tabel.getAttribute('data-kotak')) : -1;
			sorot(n === er.pilih ? -1 : n);
		}
	};
	document.addEventListener('pointerup', lepas);
	document.addEventListener('pointercancel', lepas);

	document.addEventListener('keydown', (e) => {
		const s = svg();
		if (!s || !(e.target instanceof Element) || !e.target.closest('#er-kanvas')) return;
		const tabel = e.target.closest('.er-tabel');
		if (tabel && (e.key === 'Enter' || e.key === ' ')) {
			e.preventDefault();
			const n = Number(tabel.getAttribute('data-kotak'));
			sorot(n === er.pilih ? -1 : n);
			return;
		}
		if (!er.vb) return;
		const langkah = er.vb.w / 10;
		const peta = { ArrowLeft: [-langkah, 0], ArrowRight: [langkah, 0], ArrowUp: [0, -langkah], ArrowDown: [0, langkah] };
		if (peta[e.key]) {
			e.preventDefault();
			geser(s, peta[e.key][0], peta[e.key][1]);
		} else if (e.key === '+' || e.key === '=') {
			e.preventDefault();
			zum(s, 1 / 1.25);
		} else if (e.key === '-' || e.key === '_') {
			e.preventDefault();
			zum(s, 1.25);
		} else if (e.key === '0') {
			e.preventDefault();
			paskan(s);
		} else if (e.key === 'Escape') {
			sorot(-1);
		}
	});

	// Tombol lokal (tidak dikirim ke ekstensi): perbesaran dan lepas sorotan.
	window.addEventListener('dsw-tindakan', (e) => {
		const s = svg();
		const t = e.detail && e.detail.pesan ? e.detail.pesan.tindakan : '';
		if (!s) return;
		if (t === 'er-besar') zum(s, 1 / 1.25);
		else if (t === 'er-kecil') zum(s, 1.25);
		else if (t === 'er-pas') paskan(s);
		else if (t === 'er-lepas') sorot(-1);
	});

	// -- pilihan dan saringan -----------------------------------------------------------------
	document.addEventListener('change', (e) => {
		const el = e.target;
		if (!(el instanceof HTMLSelectElement)) return;
		const tindakan = el.getAttribute('data-pilih');
		if (!tindakan || !PILIH.has(tindakan)) return;
		const indeks = Number(el.value);
		if (Number.isInteger(indeks) && indeks >= 0) dsw.kirim({ tindakan, indeks });
	});

	let pewaktuSaring;
	document.addEventListener('input', (e) => {
		const el = e.target;
		if (!(el instanceof HTMLInputElement) || el.id !== 'er-saring') return;
		clearTimeout(pewaktuSaring);
		pewaktuSaring = setTimeout(() => dsw.kirim({ tindakan: 'saring-er', teks: String(el.value).slice(0, 60) }), 350);
	});

	document.addEventListener('click', (e) => {
		const a = e.target instanceof Element ? e.target.closest('a[data-lompat]') : null;
		if (!a) return;
		e.preventDefault();
		const el = document.getElementById(a.getAttribute('data-lompat'));
		if (el) el.scrollIntoView({ behavior: dsw.tenang() ? 'auto' : 'smooth', block: 'start' });
	});

	// -- bagian yang berubah --------------------------------------------------------------------
	function ganti(id, html) {
		const wadah = document.getElementById(id);
		if (!wadah || typeof html !== 'string') return;
		// Fokus dan kursor dipertahankan bila elemen berfokus ada di bagian yang diganti.
		const aktif = document.activeElement;
		const diDalam = aktif instanceof HTMLElement && wadah.contains(aktif);
		const idAktif = diDalam ? aktif.id : '';
		const tindakanAktif = diDalam ? aktif.getAttribute('data-tindakan') : null;
		const kursor = diDalam && aktif instanceof HTMLInputElement ? [aktif.selectionStart, aktif.selectionEnd] : null;
		const terbuka = [...wadah.querySelectorAll('details')].map((d) => d.open);
		// HTML buatan ekstensi (nama tabel, kolom, pesan, log sudah di-escape di sana); CSP melarang skrip sebaris.
		wadah.innerHTML = html;
		[...wadah.querySelectorAll('details')].forEach((d, i) => {
			if (terbuka[i]) d.open = true;
		});
		if (diDalam) {
			const baru = (idAktif && document.getElementById(idAktif)) || (tindakanAktif && wadah.querySelector('[data-tindakan="' + tindakanAktif.replace(/[^a-z-]/g, '') + '"]:not(:disabled)'));
			if (baru instanceof HTMLElement) {
				baru.focus({ preventScroll: true });
				if (kursor && baru instanceof HTMLInputElement) {
					try {
						baru.setSelectionRange(kursor[0], kursor[1]);
					} catch {
						// jenis input tanpa rentang pilihan
					}
				}
			}
		}
		if (id === 'b-er') pasangEr();
	}

	window.addEventListener('message', (e) => {
		const m = e.data;
		if (!m || typeof m !== 'object' || m.jenis !== 'bagian' || !m.isi || typeof m.isi !== 'object') return;
		try {
			for (const id of Object.keys(m.isi)) if (BAGIAN.has(id)) ganti(id, m.isi[id]);
		} catch (x) {
			galat.push(String(x && x.message ? x.message : x));
		}
	});

	// Diagnostik (uji): keadaan DOM diagram, tanpa cara mengubah apa pun.
	window.dswGudang = {
		keadaan: () => {
			const s = svg();
			return {
				bagian: document.querySelectorAll('[data-bagian]').length,
				langkah: document.querySelectorAll('.langkah').length,
				tabel: s ? s.querySelectorAll('.er-tabel').length : 0,
				garis: s ? s.querySelectorAll('.er-garis').length : 0,
				garisAktif: s ? s.querySelectorAll('.er-garis--aktif').length : 0,
				viewBox: s ? s.getAttribute('viewBox') : null,
				pilih: er.pilih,
				infoTampak: Boolean(document.getElementById('er-info') && !document.getElementById('er-info').hidden),
				galat: galat.slice(),
			};
		},
		// Untuk uji tampilan: sama dengan klik tabel / tombol perbesar (tidak mengirim apa pun ke ekstensi).
		sorot: (n) => sorot(Number(n)),
		zum: (f) => {
			const s = svg();
			if (s) zum(s, Number(f) || 1);
		},
	};

	pasangEr();
	dsw.kirim({ tindakan: 'siap' });
})();
