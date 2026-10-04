#!/usr/bin/env python3
"""Import Unihan into the yomitan.el database as a CJK character dictionary.

No download: Debian ships Unihan at /usr/share/unicode/Unihan_*.txt.bz2.

Three things make this worth having alongside a Japanese dictionary:

  kCantonese   jyutping per character -- so 嘅 ge3, 冇 mou5, 喺 hai2, the
               Cantonese function words a Japanese dictionary has never heard of
  kJoyoKanji + kGradeLevel
               the two USAGE signals.  kJoyoKanji marks everyday Japanese (会 and
               学 have it, 會 and 學 do not); kGradeLevel is the Hong Kong primary
               grade by which a pupil should know a character (嗎 and 會 are grade
               1).  Together: 嗎 is basic Cantonese and not Japanese.
               kIRG_JSource does NOT mean this -- it covers JIS X 0212/0213, which
               encode many characters Japanese never uses, 嗎 among them.  Reported
               separately as "encoded in", never as usage.
  variants     kSimplifiedVariant / kTraditionalVariant, so 會 points at 会.

Readings are indexed, so lookup by jyutping works: "maa1" finds 嗎.

    ./import-unihan.py
"""
import bz2, json, os, re, sqlite3, sys, time

SRC = "/usr/share/unicode"
DB = os.path.expanduser("~/.local/share/yomitan/dict.db")

FILES = {
    "Readings": ("kCantonese", "kMandarin", "kJapaneseOn", "kJapaneseKun",
                 "kKorean", "kVietnamese", "kDefinition", "kHanyuPinyin"),
    "Variants": ("kSimplifiedVariant", "kTraditionalVariant", "kSemanticVariant"),
    "IRGSources": ("kTotalStrokes", "kRSUnicode", "kIRG_GSource", "kIRG_TSource",
                   "kIRG_JSource", "kIRG_KSource", "kIRG_KPSource", "kIRG_VSource",
                   "kIRG_HSource", "kIRG_MSource", "kIRG_USource", "kIRG_UKSource"),
    "DictionaryLikeData": ("kCangjie", "kFrequency", "kGradeLevel"),
    "OtherMappings": ("kJoyoKanji", "kJinmeiyoKanji"),
}
# IRG source tag -> the writing community that submitted it
REGION = {"G": "China", "T": "Taiwan", "J": "Japan", "K": "South Korea",
          "KP": "North Korea", "V": "Vietnam", "H": "Hong Kong", "M": "Macao",
          "U": "Unicode", "UK": "UK"}
ORDER = ["G", "T", "H", "J", "K", "KP", "V", "M", "U", "UK"]

def load():
    data = {}
    for stem, fields in FILES.items():
        path = os.path.join(SRC, f"Unihan_{stem}.txt.bz2")
        if not os.path.exists(path):
            sys.exit(f"missing {path} -- is the unicode-data package installed?")
        want = set(fields)
        with bz2.open(path, "rt", encoding="utf8") as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.rstrip("\n").split("\t", 2)
                if len(parts) != 3 or parts[1] not in want:
                    continue
                cp, field, val = parts
                data.setdefault(cp, {})[field] = val
    return data

def cp_to_char(cp):
    try:
        return chr(int(cp[2:], 16))
    except ValueError:
        return None

def variant_chars(val):
    """'U+4F1A' or 'U+4F1A<kMatchKangXi' -> the characters."""
    out = []
    for tok in val.split():
        m = re.match(r"U\+([0-9A-Fa-f]+)", tok)
        if m:
            c = chr(int(m.group(1), 16))
            if c not in out:
                out.append(c)
    return out

ALL = {}

