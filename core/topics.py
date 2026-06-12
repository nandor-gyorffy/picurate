"""CLIP-based topic tagging and semantic text search.

Model loading is lazy and optional.  All public functions return empty /
zero results when ONNX model files haven't been downloaded yet.

Expected files in  <data_dir>/clip/:
    clip_visual.onnx         — ViT image encoder (input: pixel_values [1,3,224,224])
    clip_text.onnx           — text encoder     (input: input_ids [1,77])
    bpe_simple_vocab_16e6.txt.gz — CLIP BPE vocabulary (OpenAI release)
"""
from __future__ import annotations

import gzip
import html
import json
import re
import unicodedata
from ftplib import all_errors
from pathlib import Path
from typing import Any

import numpy as np

from core.db.catalog import CatalogWriter, get_connection
from core.logger import get_logger
from core.paths import data_dir
from core.tags import add_photo_tag, get_or_create_tag

log = get_logger("picurate.topics")

DEFAULT_LABELS: list[str] = [
    "portrait", "group photo", "selfie",
    "landscape", "nature", "mountains", "beach", "forest", "desert", "snow",
    "cityscape", "architecture", "street photography", "interior",
    "sunset", "sunrise", "night", "fireworks",
    "animals", "pets", "wildlife",
    "food", "drink",
    "sports", "action",
    "travel", "vacation",
    "family", "children", "wedding", "party", "celebration",
    "flowers", "plants", "water", "sky", "clouds",
    "abstract", "black and white", "macro",
    "vehicles", "boats", "aircraft",
    "art", "music",
]

# ── Lazy model handles ─────────────────────────────────────────────────────────

_visual_session: Any = None
_text_session: Any = None
_tokenizer: Any = None
_model_ready = False


def _clip_dir() -> Path:
    return data_dir() / "clip"


def model_available() -> bool:
    d = _clip_dir()
    return (d / "clip_visual.onnx").exists() and (d / "clip_text.onnx").exists()


def _get_visual():
    global _visual_session, _model_ready
    if _visual_session is not None:
        return _visual_session
    try:
        import onnxruntime as ort
        _visual_session = ort.InferenceSession(
            str(_clip_dir() / "clip_visual.onnx"),
            providers=["CPUExecutionProvider"],
        )
        log.info("CLIP visual encoder loaded")
        return _visual_session
    except Exception as exc:
        log.warning("CLIP visual encoder unavailable: %s", exc)
        return None


def _get_text():
    global _text_session
    if _text_session is not None:
        return _text_session
    try:
        import onnxruntime as ort
        _text_session = ort.InferenceSession(
            str(_clip_dir() / "clip_text.onnx"),
            providers=["CPUExecutionProvider"],
        )
        log.info("CLIP text encoder loaded")
        return _text_session
    except Exception as exc:
        log.warning("CLIP text encoder unavailable: %s", exc)
        return None


# ── Minimal CLIP BPE tokenizer ─────────────────────────────────────────────────

def _bytes_to_unicode() -> dict[int, str]:
    bs = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("¡"), ord("¬") + 1))
        + list(range(ord("®"), ord("ÿ") + 1))
    )
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, [chr(c) for c in cs]))


def _get_pairs(word: tuple) -> set:
    return {(word[i], word[i + 1]) for i in range(len(word) - 1)}


