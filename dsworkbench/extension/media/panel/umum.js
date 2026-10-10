// Skrip bersama panel DSWorkbench (naskah, Beranda, Bravais). Dimuat sebagai berkas
// lokal ekstensi ber-nonce. Panel tidak memegang token dan tidak berbicara ke
// jaringan: satu-satunya jalur keluar adalah postMessage ke ekstensi, dan isinya
// hanya nama tindakan TETAP + nomor urut (tidak pernah path, URL, atau perintah).
(function () {
	'use strict';
	const vscode = acquireVsCodeApi();

	// KaTeX menulis ukuran lewat atribut style; CSP melarang gaya sebaris, jadi perender
	// menggantinya dengan data-gaya dan di sini diterapkan lewat CSSOM (daftar tetap).
	const PROPERTI = new Set([
		'height', 'width', 'min-width', 'max-width', 'top', 'bottom', 'left', 'right', 'position', 'vertical-align',
		'margin', 'margin-left', 'margin-right', 'margin-top', 'margin-bottom',
		'padding', 'padding-left', 'padding-right', 'padding-top', 'padding-bottom',
		'border-width', 'border-style', 'border-color', 'border-top-width', 'border-bottom-width', 'border-left-width', 'border-right-width',
		'border-right-style', 'border-top-style', 'border-bottom-style', 'border-left-style',
		'color', 'background-color', 'font-size', 'line-height', 'text-align', 'text-shadow',
	]);

	function terapkanGaya(akar) {
		for (const el of akar.querySelectorAll('[data-gaya]')) {
			for (const butir of (el.getAttribute('data-gaya') || '').split(';')) {
				const i = butir.indexOf(':');
				if (i < 1) continue;
				const properti = butir.slice(0, i).trim().toLowerCase();
				const nilai = butir.slice(i + 1).trim();
				if (!PROPERTI.has(properti) || nilai.length > 80 || /url\s*\(|expression|var\s*\(|[<>{}@\\]/i.test(nilai)) continue;
				el.style.setProperty(properti, nilai);
			}
			el.removeAttribute('data-gaya');
		}
	}

	const angka = (teks) => (/^\d{1,6}$/.test(teks || '') ? Number(teks) : undefined);

	/** Induk pesan (gelembung Bravais) tempat tombol berada, bila ada. */
	const pesanInduk = (el) => angka(el.closest('[data-pesan]')?.getAttribute('data-pesan'));

	document.addEventListener('click', (e) => {
		const sasaran = e.target instanceof Element ? e.target.closest('[data-tautan],[data-tindakan]') : null;
		if (!sasaran) return;
		if (sasaran.hasAttribute('data-tautan')) {
			// Tautan tidak pernah dinavigasi di panel: ekstensi yang membukanya di peramban.
			e.preventDefault();
			const indeks = angka(sasaran.getAttribute('data-tautan'));
			if (indeks !== undefined) vscode.postMessage({ tindakan: 'buka-tautan', indeks, pesan: pesanInduk(sasaran) });
			return;
		}
		if (sasaran.disabled || sasaran.getAttribute('aria-disabled') === 'true') return;
		e.preventDefault();
		const pesan = { tindakan: sasaran.getAttribute('data-tindakan') };
		const indeks = angka(sasaran.getAttribute('data-indeks'));
		if (indeks !== undefined) pesan.indeks = indeks;
		const induk = pesanInduk(sasaran);
		if (induk !== undefined) pesan.pesan = induk;
		const id = sasaran.getAttribute('data-id');
		if (id && /^[A-Za-z0-9_-]{1,80}$/.test(id)) pesan.id = id;
		window.dispatchEvent(new CustomEvent('dsw-tindakan', { detail: { pesan, sasaran } }));
		if (!sasaran.hasAttribute('data-lokal')) vscode.postMessage(pesan);
		// Cegah klik ganda pada tindakan yang menulis berkas atau menjalankan sesuatu.
		if (sasaran instanceof HTMLButtonElement && !sasaran.hasAttribute('data-tanpa-jeda')) {
			sasaran.disabled = true;
			setTimeout(() => {
				if (!sasaran.hasAttribute('data-terkunci')) sasaran.disabled = false;
			}, 1200);
		}
	});

	// -- suara ---------------------------------------------------------------------
	// Ekstensi yang memutuskan (pengaturan, pembatas laju, prioritas) dan mengirim
	// { jenis: 'suara', nama, volume }. Panel hanya mengenal NAMA dari daftar tetap ini
	// dan memutar elemen <audio id="suara-…"> yang alamatnya dipasang ekstensi di
	// kerangka: tidak pernah path atau URL dari pesan. Harus sama dengan
	// NAMA_SUARA di src/suara/daftar.ts.
	const NAMA_SUARA = ['klik', 'buka', 'tutup', 'berpikir', 'jawaban', 'galat', 'notif'];
	const suara = { terbuka: false, diputar: 0, gagal: 0, aktif: null };
	const elemenSuara = (nama) => {
		if (typeof nama !== 'string' || !NAMA_SUARA.includes(nama)) return null;
		const a = document.getElementById('suara-' + nama);
		return a && typeof a.play === 'function' ? a : null;
	};
	const adaSuara = NAMA_SUARA.some((n) => elemenSuara(n));
	const GESTUR = ['pointerdown', 'keydown'];

	// Webview aplikasi menolak play() tanpa gestur pengguna (NotAllowedError), dan kuncinya
	// PER ELEMEN: elemen yang pernah di-play() di dalam gestur boleh diputar lagi kapan pun.
	// Jadi pada gestur pertama tiap elemen dibuka (play lalu langsung pause, tanpa bunyi);
	// sebelum itu panel diam dan tidak memanggil play() sama sekali.
	function bukaKunciSuara(e) {
		if (suara.terbuka || (e && e.isTrusted === false)) return;
		suara.terbuka = true;
		for (const g of GESTUR) window.removeEventListener(g, bukaKunciSuara, true);
		for (const nama of NAMA_SUARA) {
			const a = elemenSuara(nama);
			if (!a) continue;
			try {
				const p = a.play();
				a.pause();
				if (p && typeof p.catch === 'function') p.catch(() => undefined);
			} catch {
				// diam
			}
		}
	}
	if (adaSuara) for (const g of GESTUR) window.addEventListener(g, bukaKunciSuara, true);

	function putarSuara(nama, volume) {
		const a = elemenSuara(nama);
		if (!a || !suara.terbuka || document.hidden) return false;
		if (typeof volume !== 'number' || !(volume > 0) || volume > 1) return false;
		try {
			// Tidak pernah menumpuk: yang sedang berbunyi dihentikan.
			if (suara.aktif && suara.aktif !== a && !suara.aktif.paused) suara.aktif.pause();
			suara.aktif = a;
			a.volume = volume;
			a.currentTime = 0;
			const p = a.play();
			suara.diputar += 1;
			// Penolakan (kebijakan autoplay, berkas tidak ada) berakhir diam, tanpa galat di konsol.
			if (p && typeof p.catch === 'function') p.catch(() => {
				suara.gagal += 1;
			});
			return true;
		} catch {
			suara.gagal += 1;
			return false;
		}
	}
	if (adaSuara) {
		window.addEventListener('message', (e) => {
			const m = e.data;
			if (m && typeof m === 'object' && m.jenis === 'suara') putarSuara(m.nama, m.volume);
		});
	}

	window.dsw = {
		// Diagnostik (uji): tidak ada cara memutar suara dari sini.
		suara: () => ({ ada: adaSuara, terbuka: suara.terbuka, diputar: suara.diputar, gagal: suara.gagal }),
		kirim: (pesan) => vscode.postMessage(pesan),
		terapkanGaya,
		keadaan: () => vscode.getState() || {},
		simpanKeadaan: (k) => vscode.setState(k),
		tenang: () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
	};
	terapkanGaya(document);
})();