def render(ch, e):
    lines = []
    head = []
    if e.get("kCantonese"):   head.append("jyut "  + e["kCantonese"])
    if e.get("kMandarin"):    head.append("pinyin " + e["kMandarin"])
    if e.get("kJapaneseOn"):  head.append("on "    + e["kJapaneseOn"])
    if e.get("kJapaneseKun"): head.append("kun "   + e["kJapaneseKun"])
    if e.get("kKorean"):      head.append("kor "   + e["kKorean"].lower())
    if head:
        lines.append(" · ".join(head))
    if e.get("kDefinition"):
        for i, d in enumerate(e["kDefinition"].split(";"), 1):
            d = d.strip()
            if d:
                lines.append(f"  {i}. {d}")
    # USAGE -- not the same thing as encoding
    use = []
    if e.get("kGradeLevel"):
        use.append(f"HK primary grade {e['kGradeLevel'].split()[0]}")
    if e.get("kJoyoKanji"):
        use.append("jōyō")
    elif e.get("kJinmeiyoKanji"):
        use.append("jinmeiyō (names only)")
    else:
        alt = []
        for field in ("kSimplifiedVariant", "kTraditionalVariant", "kSemanticVariant"):
            for c in variant_chars(e.get(field, "")):
                if ALL.get("U+%04X" % ord(c), {}).get("kJoyoKanji") and c not in alt:
                    alt.append(c)
        use.append("not jōyō" + (f" (Japanese uses {' '.join(alt)})" if alt else ""))
    if use:
        lines.append("  use: " + " · ".join(use))
    present = [t for t in ORDER if e.get(f"kIRG_{t}Source")]
    if present:
        lines.append(f"  encoded in: {' '.join(present)}")
    for field, label in (("kSimplifiedVariant", "simplified"),
                         ("kTraditionalVariant", "traditional"),
                         ("kSemanticVariant", "variant")):
        if e.get(field):
            cs = variant_chars(e[field])
            if cs:
                lines.append(f"  {label}: {' '.join(cs)}")
        
    bits = []
    if e.get("kTotalStrokes"): bits.append(e["kTotalStrokes"].split()[0] + " strokes")
    if e.get("kRSUnicode"):    bits.append("radical " + e["kRSUnicode"].split()[0])
    if e.get("kCangjie"):      bits.append("cangjie " + e["kCangjie"])
    if bits:
        lines.append("  " + " · ".join(bits))
    return "\n".join(lines)

def readings_of(e):
    out = []
    for r in (e.get("kCantonese") or "").split():
        if r not in out:
            out.append(r)
    for r in (e.get("kMandarin") or "").split():
        if r not in out:
            out.append(r)
    return out

def main():
    t0 = time.time()
    print("reading Unihan...", flush=True)
    data = load()
    ALL.update(data)
    title = "Unihan (CJK characters)"
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=OFF;")
    con.execute("DELETE FROM term WHERE dict_id IN (SELECT id FROM dict WHERE title=?)", (title,))
    con.execute("DELETE FROM dict WHERE title=?", (title,))
    cur = con.execute("INSERT INTO dict(title,revision,format,author,url,src_lang,tgt_lang,imported)"
                      " VALUES(?,?,?,?,?,?,?,datetime('now'))",
                      (title, "Unicode Unihan", 0, "Unicode Consortium",
                       "https://unicode.org/charts/unihan.html", "zh", "en"))
    did = cur.lastrowid
    rows, chars = [], 0
    for cp, e in data.items():
        ch = cp_to_char(cp)
        if not ch or not (e.get("kDefinition") or e.get("kCantonese") or e.get("kMandarin")):
            continue
        plain = render(ch, e)
        raw = json.dumps(e, ensure_ascii=False)
        score = -int(e.get("kFrequency", "9").split()[0]) if e.get("kFrequency") else -9
        reads = readings_of(e) or [""]
        chars += 1
        for r in reads:
            rows.append((did, ch, r, "", "", score, 0, "", plain, raw))
        if len(rows) >= 20000:
            con.executemany("INSERT INTO term(dict_id,expression,reading,deftags,rules,"
                            "score,seq,termtags,plain,raw) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
            rows = []
    if rows:
        con.executemany("INSERT INTO term(dict_id,expression,reading,deftags,rules,"
                        "score,seq,termtags,plain,raw) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM term WHERE dict_id=?", (did,)).fetchone()[0]
    con.close()
    print(f"done: {chars} characters, {n} reading-rows in {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main()
