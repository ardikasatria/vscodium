// Panel "Studio Data": mengganti bagian yang berubah (HTML buatan ekstensi, semua teks
// sudah di-escape di sana), mengirim kueri dari jendela kueri, berpindah tab keluaran,
// dan mengisi jendela kueri dengan skrip buatan ekstensi. Tidak ada jaringan dan tidak
// ada token: satu-satunya jalur keluar adalah postMessage lewat window.dsw.kirim.
(function () {
	'use strict';
	const dsw = window.dsw;
	const BAGIAN = new Set(['s-alat', 's-jelajah', 's-hasil', 's-status']);
	const TAB = new Set(['hasil', 'pesan', 'rencana']);
	const BER_SQL = new Set(['jalankan', 'rencana', 'rencana-aktual']);
	const SQL_MAKS = 100000;
	const galat = [];
	// Tab keluaran yang dipilih pengguna diingat selama hasilnya sama (`data-urut`).
	const tampil = { urut: '', tab: '' };

	const editor = () => document.getElementById('st-sql');

	/** Teks terpilih di jendela kueri bila ada, kalau tidak seluruh isinya. */
	function kueri() {
		const el = editor();
		if (!(el instanceof HTMLTextAreaElement)) return '';
		const pilihan = el.selectionStart !== el.selectionEnd ? el.value.slice(el.selectionStart, el.selectionEnd) : '';
		return (pilihan.trim() ? pilihan : el.value).slice(0, SQL_MAKS);
	}

	function tombolAktif(tindakan) {
		const t = document.querySelector('#s-alat [data-tindakan="' + tindakan + '"]');
		return t instanceof HTMLButtonElement && !t.disabled;
	}

	function kirimSql(tindakan) {
		if (!BER_SQL.has(tindakan) || !tombolAktif(tindakan)) return false;
		const sql = kueri();
		if (!sql.trim()) {
			const el = editor();
			if (el) el.focus();
			return false;
		}
		dsw.kirim({ tindakan, sql });
		return true;
	}

	// -- batang porsi rencana: lebar lewat CSSOM (CSP melarang gaya sebaris) -------------------
	function pasangRencana(akar) {
		for (const el of akar.querySelectorAll('.rc__isi[data-porsi]')) {
			const n = Number(el.getAttribute('data-porsi'));
			if (Number.isFinite(n)) el.style.width = Math.max(0, Math.min(100, n)) + '%';
		}
	}

	// -- tab keluaran ----------------------------------------------------------------------------
	function pilihTab(nama, fokus) {
		const wadah = document.querySelector('.st-hasil[data-urut]');
		if (!wadah || !TAB.has(nama)) return;
		for (const t of wadah.querySelectorAll('[role="tab"]')) {
			const aktif = t.getAttribute('data-tab') === nama;
			t.setAttribute('aria-selected', String(aktif));
			if (aktif) t.removeAttribute('tabindex');
			else t.setAttribute('tabindex', '-1');
			if (aktif && fokus) t.focus({ preventScroll: true });
		}
		for (const p of wadah.querySelectorAll('[data-pane]')) p.hidden = p.getAttribute('data-pane') !== nama;
		tampil.tab = nama;
	}

	function pasangHasil() {
		const wadah = document.querySelector('.st-hasil[data-urut]');
		if (!wadah) return;
		pasangRencana(wadah);
		const urut = wadah.getAttribute('data-urut') || '';
		const saran = wadah.getAttribute('data-tab') || 'hasil';
		// Hasil yang sama digambar ulang (mis. dari "berjalan" ke "selesai" punya urut sama): ikuti
		// saran ekstensi saat hasilnya tiba; pilihan pengguna dipertahankan hanya bila tidak ada perubahan saran.
		const kunci = urut + ':' + saran;
		if (kunci !== tampil.urut) {
			tampil.urut = kunci;
			pilihTab(saran, false);
		} else if (tampil.tab) pilihTab(tampil.tab, false);
	}

	window.addEventListener('dsw-tindakan', (e) => {
		const d = e.detail || {};
		const t = d.pesan ? d.pesan.tindakan : '';
		if (t === 'tab' && d.sasaran) pilihTab(d.sasaran.getAttribute('data-tab') || '', false);
		else if (t === 'kueri-baru') {
			const el = editor();
			if (el instanceof HTMLTextAreaElement) {
				el.value = '';
				el.focus();
			}
		} else if (BER_SQL.has(t)) kirimSql(t);
	});

	// -- papan ketik -----------------------------------------------------------------------------
	document.addEventListener('keydown', (e) => {
		const el = e.target;
		const mod = e.ctrlKey || e.metaKey;
		// Pintasan (F5, Ctrl/⌘+Enter, Ctrl/⌘+L, Ctrl+M) didaftarkan ekstensi sebagai keybinding aplikasi dan tiba
		// sebagai pesan `perintah`; di sini hanya dicegah agar tidak menyisipkan baris baru atau memuat ulang.
		if (e.key === 'F5' || (mod && e.key === 'Enter')) {
			e.preventDefault();
			return;
		}
		// Tab di jendela kueri menyisipkan tabulasi; Esc melepas fokus agar Tab kembali berpindah elemen.
		if (el instanceof HTMLTextAreaElement && el.id === 'st-sql') {
			if (e.key === 'Tab' && !e.shiftKey && !mod && !e.altKey) {
				e.preventDefault();
				el.setRangeText('\t', el.selectionStart, el.selectionEnd, 'end');
			} else if (e.key === 'Escape') el.blur();
			return;
		}
		// Panah kiri/kanan berpindah tab keluaran.
		if (el instanceof Element && el.getAttribute('role') === 'tab' && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
			const urutan = ['hasil', 'pesan', 'rencana'];
			const i = urutan.indexOf(el.getAttribute('data-tab') || '');
			if (i >= 0) {
				e.preventDefault();
				pilihTab(urutan[(i + (e.key === 'ArrowRight' ? 1 : urutan.length - 1)) % urutan.length], true);
			}
		}
	});

	document.addEventListener('change', (e) => {
		const el = e.target;
		if (!(el instanceof HTMLSelectElement) || el.getAttribute('data-pilih') !== 'pilih-target') return;
		const indeks = Number(el.value);
		if (Number.isInteger(indeks) && indeks >= 0) dsw.kirim({ tindakan: 'pilih-target', indeks });
	});

	// -- bagian yang berubah -----------------------------------------------------------------------
	function ganti(id, html) {
		const wadah = document.getElementById(id);
		if (!wadah || typeof html !== 'string') return;
		const aktif = document.activeElement;
		const diDalam = aktif instanceof HTMLElement && wadah.contains(aktif);
		const idAktif = diDalam ? aktif.id : '';
		const tindakanAktif = diDalam ? aktif.getAttribute('data-tindakan') : null;
		const indeksAktif = diDalam ? aktif.getAttribute('data-indeks') : null;
		// Simpul pohon yang dibuka/ditutup pengguna dipertahankan menurut kuncinya (`data-k`).
		const buka = new Map();
		for (const d of wadah.querySelectorAll('details[data-k]')) buka.set(d.getAttribute('data-k'), d.open);
		const gulir = wadah.parentElement ? wadah.parentElement.scrollTop : 0;
		// HTML buatan ekstensi (nama objek, isi sel, pesan sudah di-escape di sana); CSP melarang skrip sebaris.
		wadah.innerHTML = html;
		for (const d of wadah.querySelectorAll('details[data-k]')) {
			const k = d.getAttribute('data-k');
			if (buka.has(k)) d.open = buka.get(k);
		}
		if (wadah.parentElement) wadah.parentElement.scrollTop = gulir;
		if (diDalam) {
			let baru = idAktif ? document.getElementById(idAktif) : null;
			if (!baru && tindakanAktif) {
				const pilih = '[data-tindakan="' + tindakanAktif.replace(/[^a-z-]/g, '') + '"]' + (indeksAktif && /^\d+$/.test(indeksAktif) ? '[data-indeks="' + indeksAktif + '"]' : '');
				baru = wadah.querySelector(pilih + ':not(:disabled)');
			}
			if (baru instanceof HTMLElement) baru.focus({ preventScroll: true });
		}
		if (id === 's-hasil') pasangHasil();
	}

	window.addEventListener('message', (e) => {
		const m = e.data;
		if (!m || typeof m !== 'object') return;
		try {
			if (m.jenis === 'bagian' && m.isi && typeof m.isi === 'object') {
				for (const id of Object.keys(m.isi)) if (BAGIAN.has(id)) ganti(id, m.isi[id]);
			} else if (m.jenis === 'perintah' && typeof m.tindakan === 'string') {
				// Pintasan papan ketik aplikasi: sama dengan menekan tombol bilah alat.
				kirimSql(m.tindakan);
			} else if (m.jenis === 'editor' && typeof m.teks === 'string') {
				// Skrip buatan ekstensi (dari katalog) dipasang sebagai NILAI jendela kueri, bukan HTML.
				const el = editor();
				if (el instanceof HTMLTextAreaElement) {
					el.value = m.teks.slice(0, SQL_MAKS);
					el.setSelectionRange(0, 0);
					el.scrollTop = 0;
				}
			}
		} catch (x) {
			galat.push(String(x && x.message ? x.message : x));
		}
	});

	// Diagnostik (uji): keadaan DOM, tanpa cara menjalankan apa pun.
	window.dswStudio = {
		keadaan: () => {
			const wadah = document.querySelector('.st-hasil[data-urut]');
			const el = editor();
			return {
				bagian: document.querySelectorAll('[data-bagian]').length,
				server: document.querySelectorAll('.po__server').length,
				tabel: document.querySelectorAll('.po__tabel').length,
				kisi: document.querySelectorAll('.kisi').length,
				operator: document.querySelectorAll('.rc__kartu').length,
				tab: wadah ? [...wadah.querySelectorAll('[role="tab"]')].filter((t) => t.getAttribute('aria-selected') === 'true').map((t) => t.getAttribute('data-tab'))[0] || null : null,
				editor: el instanceof HTMLTextAreaElement ? el.value : null,
				galat: galat.slice(),
			};
		},
	};

	pasangHasil();
	dsw.kirim({ tindakan: 'siap' });
})();
