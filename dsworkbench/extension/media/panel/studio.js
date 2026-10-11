// Panel "Studio Data": mengganti bagian yang berubah (HTML buatan ekstensi, semua teks
// sudah di-escape di sana), mengirim kueri dari jendela kueri, berpindah tab keluaran,
// dan mengisi jendela kueri dengan skrip buatan ekstensi. Tidak ada jaringan dan tidak
// ada token: satu-satunya jalur keluar adalah postMessage lewat window.dsw.kirim.
(function () {
	'use strict';
	const dsw = window.dsw;
	const BAGIAN = new Set(['s-alat', 's-jelajah', 's-hasil', 's-status']);
	const TAB = new Set(['hasil', 'pesan', 'rencana', 'visual']);
	const BER_SQL = new Set(['jalankan', 'rencana', 'rencana-aktual', 'viz-kueri']);
	const SQL_MAKS = 100000;
	const galat = [];
	// Tab keluaran yang dipilih pengguna diingat selama hasilnya sama (`data-urut`).
	const tampil = { urut: '', tab: '' };

	const editor = () => document.getElementById('st-sql');

	// -- pewarnaan sintaks jendela kueri ------------------------------------------------------------
	// Lapisan <pre id="st-sorot"> di bawah kotak teks berisi salinan kueri yang sudah diwarnai; kotak
	// teksnya berhuruf tembus pandang. Token dipasang sebagai simpul teks (bukan HTML).
	const SOROT_MAKS = 60000;
	const KUNCI = new Set(
		('select from where group by order having limit offset fetch join inner left right full outer cross natural on using as and or not in is null like ilike similar between exists any some all ' +
			'case when then else end distinct union intersect except insert into values update set delete merge matched create table view materialized index unique primary key foreign references ' +
			'constraint check default alter drop add column schema database with recursive returning cube rollup grouping sets over partition range list hash for of to begin commit rollback start transaction ' +
			'savepoint explain analyze verbose buffers truncate grant revoke comment extension if replace temporary temp true false asc desc nulls first last lateral filter within window rows preceding ' +
			'following current row unbounded only cascade restrict do nothing conflict vacuum refresh concurrently generated always identity stored attach detach function returns language call').split(' '),
	);
	const TIPE = new Set(
		'int integer bigint smallint numeric decimal real double precision text varchar char character varying boolean bool date time timestamp timestamptz interval uuid json jsonb serial bigserial bytea money'.split(' '),
	);
	const POLA = /(--[^\n]*|\/\*[\s\S]*?(?:\*\/|$))|('(?:[^']|'')*'?|\$([A-Za-z_]\w*)?\$[\s\S]*?(?:\$\3\$|$))|("(?:[^"]|"")*"?)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)(?=(\s*\()?)/g;

	/** Potongan [kelas, teks] sebuah kueri; kelas kosong = teks biasa. Diekspor lewat dswStudio untuk uji. */
	function token(sql) {
		const hasil = [];
		let akhir = 0;
		POLA.lastIndex = 0;
		for (let m = POLA.exec(sql); m; m = POLA.exec(sql)) {
			if (m[0] === '') {
				POLA.lastIndex += 1;
				continue;
			}
			if (m.index > akhir) hasil.push(['', sql.slice(akhir, m.index)]);
			let kelas = '';
			if (m[1] !== undefined) kelas = 'tk-komentar';
			else if (m[2] !== undefined) kelas = 'tk-teks';
			else if (m[4] !== undefined) kelas = 'tk-nama';
			else if (m[5] !== undefined) kelas = 'tk-angka';
			else if (m[6] !== undefined) {
				const kata = m[6].toLowerCase();
				kelas = KUNCI.has(kata) ? 'tk-kunci' : TIPE.has(kata) ? 'tk-tipe' : m[7] !== undefined ? 'tk-fungsi' : '';
			}
			hasil.push([kelas, m[0]]);
			akhir = m.index + m[0].length;
		}
		if (akhir < sql.length) hasil.push(['', sql.slice(akhir)]);
		return hasil;
	}

	let pewaktuSorot = 0;
	function sorot() {
		const el = editor();
		const lapis = document.getElementById('st-sorot');
		if (!(el instanceof HTMLTextAreaElement) || !lapis || !lapis.parentElement) return;
		// Kueri sangat panjang: tanpa pewarnaan (kotak teks kembali berhuruf biasa) agar mengetik tetap ringan.
		const polos = el.value.length > SOROT_MAKS;
		lapis.parentElement.classList.toggle('st-sunting--polos', polos);
		const isi = document.createDocumentFragment();
		if (!polos) {
			for (const [kelas, teks] of token(el.value)) {
				if (!kelas) isi.appendChild(document.createTextNode(teks));
				else {
					const span = document.createElement('span');
					span.className = kelas;
					span.textContent = teks;
					isi.appendChild(span);
				}
			}
			// Baris kosong terakhir kotak teks tetap punya tinggi di lapisan <pre>.
			isi.appendChild(document.createTextNode('\n '));
		}
		lapis.replaceChildren(isi);
		lapis.scrollTop = el.scrollTop;
		lapis.scrollLeft = el.scrollLeft;
	}
	function jadwalkanSorot() {
		cancelAnimationFrame(pewaktuSorot);
		pewaktuSorot = requestAnimationFrame(sorot);
	}
	document.addEventListener('input', (e) => {
		if (e.target === editor()) jadwalkanSorot();
	});
	document.addEventListener(
		'scroll',
		(e) => {
			const el = editor();
			const lapis = document.getElementById('st-sorot');
			if (e.target !== el || !lapis) return;
			lapis.scrollTop = el.scrollTop;
			lapis.scrollLeft = el.scrollLeft;
		},
		true,
	);

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
				sorot();
			}
		} else if (t === 'pivot' && d.sasaran) {
			// Susunan pivot: nomor pilihan keempat <select>; ekstensi yang memeriksa dan menyusun kuerinya.
			const nilai = (id) => {
				const sel = document.getElementById(id);
				return sel instanceof HTMLSelectElement ? Number(sel.value) : NaN;
			};
			const pesan = { tindakan: 'pivot', indeks: Number(d.sasaran.getAttribute('data-indeks')), baris: nilai('pv-baris'), kolom: nilai('pv-kolom'), ukuran: nilai('pv-ukuran'), agregat: nilai('pv-agregat') };
			if (Object.values(pesan).every((v) => typeof v === 'string' || Number.isInteger(v))) dsw.kirim(pesan);
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
				jadwalkanSorot();
			} else if (e.key === 'Escape') el.blur();
			return;
		}
		// Panah kiri/kanan berpindah tab keluaran.
		if (el instanceof Element && el.getAttribute('role') === 'tab' && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
			// Hanya tab yang memang ada pada hasil ini (tab tampilan tidak selalu ada).
			const urutan = el.parentElement ? [...el.parentElement.querySelectorAll('[role="tab"]')].map((t) => t.getAttribute('data-tab') || '') : [];
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
					sorot();
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
				token: document.querySelectorAll('#st-sorot span').length,
				visual: document.querySelectorAll('#st-pane-visual').length,
				galat: galat.slice(),
			};
		},
		// Pengurai token pewarnaan (murni), untuk uji.
		token,
	};

	sorot();
	pasangHasil();
	dsw.kirim({ tindakan: 'siap' });
})();
