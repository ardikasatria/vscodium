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

	window.dsw = {
		kirim: (pesan) => vscode.postMessage(pesan),
		terapkanGaya,
		keadaan: () => vscode.getState() || {},
		simpanKeadaan: (k) => vscode.setState(k),
		tenang: () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
	};
	terapkanGaya(document);
})();
