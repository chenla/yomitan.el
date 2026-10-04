#!/usr/bin/env python3
"""Import CC-CEDICT and CC-Canto into the yomitan.el database.

These are not Yomitan dictionaries -- they are the CC-CEDICT text format:

    一世人 一世人 [yi1 shi4 ren2] {jat1 sai3 jan4} /the whole life/for a lifetime/
    TRAD    SIMP   [pinyin]       {jyutping}      /sense/sense/

CC-Canto carries the jyutping inline. Plain CC-CEDICT does not, so the separate
cccedict-canto-readings file is merged in by (traditional, simplified).

Both the traditional and simplified forms are indexed as expressions, and each
entry is indexed under several readings -- full jyutping, full pinyin, and the
toneless run-together forms people actually type -- so 一世人, 一世人, "jat1 sai3
jan4" and "jatsaijan" all find it.

All three sources are CC BY-SA 3.0/4.0:
  CC-CEDICT   its maintainers, from CEDICT (c) 1997-98 Paul Andrew Denisowski
  CC-Canto    (c) 2015-16 Pleco Software Incorporated
  readings    (c) 2015-16 Pleco Software Incorporated

    ./import-cedict.py            # imports whatever is present in ./data
"""
import json, os, re, sqlite3, sys, time, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB = os.path.expanduser("~/.local/share/yomitan/dict.db")

# Tolerant on purpose: some CC-Canto lines omit the closing slash, and some carry
# a trailing "# adapted from cc-cedict" comment. Requiring /.../ loses 8,883 of
# 34,335 lines -- a quarter of the dictionary, silently.
LINE = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]*)\](?:\s*\{([^}]*)\})?\s*(.*)$")
COMMENT = re.compile(r"\s+#\s.*$")
TONE = re.compile(r"[0-9]")

SOURCES = [
    ("cccanto-170202.zip", "CC-Canto (Cantonese)",
     "(c) 2015-16 Pleco Software, CC BY-SA 3.0", "yue"),
    ("cedict_1_0_ts_utf-8_mdbg.zip", "CC-CEDICT (Mandarin)",
     "CC-CEDICT maintainers, CC BY-SA 4.0", "zh"),
]
READINGS = "cccedict-canto-readings-150923.zip"

def lines_of(zip_name):
    path = os.path.join(DATA, zip_name)
    if not os.path.exists(path):
        return None
    z = zipfile.ZipFile(path)
    name = next(n for n in z.namelist() if n.endswith((".txt", ".u8")))
    for raw in z.read(name).decode("utf8", "ignore").splitlines():
        if raw.startswith("#") or not raw.strip():
            continue
        line = COMMENT.sub("", raw.rstrip())
        m = LINE.match(line)
        if m:
            trad, simp, pinyin, jyut, defs = m.groups()
            yield (trad, simp, pinyin, jyut, (defs or "").strip("/ "), raw)

def toneless(s):
    return TONE.sub("", s or "").replace(" ", "")

def load_readings():
    """(trad, simp) -> jyutping, and trad -> jyutping as a fallback."""
    pair, single = {}, {}
    rows = lines_of(READINGS)
    if rows is None:
        print(f"  note: {READINGS} not present -- CC-CEDICT will have no jyutping")
        return pair, single
    n = 0
    for trad, simp, _pin, jyut, _defs, _raw in rows:
        if not jyut:
            continue
        pair.setdefault((trad, simp), jyut)
        single.setdefault(trad, jyut)
        n += 1
    print(f"  merged {n} jyutping readings")
    return pair, single

def render(trad, simp, pinyin, jyut, defs):
    out = []
    head = []
    if jyut:   head.append("jyut " + jyut)
    if pinyin: head.append("pinyin " + pinyin)
    if simp and simp != trad: head.append("simp " + simp)
    if head:
        out.append(" · ".join(head))
    senses = [d.strip() for d in (defs or "").split("/") if d.strip()]
    for i, d in enumerate(senses, 1):
        out.append(f"  {i}. {d}")
    return "\n".join(out)

def readings_for(pinyin, jyut):
    """Every form someone might actually type.

    For 黐線 that is "ci1 sin3" (as the data has it), "ci1sin3" (how a jyutping
    IME user types it -- tones kept, spaces dropped), and "cisin" (no tones at
    all), plus the same three for pinyin. Omitting the middle one loses the most
    likely query of the three.
    """
    out = []
    for r in (jyut, jyut.replace(" ", "") if jyut else "", toneless(jyut),
              pinyin, pinyin.replace(" ", "") if pinyin else "", toneless(pinyin)):
        if r and r not in out:
            out.append(r)
    return out or [""]

def main():
    if not os.path.isdir(DATA):
        sys.exit(f"no {DATA} -- see README.org for the download commands")
    con = sqlite3.connect(DB)
    con.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=OFF;")
    pair, single = load_readings()
    for zip_name, title, attrib, lang in SOURCES:
        rows_in = lines_of(zip_name)
        if rows_in is None:
            print(f"  skip {zip_name} (not downloaded)")
            continue
        t0 = time.time()
        con.execute("DELETE FROM term WHERE dict_id IN (SELECT id FROM dict WHERE title=?)", (title,))
        con.execute("DELETE FROM dict WHERE title=?", (title,))
        cur = con.execute("INSERT INTO dict(title,revision,format,author,url,src_lang,tgt_lang,"
                          "imported) VALUES(?,?,?,?,?,?,?,datetime('now'))",
                          (title, zip_name, 0, attrib, "https://cantonese.org", lang, "en"))
        did, batch, n = cur.lastrowid, [], 0
        for trad, simp, pinyin, jyut, defs, raw in rows_in:
            if not jyut:
                jyut = pair.get((trad, simp)) or single.get(trad) or ""
            plain = render(trad, simp, pinyin, jyut, defs)
            if not plain:
                continue
            n += 1
            # he writes traditional: rank it above the simplified form, which is
            # otherwise a coin toss between two rows of equal score and length
            exprs = [(trad, 1)] + ([(simp, 0)] if simp and simp != trad else [])
            raw_json = json.dumps({"trad": trad, "simp": simp, "pinyin": pinyin,
                                   "jyutping": jyut, "defs": defs, "line": raw},
                                  ensure_ascii=False)
            for ex, rank in exprs:
                for rd in readings_for(pinyin, jyut):
                    batch.append((did, ex, rd, "", "", rank, 0, "", plain, raw_json))
            if len(batch) >= 40000:
                con.executemany("INSERT INTO term(dict_id,expression,reading,deftags,rules,"
                                "score,seq,termtags,plain,raw) VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
                batch = []
        if batch:
            con.executemany("INSERT INTO term(dict_id,expression,reading,deftags,rules,"
                            "score,seq,termtags,plain,raw) VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
        con.commit()
        k = con.execute("SELECT COUNT(*) FROM term WHERE dict_id=?", (did,)).fetchone()[0]
        print(f"  {title}: {n} entries, {k} index rows, {time.time()-t0:.1f}s")
    con.executescript("CREATE INDEX IF NOT EXISTS term_expr ON term(expression);"
                      "CREATE INDEX IF NOT EXISTS term_read ON term(reading);")
    con.commit(); con.close()
    print(f"db now {os.path.getsize(DB)/1048576:.0f} MB")

if __name__ == "__main__":
    main()
