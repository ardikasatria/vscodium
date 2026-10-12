// Skrip jendela "Soal analisis". Dimuat setelah umum.js. Panel tidak berbicara ke
// jaringan: jawaban dikirim ke ekstensi (postMessage) sebagai daftar teks berurutan,
// dan ekstensi yang menyimpannya ke draf di server.
(function () {
	'use strict';
	const dsw = window.dsw;
	const kotak = Array.from(document.querySelectorAll('textarea[data-soal]'));
	const status = document.getElementById('status');
	const tombolSimpan = document.getElementById('simpan');
	const tombolKumpul = document.getElementById('kumpulkan');
	const terkunci = kotak.length > 0 && kotak[0].readOnly;
	const JEDA_SIMPAN = 1500;

	let kotor = false;
	let menyimpan = false;
	let pewaktu = null;

	const nilai = () => kotak.map((k) => k.value);
	const minimum = (k) => Number(k.getAttribute('data-min')) || 0;
	const cukup = (k) => {
		const n = k.value.trim().length;
		return n > 0 && n >= minimum(k);
	};

	function hitung(k) {
		const p = document.getElementById('hitung-' + k.getAttribute('data-soal'));
		if (!p) return;
		const n = k.value.trim().length;
		p.textContent = (cukup(k) ? '☑ ' : '□ ') + n + ' / min. ' + minimum(k) + ' karakter';
		p.setAttribute('data-cukup', cukup(k) ? 'ya' : 'tidak');
		p.setAttribute('data-lebih', n >= k.maxLength ? 'ya' : 'tidak');
	}

	function tulisStatus(keadaan, teks) {
		if (!status) return;
		status.setAttribute('data-keadaan', keadaan);
		status.textContent = teks;
	}

	function segarkanTombol() {
		if (tombolSimpan) tombolSimpan.disabled = menyimpan;
		if (!tombolKumpul) return;
		const kurang = kotak.filter((k) => !cukup(k)).length;
		const luring = tombolKumpul.hasAttribute('data-luring');
		tombolKumpul.disabled = menyimpan || kurang > 0 || luring;
		tombolKumpul.title = luring
			? 'Belum tersambung ke server Workbench. Jawaban tetap dapat disimpan.'
			: kurang > 0
				? kurang + ' soal belum memenuhi panjang minimum.'
				: '';
	}

	function kirim(tindakan, otomatis) {
		if (terkunci) return;
		if (pewaktu) clearTimeout(pewaktu);
		pewaktu = null;
		kotor = false;
		menyimpan = true;
		segarkanTombol();
		tulisStatus('menyimpan', tindakan === 'kumpulkan' ? 'Menyimpan, lalu mengumpulkan…' : 'Menyimpan…');
		dsw.kirim({ tindakan, jawaban: nilai(), otomatis });
	}

	function jadwalkan() {
		kotor = true;
		dsw.simpanKeadaan({ jawaban: nilai() });
		if (!menyimpan) tulisStatus('kotor', 'Belum tersimpan…');
		if (pewaktu) clearTimeout(pewaktu);
		pewaktu = setTimeout(() => {
			if (!menyimpan) kirim('simpan', true);
		}, JEDA_SIMPAN);
	}

	// Panel disembunyikan lalu dibuat ulang: teks yang belum tersimpan dipulihkan.
	const lama = dsw.keadaan().jawaban;
	if (!terkunci && Array.isArray(lama) && lama.length === kotak.length) {
		lama.forEach((t, i) => {
			if (typeof t === 'string' && t !== kotak[i].value) {
				kotak[i].value = t;
				kotor = true;
			}
		});
	}

	for (const k of kotak) {
		hitung(k);
		if (terkunci) continue;
		k.addEventListener('input', () => {
			hitung(k);
			segarkanTombol();
			jadwalkan();
		});
		// Pindah kotak atau pindah jendela: simpan segera, tidak menunggu jeda.
		k.addEventListener('blur', () => {
			if (kotor && !menyimpan) kirim('simpan', true);
		});
	}
	document.addEventListener('visibilitychange', () => {
		if (document.hidden && kotor && !menyimpan) kirim('simpan', true);
	});

	window.addEventListener('dsw-tindakan', (e) => {
		const t = e.detail && e.detail.pesan && e.detail.pesan.tindakan;
		if (t === 'simpan' || t === 'kumpulkan') kirim(t, false);
	});

	window.addEventListener('message', (e) => {
		const m = e.data;
		if (!m || typeof m !== 'object') return;
		if (typeof m.daring === 'boolean' && tombolKumpul) tombolKumpul.toggleAttribute('data-luring', !m.daring);
		if (m.jenis !== 'status') {
			segarkanTombol();
			return;
		}
		menyimpan = false;
		if (m.keadaan === 'galat') kotor = true;
		// Yang tersimpan di server tidak perlu dipulihkan lagi dari keadaan panel.
		else if (!kotor) dsw.simpanKeadaan({});
		tulisStatus(m.keadaan === 'galat' ? 'galat' : 'tersimpan', typeof m.teks === 'string' ? m.teks : '');
		segarkanTombol();
		// Ada ketikan selama penyimpanan berjalan: simpan lagi.
		if (kotor && m.keadaan !== 'galat') jadwalkan();
	});

	segarkanTombol();
	if (kotor) jadwalkan();
	if (!terkunci && kotak[0]) kotak[0].focus();
})();
