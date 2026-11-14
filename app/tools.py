"""Agent tools (A1): search_corpus, get_clause, get_alert_field, find_references. Timeouts enforced by caller."""
import json, os, re, threading, time
from collections import OrderedDict
import numpy as np
from .config import settings
from . import corpus, metrics

class Index:
    def __init__(self):
        self.lock = threading.Lock(); self.loaded_at = 0; self.chunks = []; self.by_id = {}; self.mat = None; self.faiss = None
        self.manifest = {}; self.qcache = OrderedDict()
    def load(self):
        d = settings.INDEX_DIR
        if not os.path.exists(os.path.join(d, "manifest.json")): corpus.build()
        self.chunks = [json.loads(l) for l in open(os.path.join(d, "chunks.jsonl"))]
        self.by_id = {c["id"]: c for c in self.chunks}
        self.mat = np.load(os.path.join(d, "vectors.npy"))
        self.manifest = json.load(open(os.path.join(d, "manifest.json")))
        try:
            import faiss; self.faiss = faiss.read_index(os.path.join(d, "faiss.index"))
        except Exception: self.faiss = None
        self.loaded_at = time.time()
        metrics.INDEX_CHUNKS.set(self.manifest["chunks"]); metrics.INDEX_BUILT.set(self.manifest["built_at"])
        if self.manifest.get("chunks_per_s"): metrics.INDEX_RATE.set(self.manifest["chunks_per_s"])
    def ensure(self):
        if self.mat is None:
            with self.lock:
                if self.mat is None: self.load()
    def _qvec(self, q):
        if q in self.qcache:
            self.qcache.move_to_end(q); metrics.CACHE.labels("embedding", "hit").inc(); return self.qcache[q]
        metrics.CACHE.labels("embedding", "miss").inc()
        v = corpus.embed([q])
        self.qcache[q] = v
        if len(self.qcache) > 2048: self.qcache.popitem(last=False)
        return v
    def search(self, query, k=4, version=None, kind=None):
        self.ensure()
        v = self._qvec(query)
        if self.faiss is not None: sc, ix = self.faiss.search(v, len(self.chunks)); sc, ix = sc[0], ix[0]
        else: s = (self.mat @ v[0]); ix = np.argsort(-s); sc = s[ix]
        out = []
        for s, i in zip(sc, ix):
            c = self.chunks[int(i)]
            if version and c["version"] and c["version"] != version: continue
            if kind and c["kind"] != kind: continue
            out.append({"id": c["id"], "title": c["title"], "kind": c["kind"], "version": c["version"], "score": round(float(s), 4), "text": c["text"]})
            if len(out) >= k: break
        return out

INDEX = Index()
REF_RE = re.compile(r"\b(PG\d+-[\w.\-]+?|RB-\d{3}|POL-[A-Z]+)(?=[\s,.;)\]]|$)")
IDENT_RE = re.compile(r"\b([a-z]+_[a-z_0-9]+|pg_[a-z_]+)\b")

def search_corpus(query, k=4, version=None, kind=None): return INDEX.search(query, k, version, kind)

def get_clause(clause_id, version=None):
    INDEX.ensure()
    c = INDEX.by_id.get(clause_id)
    if not c: return None
    if version and c["version"] and c["version"] != version: return None
    return {"id": c["id"], "title": c["title"], "kind": c["kind"], "version": c["version"], "score": None, "text": c["text"]}

def get_alert_field(req_dict, name):
    return req_dict.get(name, req_dict.get("labels", {}).get(name))

def find_references(text):
    return [r for r in dict.fromkeys(REF_RE.findall(text or ""))]

def identifiers(text): return IDENT_RE.findall(text or "")
