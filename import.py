#!/usr/bin/env python3
"""Import a Yomitan dictionary zip into sqlite for yomitan.el.

Yomitan term entries (format 3) are arrays:
    [expression, reading, definitionTags, rules, score, glossary, sequence, termTags]
The glossary is a list whose items are either plain strings or structured-content
trees: {"tag","data","content"} nodes mimicking HTML. We store the raw JSON so a
real renderer can come later, plus a flattened plain-text form so lookup works now.

    ./import.py ~/torrents/jitendex-yomitan.zip
"""
import json, sqlite3, sys, zipfile, time, os, re
from render import render

DB = os.path.expanduser("~/.local/share/yomitan/dict.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS dict(
  id INTEGER PRIMARY KEY, title TEXT UNIQUE, revision TEXT, format INT,
  author TEXT, url TEXT, src_lang TEXT, tgt_lang TEXT, imported TEXT);
CREATE TABLE IF NOT EXISTS term(
  dict_id INT NOT NULL, expression TEXT, reading TEXT, deftags TEXT,
  rules TEXT, score INT, seq INT, termtags TEXT, plain TEXT, raw TEXT);
"""
INDEXES = """
CREATE INDEX IF NOT EXISTS term_expr ON term(expression);
CREATE INDEX IF NOT EXISTS term_read ON term(reading);
CREATE INDEX IF NOT EXISTS term_dict ON term(dict_id);
"""

def main(path):
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=OFF;")
    con.executescript(SCHEMA)
    z = zipfile.ZipFile(path)
    names = z.namelist()
    idx_name = next(n for n in names if n.endswith("index.json"))
    meta = json.loads(z.read(idx_name))
    title = meta.get("title", os.path.basename(path))
    con.execute("DELETE FROM term WHERE dict_id IN (SELECT id FROM dict WHERE title=?)", (title,))
    con.execute("DELETE FROM dict WHERE title=?", (title,))
    cur = con.execute(
        "INSERT INTO dict(title,revision,format,author,url,src_lang,tgt_lang,imported)"
        " VALUES(?,?,?,?,?,?,?,datetime('now'))",
        (title, str(meta.get("revision","")), meta.get("format") or meta.get("version"),
         meta.get("author",""), meta.get("url",""),
         meta.get("sourceLanguage",""), meta.get("targetLanguage","")))
    did = cur.lastrowid
    banks = sorted(n for n in names if re.search(r"term_bank_\d+\.json$", n))
    print(f"{title}: {len(banks)} term banks")
    t0, total = time.time(), 0
    for i, b in enumerate(banks, 1):
        rows = []
        for e in json.loads(z.read(b)):
            e = (e + [None] * 8)[:8]
            expr, read, deftags, rules, score, gloss, seq, termtags = e
            rows.append((did, expr, read, deftags, rules,
                         score if isinstance(score, int) else 0,
                         seq if isinstance(seq, int) else 0, termtags,
                         render(gloss or []), json.dumps(gloss, ensure_ascii=False)))
        con.executemany("INSERT INTO term(dict_id,expression,reading,deftags,rules,"
                        "score,seq,termtags,plain,raw) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        total += len(rows)
        if i % 20 == 0 or i == len(banks):
            con.commit()
            print(f"  {i:3}/{len(banks)}  {total:>7} entries  {time.time()-t0:5.1f}s", flush=True)
    print("  building indexes...", flush=True)
    con.executescript(INDEXES)
    con.commit()
    con.execute("PRAGMA optimize")
    con.close()
    print(f"done: {total} entries in {time.time()-t0:.1f}s -> {DB} "
          f"({os.path.getsize(DB)/1048576:.0f} MB)")

if __name__ == "__main__":
    main(os.path.expanduser(sys.argv[1]))
