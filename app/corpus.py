"""Corpus pipeline (D2, L1): read -> parse -> chunk -> embed -> index. Idempotent and incremental.

Chunking: one chunk per '## [ID] Title' section. Passages are small, self-contained and carry a stable id,
so a citation resolves to exactly one chunk. Runbooks are written in SYMPTOM language and cross-reference the
cause-oriented documentation by id, which bridges the inverted retrieval direction (section 3).
Embedding: stateless hashed word uni+bigram vectors (dim 1024, l2-normalised) -> FAISS inner product.
Stateless means a changed chunk is re-embedded alone (hash of text -> cached vector); the index is rebuilt from vectors.
"""
import glob, hashlib, json, os, re, time
import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer
from .config import settings

DIM = 1024
STOP = set("a an the of to in on is are was be by for and or with as at it its this that from can may not no if then when which".split())
_vec = HashingVectorizer(n_features=DIM, alternate_sign=False, norm="l2", ngram_range=(1, 2),
                         stop_words=list(STOP), token_pattern=r"(?u)\b[a-zA-Z_][a-zA-Z_0-9]+\b", lowercase=True)

def embed(texts):
    return _vec.transform(texts).toarray().astype("float32")

def parse_file(path):
    raw = open(path, encoding="utf-8").read()
    meta = {}
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    body = raw
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1); meta[k.strip()] = v.strip()
        body = raw[m.end():]
    chunks = []
    for sec in re.split(r"\n(?=## \[)", "\n" + body):
        h = re.match(r"\s*## \[([^\]]+)\][ \t]*([^\n]*)\n(.*)", sec, re.S)
        if not h: continue
        cid, title, text = h.group(1), h.group(2).strip(), h.group(3).strip()
        kind = "runbook" if cid.startswith("RB-") else "policy" if cid.startswith("POL-") else "doc"
        chunks.append({"id": cid, "title": title, "text": text, "kind": kind,
                       "version": int(meta.get("version", 0)) or None, "source": os.path.relpath(path),
                       "hash": hashlib.sha256((cid + title + text).encode()).hexdigest()[:16]})
    return chunks

def build(index_dir=None, corpus_dir=None, force=False):
    t0 = time.time()
    index_dir = index_dir or settings.INDEX_DIR; corpus_dir = corpus_dir or settings.CORPUS_DIR
    os.makedirs(index_dir, exist_ok=True)
    files = sorted(glob.glob(os.path.join(corpus_dir, "**", "*.md"), recursive=True))
    chunks = [c for f in files for c in parse_file(f)]
    ids = [c["id"] for c in chunks]
    assert len(ids) == len(set(ids)), "duplicate chunk ids"
    cache = {}
    cpath, vpath = os.path.join(index_dir, "chunks.jsonl"), os.path.join(index_dir, "vectors.npy")
    if os.path.exists(cpath) and os.path.exists(vpath) and not force:
        old = [json.loads(l) for l in open(cpath)]; ov = np.load(vpath)
        if len(old) == len(ov): cache = {o["hash"]: ov[i] for i, o in enumerate(old)}
    todo = [c for c in chunks if c["hash"] not in cache]
    if todo:
        vs = embed([c["title"] + ". " + c["text"] for c in todo])
        for c, v in zip(todo, vs): cache[c["hash"]] = v
    mat = np.stack([cache[c["hash"]] for c in chunks]).astype("float32") if chunks else np.zeros((0, DIM), "float32")
    with open(cpath, "w") as f:
        for c in chunks: f.write(json.dumps(c) + "\n")
    np.save(vpath, mat)
    try:
        import faiss
        idx = faiss.IndexFlatIP(DIM); idx.add(mat); faiss.write_index(idx, os.path.join(index_dir, "faiss.index"))
        backend = "faiss"
    except Exception:
        backend = "numpy"
    dur = time.time() - t0
    manifest = {"built_at": time.time(), "built_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "pg_versions": sorted({c["version"] for c in chunks if c["version"]}), "chunks": len(chunks),
                "files": len(files), "re_embedded": len(todo), "reused": len(chunks) - len(todo),
                "duration_s": round(dur, 4), "chunks_per_s": round(len(chunks) / dur, 1) if dur else None,
                "vector_backend": backend, "embedder": f"hashing-uni-bigram-{DIM}"}
    json.dump(manifest, open(os.path.join(index_dir, "manifest.json"), "w"), indent=2)
    return manifest

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Build/refresh the corpus index (idempotent)")
    ap.add_argument("--force", action="store_true", help="re-embed everything")
    a = ap.parse_args()
    print(json.dumps(build(force=a.force), indent=2))
