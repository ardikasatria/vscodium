// Panel Sosial: menggambar keadaan kiriman ekstensi (teman, percakapan, status sendiri)
// dan mengirim tindakan tetap. SEMUA isi dari pengguna lain (nama, status, isi pesan)
// dipasang lewat textContent; teks tidak pernah ditafsirkan sebagai HTML. Panel tidak memegang
// token dan tidak berbicara ke jaringan. Draf pesan hanya di memori panel.
(function () {
	'use strict';
	const dsw = window.dsw;
	const el = (id) => document.getElementById(id);
	const MAKS_ANIMASI = 6;
	let keadaan = null;
	let cari = '';
	let tandaDaftar = '';
	let tandaPesan = '';
	let idObrolan = null;
	let teksStatusServer = '';
	let statusKotor = false;
	const draf = new Map();

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

	// -- avatar: PNG dari cache ekstensi, inisial bila tidak ada; Lottie hanya untuk yang tampak --
	function avatar(a, opsi) {
		const o = opsi || {};
		const akar = buat('span', `avatar${o.kecil ? ' avatar--kecil' : ''}`);
		const gambar = buat('span', 'avatar__gambar');
		gambar.setAttribute('aria-hidden', 'true');
		if (a && typeof a.uri === 'string' && a.uri) {
			const img = document.createElement('img');
			img.alt = '';
			img.draggable = false;
			img.addEventListener('error', () => {
				img.remove();
				gambar.textContent = a.inisial || '?';
			});
			img.src = a.uri;
			gambar.appendChild(img);
			if (a.animasi) {
				akar.setAttribute('data-animasi', a.animasi);
				gambar.appendChild(buat('span', 'avatar__animasi'));
			}
		} else {
			gambar.textContent = (a && a.inisial) || '?';
		}
		akar.appendChild(gambar);
		if (o.titik) {
			const t = buat('span', `avatar__titik ${o.titik}`);
			t.appendChild(buat('span', 'hanya-pembaca-layar', o.titik === 'daring' ? 'Online' : o.titik === 'dnd' ? 'Jangan ganggu' : 'Offline'));
			akar.appendChild(t);
		}
		if (o.lencana > 0) {
			const l = buat('span', 'avatar__lencana', o.lencana > 99 ? '99+' : String(o.lencana));
			l.appendChild(buat('span', 'hanya-pembaca-layar', ' pesan belum dibaca'));
			akar.appendChild(l);
		}
		return akar;
	}

	const titik = (t) => (t.dnd ? 'dnd' : t.daring ? 'daring' : 'luring');

	const dataAnimasi = new Map(); // id katalog → { teks, kotak }
	const diminta = new Set();
	const pemutar = new Map(); // elemen avatar → animasi
	const tampak = new Set();
	const pengamat = new IntersectionObserver((entri) => {
		for (const e of entri) {
			if (e.isIntersecting) tampak.add(e.target);
			else {
				tampak.delete(e.target);
				hentikan(e.target);
			}
		}
		aturAnimasi();
	});

	function hentikan(elemen) {
		const a = pemutar.get(elemen);
		if (!a) return;
		pemutar.delete(elemen);
		try {
			a.destroy();
		} catch {
			// abaikan
		}
		elemen.classList.remove('hidup');
		elemen.querySelector('.avatar__animasi')?.replaceChildren();
	}

	function hentikanSemua() {
		for (const e of [...pemutar.keys()]) hentikan(e);
	}

	function aturAnimasi() {
		// Mengikuti "kurangi gerak"; berhenti saat panel tersembunyi.
		if (!window.lottie || dsw.tenang() || document.hidden) {
			hentikanSemua();
			return;
		}
		for (const elemen of tampak) {
			if (!elemen.isConnected) {
				tampak.delete(elemen);
				hentikan(elemen);
				continue;
			}
			if (pemutar.has(elemen) || pemutar.size >= MAKS_ANIMASI) continue;
			const id = elemen.getAttribute('data-animasi');
			const wadah = elemen.querySelector('.avatar__animasi');
			if (!id || !wadah) continue;
			const d = dataAnimasi.get(id);
			if (!d) {
				// Animasi diambil ekstensi saat dibutuhkan, sekali per avatar.
				if (!diminta.has(id) && diminta.size < 12) {
					diminta.add(id);
					dsw.kirim({ tindakan: 'animasi', avatar: id });
				}
				continue;
			}
			try {
				const anim = window.lottie.loadAnimation({
					container: wadah,
					renderer: 'svg',
					loop: true,
					autoplay: true,
					// lottie-web mengubah objek datanya: tiap pemutar mendapat salinan sendiri.
					animationData: JSON.parse(d.teks),
					rendererSettings: { viewBoxOnly: true, viewBoxSize: `${d.kotak[0]} ${d.kotak[1]} ${d.kotak[2]} ${d.kotak[2]}`, preserveAspectRatio: 'xMidYMid meet' },
				});
				anim.setSubframe(false);
				pemutar.set(elemen, anim);
				anim.addEventListener('DOMLoaded', () => elemen.classList.add('hidup'));
				anim.addEventListener('data_failed', () => hentikan(elemen));
				anim.addEventListener('error', () => hentikan(elemen));
			} catch {
				hentikan(elemen);
			}
		}
	}

	function amatiAvatar(akar) {
		for (const e of akar.querySelectorAll('[data-animasi]')) pengamat.observe(e);
	}

	function lepasAvatar(akar) {
		for (const e of akar.querySelectorAll('[data-animasi]')) {
			pengamat.unobserve(e);
			tampak.delete(e);
			hentikan(e);
		}
	}

	function ganti(wadah, anak) {
		lepasAvatar(wadah);
		wadah.replaceChildren(...anak);
		amatiAvatar(wadah);
	}

	// -- daftar teman ----------------------------------------------------------------
	function barisTeman(t) {
		const b = buat('button', `teman${t.belumDibaca ? ' baru' : ''}`);
		b.type = 'button';
		b.setAttribute('data-tindakan', 'buka');
		b.setAttribute('data-id', t.id);
		b.setAttribute('data-tanpa-jeda', '');
		b.appendChild(avatar(t.avatar, { titik: titik(t), lencana: t.belumDibaca }));
		const teks = buat('span', 'teman__teks');
		const atas = buat('span', 'teman__atas');
		atas.appendChild(buat('span', 'teman__nama', t.nama));
		const pil = buat('span', `pil${t.daring ? ' daring' : ''}`, t.pil);
		if (t.judulPil) pil.title = t.judulPil;
		atas.appendChild(pil);
		teks.appendChild(atas);
		if (t.suasana) teks.appendChild(buat('span', 'teman__suasana', t.suasana)).title = t.suasana;
		if (t.pratinjau) teks.appendChild(buat('span', 'teman__pratinjau', t.pratinjau));
		if (t.terakhir) teks.appendChild(buat('span', 'teman__terakhir', t.terakhir));
		b.appendChild(teks);
		return b;
	}

	function bagian(judul, daftar, kosong) {
		const s = buat('section', 'bagian');
		const k = buat('div', 'bagian__kepala');
		k.appendChild(buat('strong', '', judul));
		k.appendChild(buat('span', '', `${daftar.length} teman`));
		s.appendChild(k);
		if (daftar.length === 0) s.appendChild(buat('div', 'kosong', kosong));
		for (const t of daftar) s.appendChild(barisTeman(t));
		return s;
	}

	function kartu(isi, aksi) {
		const k = buat('div', 'kartu kartu-tengah');
		k.appendChild(buat('p', '', isi));
		for (const a of aksi || []) k.appendChild(a);
		return k;
	}

	function gambarDaftar(k) {
		const siap = k.masuk && k.keadaan === 'siap';
		el('diri').hidden = !siap || !k.diri;
		el('cari-bungkus').hidden = !siap || k.online.length + k.offline.length === 0;
		const q = cari.trim().toLowerCase();
		const saring = (d) => (q ? d.filter((t) => t.nama.toLowerCase().includes(q)) : d);
		const tanda = JSON.stringify([k.masuk, k.keadaan, k.pesanGalat, k.galatTeman, q, k.online, k.offline]);
		if (tanda === tandaDaftar) return;
		tandaDaftar = tanda;
		const anak = [];
		if (!k.masuk) {
			anak.push(kartu('Masuk ke DSWorkbench untuk melihat teman sekelas dan mengobrol.', [tombol('Masuk', 'masuk', {}, 'tombol tombol--utama')]));
		} else if (k.keadaan === 'tidak_sah') {
			anak.push(kartu(k.pesanGalat || 'Sesi Anda berakhir.', [tombol('Masuk', 'masuk', {}, 'tombol tombol--utama')]));
		} else if (k.keadaan === 'fitur_mati') {
			// Fitur dimatikan admin atau belum tersedia untuk aplikasi: pesan server apa adanya.
			anak.push(kartu(k.pesanGalat || 'Sosial tidak tersedia.', [tombol('Coba lagi', 'muat-ulang'), tombol('Buka di web', 'buka-web', { 'data-tanpa-jeda': '' })]));
		} else if (k.keadaan === 'galat') {
			anak.push(kartu(k.pesanGalat || 'Sosial tidak dapat dimuat.', [tombol('Coba lagi', 'muat-ulang')]));
		} else if (k.keadaan !== 'siap') {
			anak.push(buat('div', 'kosong', 'Memuat…'));
		} else if (k.online.length + k.offline.length === 0) {
			if (k.galatTeman) anak.push(kartu('Daftar teman gagal dimuat.', [tombol('Coba lagi', 'muat-ulang')]));
			else anak.push(buat('div', 'kosong', 'Belum ada teman sekelas.'));
		} else {
			anak.push(bagian('Online', saring(k.online), q ? 'Tidak ada yang cocok.' : 'Tidak ada teman yang online.'));
			anak.push(bagian('Offline', saring(k.offline), q ? 'Tidak ada yang cocok.' : 'Semua teman online.'));
		}
		ganti(el('daftar'), anak);
	}

	// -- status sendiri ------------------------------------------------------------------
	const pilihStatus = el('status');
	const teksStatus = el('teks-status');
	const simpanStatus = el('simpan-status');

	function perbaruiTombolStatus() {
		const nilai = teksStatus.value.trim();
		const berubah = nilai !== teksStatusServer;
		el('hitung-status').textContent = `${nilai.length}/${teksStatus.maxLength}`;
		// Ada status tersimpan dan tidak diubah → tombol menjadi "Hapus".
		const hapus = teksStatusServer !== '' && !berubah;
		simpanStatus.textContent = hapus ? 'Hapus' : 'Simpan';
		simpanStatus.setAttribute('aria-label', hapus ? 'Hapus status' : 'Simpan status');
		simpanStatus.disabled = !hapus && !berubah;
		el('saran-suasana').hidden = teksStatusServer !== '' || teksStatus.value !== '';
	}

	function gambarDiri(k) {
		if (!k.diri) return;
		if (pilihStatus.options.length === 0) {
			for (const [nilai, label] of Object.entries(k.labelStatus)) {
				const o = buat('option', '', label);
				o.value = nilai;
				pilihStatus.appendChild(o);
			}
		}
		if (document.activeElement !== pilihStatus) pilihStatus.value = k.diri.status;
		const tandaAvatar = JSON.stringify(k.diri.avatar);
		if (el('diri-avatar').getAttribute('data-tanda') !== tandaAvatar) {
			el('diri-avatar').setAttribute('data-tanda', tandaAvatar);
			ganti(el('diri-avatar'), [avatar(k.diri.avatar, {})]);
		}
		if (k.diri.teksStatus !== teksStatusServer) {
			teksStatusServer = k.diri.teksStatus;
			statusKotor = false;
		}
		if (!statusKotor && document.activeElement !== teksStatus) teksStatus.value = teksStatusServer;
		perbaruiTombolStatus();
	}

	pilihStatus.addEventListener('change', () => dsw.kirim({ tindakan: 'status', nilai: pilihStatus.value }));
	teksStatus.addEventListener('input', () => {
		statusKotor = true;
		perbaruiTombolStatus();
	});
	function kirimStatus() {
		if (simpanStatus.disabled) return;
		const nilai = teksStatus.value.trim();
		const hapus = teksStatusServer !== '' && nilai === teksStatusServer;
		statusKotor = false;
		if (hapus) teksStatus.value = '';
		simpanStatus.disabled = true;
		dsw.kirim({ tindakan: 'teks-status', teks: hapus ? '' : nilai.slice(0, teksStatus.maxLength) });
	}
	simpanStatus.addEventListener('click', kirimStatus);
	teksStatus.addEventListener('keydown', (e) => {
		if (e.key === 'Enter' && !e.isComposing) {
			e.preventDefault();
			kirimStatus();
		}
	});
	for (const s of ['Lagi semangat!', 'Fokus belajar', 'Lagi pusing tugas', 'Istirahat sebentar']) {
		const b = buat('button', 'tombol tombol--halus', s);
		b.type = 'button';
		b.addEventListener('click', () => {
			teksStatus.value = s;
			statusKotor = true;
			perbaruiTombolStatus();
			teksStatus.focus();
		});
		el('saran-suasana').appendChild(b);
	}
	el('cari').addEventListener('input', (e) => {
		cari = e.target.value;
		if (keadaan) gambarDaftar(keadaan);
	});

	// -- percakapan ---------------------------------------------------------------------
	const masukan = el('masukan');
	const tombolKirim = el('kirim');
	const wadahPesan = el('pesan');
	let pewaktuMengetik = null;

	function gelembung(p, milikSaya, namaTeman) {
		const g = buat('div', `gelembung${milikSaya ? ' milik' : ''}${p.bantuan ? ' bantuan' : ''}`);
		g.appendChild(buat('span', 'hanya-pembaca-layar', milikSaya ? 'Anda: ' : `${namaTeman}: `));
		if (p.bantuan) {
			const kepala = buat('div', 'bantuan__kepala', 'Minta bantuan');
			if (p.bantuan.topik) kepala.appendChild(buat('span', '', ` · ${p.bantuan.topik}`));
			g.appendChild(kepala);
		}
		const isi = buat('span');
		for (const b of p.bagian) {
			if (typeof b.tautan === 'number') {
				// Tautan tidak dinavigasi di panel: ekstensi memvalidasi alamat miliknya lalu membukanya di peramban.
				isi.appendChild(tombol(b.teks, 'buka-tautan', { 'data-id': p.id, 'data-indeks': b.tautan, 'data-tanpa-jeda': '' }, 'tautan-tombol'));
			} else isi.appendChild(document.createTextNode(b.teks));
		}
		g.appendChild(isi);
		if (p.bantuan && p.bantuan.sesi) g.appendChild(buat('div')).appendChild(tombol('Buka sesi bantuan di web', 'bantuan', { 'data-id': p.id, 'data-tanpa-jeda': '' }));
		else if (p.bantuan && p.bantuan.tawarkan) {
			const t = tombol('Tawarkan bantuan', 'tawarkan', { 'data-lokal': '', 'data-tanpa-jeda': '' });
			t.addEventListener('click', () => {
				masukan.value = `Saya bisa bantu${p.bantuan.topik ? ` soal ${p.bantuan.topik}` : ''}. `.slice(0, masukan.maxLength);
				perbaruiKirim();
				masukan.focus();
			});
			g.appendChild(buat('div')).appendChild(t);
		}
		return g;
	}

	function perbaruiKirim() {
		const o = keadaan && keadaan.obrolan;
		tombolKirim.disabled = !o || o.dnd || masukan.value.trim() === '';
	}

	function gambarObrolan(k) {
		const o = k.obrolan;
		if (!o) return;
		if (idObrolan !== o.id) {
			// Berpindah percakapan: draf (hanya di memori) mengikuti temannya.
			if (idObrolan) draf.set(idObrolan, masukan.value);
			idObrolan = o.id;
			masukan.value = draf.get(o.id) || '';
			tandaPesan = '';
			setTimeout(() => masukan.focus(), 0);
		}
		const aksi = [tombol(o.balasColek ? 'Balas colek' : 'Colek', 'colek', { 'data-id': o.id })];
		if (o.dnd) aksi[0].disabled = true;
		if (o.suasana) aksi.push(buat('span', 'aksi__suasana', o.suasana));
		aksi[aksi.length - 1].title = o.suasana || '';
		el('aksi').replaceChildren(...aksi);

		const tanda = JSON.stringify([o.id, o.dimuat, o.galat, o.butir, o.mengetik, o.avatar, o.dnd]);
		if (tanda !== tandaPesan) {
			const diBawah = wadahPesan.scrollHeight - wadahPesan.scrollTop - wadahPesan.clientHeight < 80 || tandaPesan === '';
			tandaPesan = tanda;
			const anak = [];
			if (o.galat && !o.dimuat) anak.push(buat('div', 'kosong galat', o.galat));
			else if (!o.dimuat) anak.push(buat('div', 'kosong', 'Memuat…'));
			else if (o.butir.length === 0) anak.push(buat('div', 'kosong', `Belum ada pesan. Sapa ${o.nama}!`));
			for (const b of o.butir) {
				if (b.jenis === 'pemisah') anak.push(buat('div', 'pemisah', b.teks));
				else if (b.jenis === 'colek') {
					const c = buat('div', 'colek');
					c.appendChild(buat('span', '', `${b.teks} · ${b.jam}`));
					if (b.balas) {
						const t = tombol('Balas colek', 'colek', { 'data-id': o.id });
						t.disabled = o.dnd;
						c.appendChild(t);
					}
					anak.push(c);
				} else {
					const g = buat('div', `grup${b.milikSaya ? ' grup--saya' : ''}`);
					if (!b.milikSaya) g.appendChild(avatar(o.avatar, { kecil: true }));
					const isi = buat('div', 'grup__isi');
					for (const p of b.pesan) isi.appendChild(gelembung(p, b.milikSaya, o.nama));
					isi.appendChild(buat('div', 'grup__kaki', b.kaki));
					g.appendChild(isi);
					anak.push(g);
				}
			}
			if (o.mengetik) {
				const m = buat('div', 'mengetik');
				m.setAttribute('role', 'status');
				const t = buat('span', 'titik');
				t.setAttribute('aria-hidden', 'true');
				t.append(buat('i'), buat('i'), buat('i'));
				m.append(t, buat('span', '', `${o.nama} sedang mengetik…`));
				anak.push(m);
			}
			ganti(wadahPesan, anak);
			if (diBawah) wadahPesan.scrollTop = wadahPesan.scrollHeight;
		}
		wadahPesan.setAttribute('aria-label', `Percakapan dengan ${o.nama}`);
		el('catatan-dnd').hidden = !o.dnd;
		el('catatan-dnd').textContent = o.dnd ? `${o.nama} sedang mode Jangan ganggu — pesan tidak dapat dikirim.` : '';
		masukan.disabled = o.dnd;
		masukan.placeholder = o.daring ? 'Tulis pesan…' : 'Tulis pesan — dibaca saat ia online';
		masukan.setAttribute('aria-label', `Pesan untuk ${o.nama}`);
		perbaruiKirim();
	}

	function kirim() {
		const o = keadaan && keadaan.obrolan;
		const teks = masukan.value.trim();
		if (!o || o.dnd || teks === '') return;
		dsw.kirim({ tindakan: 'kirim', id: o.id, teks: teks.slice(0, masukan.maxLength) });
		masukan.value = '';
		draf.delete(o.id);
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
		perbaruiKirim();
		// Sinyal "sedang mengetik" setelah jeda ketik singkat (web: 400 ms).
		clearTimeout(pewaktuMengetik);
		const id = idObrolan;
		if (!id || masukan.value.trim() === '') return;
		pewaktuMengetik = setTimeout(() => {
			if (idObrolan === id) dsw.kirim({ tindakan: 'mengetik', id });
		}, 400);
	});
	document.addEventListener('keydown', (e) => {
		if (e.key === 'Escape' && keadaan && keadaan.obrolan) dsw.kirim({ tindakan: 'kembali' });
	});

	// -- kerangka ------------------------------------------------------------------------
	function gambar(k) {
		keadaan = k;
		const o = k.masuk && k.keadaan === 'siap' ? k.obrolan : undefined;
		el('tampilan-daftar').hidden = Boolean(o);
		el('tampilan-obrolan').hidden = !o;
		el('komposer').hidden = !o;
		el('kembali').hidden = !o;
		const pemberitahuan = el('pemberitahuan');
		pemberitahuan.hidden = !k.pemberitahuan;
		pemberitahuan.textContent = k.pemberitahuan || '';
		const kepalaAvatar = el('kepala-avatar');
		const ket = el('kepala-ket');
		if (o) {
			el('kepala-nama').textContent = o.nama;
			ket.textContent = o.mengetik ? 'sedang mengetik…' : o.kehadiran;
			ket.title = o.kehadiran;
			ket.classList.toggle('hidup', o.daring);
			const lain = k.belumDibaca;
			el('kembali').textContent = lain > 0 ? `‹ ${lain > 99 ? '99+' : lain}` : '‹';
			el('kembali').setAttribute('aria-label', lain > 0 ? `Kembali ke daftar teman, ${lain} pesan belum dibaca` : 'Kembali ke daftar teman');
			const tanda = JSON.stringify([o.id, o.avatar, o.daring, o.dnd]);
			if (kepalaAvatar.getAttribute('data-tanda') !== tanda) {
				kepalaAvatar.setAttribute('data-tanda', tanda);
				ganti(kepalaAvatar, [avatar(o.avatar, { titik: titik(o) })]);
			}
			gambarObrolan(k);
		} else {
			if (idObrolan) draf.set(idObrolan, masukan.value);
			idObrolan = null;
			tandaPesan = '';
			el('kepala-nama').textContent = 'Teman';
			const total = k.online.length + k.offline.length;
			ket.textContent = k.masuk && k.keadaan === 'siap' ? `${k.online.length} online dari ${total} teman sekelas` : 'Teman sekelas';
			ket.title = '';
			ket.classList.remove('hidup');
			if (kepalaAvatar.hasAttribute('data-tanda')) {
				kepalaAvatar.removeAttribute('data-tanda');
				ganti(kepalaAvatar, []);
			}
			ganti(wadahPesan, []);
			gambarDiri(k);
			gambarDaftar(k);
		}
		aturAnimasi();
	}

	document.addEventListener('visibilitychange', () => {
		document.body.classList.toggle('tersembunyi', document.hidden);
		aturAnimasi();
	});
	window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', aturAnimasi);

	window.addEventListener('message', (e) => {
		const m = e.data;
		if (!m || typeof m !== 'object') return;
		if (m.jenis === 'keadaan') gambar(m);
		else if (m.jenis === 'animasi' && typeof m.avatar === 'string' && m.data && typeof m.data === 'object' && Array.isArray(m.kotak)) {
			// Paling banyak beberapa animasi disimpan di memori panel.
			if (dataAnimasi.size >= 8) dataAnimasi.delete(dataAnimasi.keys().next().value);
			dataAnimasi.set(m.avatar, { teks: JSON.stringify(m.data), kotak: m.kotak.map(Number) });
			aturAnimasi();
		} else if (m.jenis === 'gagal-kirim' && typeof m.teks === 'string' && masukan.value === '') {
			// Pesan tidak terkirim: kembalikan ke kotak tulis agar tidak hilang.
			masukan.value = m.teks;
			perbaruiKirim();
		}
	});

	dsw.kirim({ tindakan: 'siap' });
})();