class _SimpleTokenizer:
    SOT = 49406
    EOT = 49407
    CONTEXT = 77

    def __init__(self, vocab_path: str) -> None:
        self.byte_encoder = _bytes_to_unicode()
        self.byte_decoder = {v: k for k, v in self.byte_encoder.items()}
        with gzip.open(vocab_path, "rt", encoding="utf-8") as f:
            merges = f.read().split("\n")
        merges = merges[1 : 49152 - 256 - 2 + 1]
        merges = [tuple(m.split()) for m in merges]
        vocab = list(self.byte_encoder.values())
        vocab += [v + "</w>" for v in vocab]
        for merge in merges:
            vocab.append("".join(merge))
        vocab += ["<|startoftext|>", "<|endoftext|>"]
        self.encoder = dict(zip(vocab, range(len(vocab))))
        self.decoder = {v: k for k, v in self.encoder.items()}
        self.bpe_ranks = dict(zip(merges, range(len(merges))))
        self.cache = {"<|startoftext|>": "<|startoftext|>", "<|endoftext|>": "<|endoftext|>"}
        self.pat = re.compile(
            r"""<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|"""
            r"""[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+""",
            re.IGNORECASE,
        )

    def _bpe(self, token: str) -> str:
        if token in self.cache:
            return self.cache[token]
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        pairs = _get_pairs(word)
        if not pairs:
            return token + "</w>"
        while True:
            bigram = min(pairs, key=lambda p: self.bpe_ranks.get(p, float("inf")))
            if bigram not in self.bpe_ranks:
                break
            first, second = bigram
            new_word: list[str] = []
            i = 0
            while i < len(word):
                try:
                    j = word.index(first, i)
                except ValueError:
                    new_word.extend(word[i:])
                    break
                new_word.extend(word[i:j])
                i = j
                if i < len(word) - 1 and word[i + 1] == second:
                    new_word.append(first + second)
                    i += 2
                else:
                    new_word.append(word[i])
                    i += 1
            word = tuple(new_word)
            if len(word) == 1:
                break
            pairs = _get_pairs(word)
        result = " ".join(word)
        self.cache[token] = result
        return result

    def encode(self, text: str) -> list[int]:
        bpe_tokens: list[int] = []
        text = html.unescape(unicodedata.normalize("NFC", text)).lower()
        for token in re.findall(self.pat, text):
            token = "".join(self.byte_encoder[b] for b in token.encode("utf-8"))
            bpe_tokens.extend(self.encoder[bpe_tok] for bpe_tok in self._bpe(token).split(" "))
        return bpe_tokens

    def tokenize(self, texts: list[str]) -> np.ndarray:
        result = np.zeros((len(texts), self.CONTEXT), dtype=np.int64)
        for i, text in enumerate(texts):
            tokens = [self.SOT] + self.encode(text)[: self.CONTEXT - 2] + [self.EOT]
            result[i, : len(tokens)] = tokens
        return result


def _get_tokenizer() -> "_SimpleTokenizer | None":
    global _tokenizer
    if _tokenizer is not None:
        return _tokenizer
    vocab_path = _clip_dir() / "bpe_simple_vocab_16e6.txt.gz"
    if not vocab_path.exists():
        return None
    try:
        _tokenizer = _SimpleTokenizer(str(vocab_path))
        return _tokenizer
    except Exception as exc:
        log.warning("CLIP tokenizer load failed: %s", exc)
        return None


# ── Image pre-processing ───────────────────────────────────────────────────────

_CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_CLIP_STD  = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def _preprocess_image(file_path: str | Path) -> np.ndarray | None:
    """Load and normalize an image to the CLIP input format [1, 3, 224, 224]."""
    try:
        from PIL import Image
        img = Image.open(file_path).convert("RGB")
        img = img.resize((224, 224), Image.BICUBIC)
        arr = np.array(img, dtype=np.float32) / 255.0
        arr = (arr - _CLIP_MEAN) / _CLIP_STD
        return arr.transpose(2, 0, 1)[np.newaxis]  # [1, 3, 224, 224]
    except Exception as exc:
        log.warning("Image preprocess failed for %s: %s", file_path, exc)
        return None


# ── Core embedding functions ───────────────────────────────────────────────────

def get_image_embedding(file_path: str | Path) -> np.ndarray | None:
    """Return a unit-normalized CLIP image embedding, or None if unavailable."""
    session = _get_visual()
    if session is None:
        return None
    pixels = _preprocess_image(file_path)
    if pixels is None:
        return None
    try:
        name = session.get_inputs()[0].name
        out = session.run(None, {name: pixels})[0][0]  # [512]
        norm = np.linalg.norm(out)
        return (out / norm).astype(np.float32) if norm > 0 else out
    except Exception as exc:
        log.warning("CLIP image inference failed: %s", exc)
        return None


