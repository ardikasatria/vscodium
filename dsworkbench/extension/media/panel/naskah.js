// Panel "Naskah modul": daftar isi, posisi gulir yang diingat, dan tombol "Tandai selesai".
(function () {
	'use strict';
	const dsw = window.dsw;
	const lompat = (id) => {
		const el = document.getElementById(id);
		if (el) el.scrollIntoView({ behavior: dsw.tenang() ? 'auto' : 'smooth', block: 'start' });
	};

	// Daftar isi: gulir di dalam panel (tidak ada navigasi).
	document.addEventListener('click', (e) => {
		const a = e.target instanceof Element ? e.target.closest('a[data-lompat]') : null;
		if (!a) return;
		e.preventDefault();
		lompat(a.getAttribute('data-lompat'));
	});

	// Posisi gulir: dari ekstensi (panel dibuka ulang) atau dari keadaan webview (tab disembunyikan).
	const awal = Number(dsw.keadaan().gulir ?? document.body.getAttribute('data-gulir') ?? 0);
	const pulihkan = () => {
		if (Number.isFinite(awal) && awal > 0) window.scrollTo(0, awal);
	};
	pulihkan();
	window.addEventListener('load', pulihkan);

	let pewaktu;
	window.addEventListener(
		'scroll',
		() => {
			clearTimeout(pewaktu);
			pewaktu = setTimeout(() => {
				const y = Math.max(0, Math.round(window.scrollY));
				dsw.simpanKeadaan({ ...dsw.keadaan(), gulir: y });
				dsw.kirim({ tindakan: 'gulir', y });
			}, 400);
		},
		{ passive: true },
	);

	// Butir daftar isi yang sedang dibaca.
	const tautan = new Map();
	for (const a of document.querySelectorAll('a[data-lompat]')) tautan.set(a.getAttribute('data-lompat'), a);
	if (tautan.size > 0 && 'IntersectionObserver' in window) {
		const pengamat = new IntersectionObserver(
			(entri) => {
				for (const en of entri) {
					if (!en.isIntersecting) continue;
					for (const a of tautan.values()) a.removeAttribute('aria-current');
					tautan.get(en.target.id)?.setAttribute('aria-current', 'location');
				}
			},
			{ rootMargin: '0px 0px -75% 0px' },
		);
		for (const id of tautan.keys()) {
			const el = document.getElementById(id);
			if (el) pengamat.observe(el);
		}
	}

	window.addEventListener('message', (e) => {
		const m = e.data;
		if (!m || typeof m !== 'object') return;
		if (m.jenis === 'tandai') {
			const t = document.getElementById('tandai');
			if (!t) return;
			t.textContent = m.selesai ? 'Sudah selesai dibaca' : 'Tandai selesai baca';
			t.disabled = Boolean(m.selesai);
			t.classList.toggle('selesai', Boolean(m.selesai));
			if (m.selesai) t.setAttribute('data-terkunci', '');
		}
	});
	dsw.kirim({ tindakan: 'siap' });
})();
