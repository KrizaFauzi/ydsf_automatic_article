# ─────────────────────────────────────────────
# INI ADALAH SEBUAH FUNGSI
# Menerima 2 input: topik (teks) dan llm (objek AI)
# Mengembalikan 1 output: dict berisi query per seksi
# ─────────────────────────────────────────────
def layered_query_expansion(topic: str, llm) -> dict:

    month_year = datetime.now().strftime("%B %Y")
    # month_year sekarang berisi misalnya: "March 2026"
    # Ini dipakai nanti di dalam prompt agar query selalu fresh


    # ══════════════════════════════════════════
    # LAYER 1
    # Kita tulis instruksi untuk LLM (ini namanya "prompt")
    # Lalu kita kirim ke LLM, dan LLM membalas dengan JSON
    # Balasan itu kita simpan ke variabel bernama "universal"
    # ══════════════════════════════════════════

    universal_prompt = f"""
Topik: "{topic}"
Buat dalam format JSON: {{ ... }}
"""
    #        ↑ f-string: {topic} akan diganti nilai variabel
    #          jadi kalau topic = "Konflik Gaza", maka
    #          prompt-nya: Topik: "Konflik Gaza"

    universal = json.loads(llm.invoke(universal_prompt))
    #           ↑ json.loads = ubah teks JSON menjadi dict Python
    #                          llm.invoke = kirim prompt, dapat balasan teks


    # ══════════════════════════════════════════
    # LAYER 2
    # Sama persis polanya dengan Layer 1:
    # Tulis prompt → kirim ke LLM → simpan hasilnya
    # Bedanya: prompt ini minta query per seksi artikel
    # ══════════════════════════════════════════

    per_section_prompt = f"""
Topik: "{topic}"
Buat sub-query untuk tiap seksi: {{ ... }}
"""

    per_section = json.loads(llm.invoke(per_section_prompt))
    #             ↑ hasilnya adalah dict dengan key:
    #               "latar_belakang", "ringkasan", "tokoh",
    #               "konflik", "prediksi"
    #               masing-masing berisi list of string (query)


    # ══════════════════════════════════════════
    # LAYER 3
    # Tidak pakai LLM — ini logika Python biasa
    # Tujuan: bersihkan dan batasi jumlah query
    # ══════════════════════════════════════════

    # Langkah 3a: Gabungkan SEMUA query jadi satu list panjang
    all_queries_flat = (
        universal["entities"] +       # ["Gaza", "Jalur Gaza", ...]
        universal["temporal"] +       # ["Konflik Gaza Maret 2026", ...]
        universal["paraphrases"] +    # ["Gaza war 2026", ...]
        [q for section in per_section.values() for q in section]
        #  ↑ ini cara Python mengambil semua query dari semua seksi
        #    hasilnya: ["sejarah Gaza 1948", "situasi terkini", ...]
    )

    # Langkah 3b: Buang duplikat
    seen = set()      # tempat "catat yang sudah pernah ada"
    deduped = []      # list bersih tanpa duplikat
    for q in all_queries_flat:
        key = q.lower().strip()               # samakan huruf besar/kecil
        if key not in seen and len(key.split()) > 1:  # belum ada & bukan 1 kata
            seen.add(key)    # catat sudah pernah ada
            deduped.append(q)  # masukkan ke list bersih


    # ══════════════════════════════════════════
    # RETURN — kembalikan hasilnya
    # Format: dict dengan key per seksi
    # ══════════════════════════════════════════

    result = {
        "universal": deduped[:5],   # ambil 5 query universal teratas
        **per_section               # masukkan semua seksi (latar_belakang, dst)
        # ** artinya: "bongkar isi dict ini dan masukkan ke sini"
    }

    return result
    # Yang dikembalikan contohnya:
    # {
    #   "universal": ["Gaza Maret 2026", "Gaza war 2026", ...],
    #   "latar_belakang": ["sejarah Israel Palestina", ...],
    #   "ringkasan": ["situasi Gaza terkini", ...],
    #   "tokoh": ["Netanyahu Hamas pemimpin", ...],
    #   "konflik": ["serangan militer Gaza", ...],
    #   "prediksi": ["proyeksi resolusi konflik Gaza", ...]
    # }