def get_text_embedding(text: str) -> np.ndarray | None:
    """Return a unit-normalized CLIP text embedding, or None if unavailable."""
    session = _get_text()
    if session is None:
        return None
    tok = _get_tokenizer()
    if tok is None:
        return None
    try:
        ids = tok.tokenize([text])
        name = session.get_inputs()[0].name
        out = session.run(None, {name: ids})[0][0]  # [512]
        norm = np.linalg.norm(out)
        return (out / norm).astype(np.float32) if norm > 0 else out
    except Exception as exc:
        log.warning("CLIP text inference failed: %s", exc)
        return None


# ── Tagging ────────────────────────────────────────────────────────────────────

def tag_photo(
    photo_id: int,
    file_path: str | Path,
    catalog_path: Path | None = None,
    labels: list[str] | None = None,
    threshold: float = 0.22,
) -> list[str]:
    """Run CLIP zero-shot scoring against labels and store tags above threshold.

    Returns the list of tag names assigned.  Returns [] when models unavailable.
    """
    if not model_available():
        return []

    img_emb = get_image_embedding(file_path)
    if img_emb is None:
        return []

    label_list = labels or DEFAULT_LABELS
    prompts = [f"a photo of {lbl}" for lbl in label_list]
    text_embs = []
    for prompt in prompts:
        emb = get_text_embedding(prompt)
        if emb is None:
            return []
        text_embs.append(emb)

    text_mat = np.stack(text_embs)   # [n_labels, 512]
    scores = (text_mat @ img_emb).tolist()  # [n_labels]

    # Store clip_embedding on the photo row
    with CatalogWriter(catalog_path) as conn:
        conn.execute(
            "UPDATE photos SET clip_embedding=? WHERE id=?",
            (json.dumps(img_emb.tolist()), photo_id),
        )

    assigned: list[str] = []
    for label, score in zip(label_list, scores):
        if score >= threshold:
            tag_id = get_or_create_tag(label, "auto", catalog_path)
            add_photo_tag(photo_id, tag_id, confidence=float(score), source="clip", catalog_path=catalog_path)
            assigned.append(label)

    return assigned


def tag_photos_batch(catalog_path: Path | None = None) -> dict:
    """Enqueue clip_tag jobs for all photos that don't yet have a clip_embedding."""
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT id, file_path FROM photos
           WHERE status NOT IN ('missing', 'duplicate') AND clip_embedding IS NULL"""
    ).fetchall()
    with CatalogWriter(catalog_path) as wconn:
        for row in rows:
            wconn.execute(
                "INSERT INTO jobs(job_type, payload, status) VALUES('clip_tag',?,?)",
                (json.dumps({"photo_id": row["id"], "path": row["file_path"]}), "pending"),
            )
    return {"enqueued": len(rows)}


# ── Semantic search ────────────────────────────────────────────────────────────

def search_photos_by_text(
    query: str,
    catalog_path: Path | None = None,
    limit: int = 200,
) -> list[int]:
    """Return photo ids ordered by CLIP similarity to the text query.

    Falls back to a keyword search in filename/caption/keywords when CLIP
    models are not available.
    """
    conn = get_connection(catalog_path)

    if model_available():
        q_emb = get_text_embedding(query)
        if q_emb is not None:
            rows = conn.execute(
                "SELECT id, clip_embedding FROM photos WHERE clip_embedding IS NOT NULL"
            ).fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                embs = np.array([json.loads(r["clip_embedding"]) for r in rows], dtype=np.float32)
                scores = embs @ q_emb
                order = np.argsort(-scores)
                return [ids[i] for i in order[:limit]]

    # Keyword fallback
    like = f"%{query}%"
    rows = conn.execute(
        """SELECT id FROM photos
           WHERE status NOT IN ('missing', 'duplicate')
             AND (filename LIKE ? OR caption LIKE ? OR keywords LIKE ?)
           LIMIT ?""",
        (like, like, like, limit),
    ).fetchall()
    return [r["id"] for r in rows]
