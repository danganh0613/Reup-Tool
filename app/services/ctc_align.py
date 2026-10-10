"""Căn ép đúng chữ (CTC forced alignment) cho giọng tiếng Việt — cách WhisperX làm, chạy bằng numpy thuần.

Model: wav2vec2-base tiếng Việt 250 h (nguyenvulebinh — giấy phép CC-BY-NC 4.0, dùng phi thương mại), đổi một lần sang
numpy float16 (~190 MB) ở ``models/align_vi``. Cả lúc cài lẫn lúc chạy chỉ cần numpy (không torch / transformers —
bản đóng gói .exe không kèm hai thư viện này). Đo trên câu Edge TTS có mốc chữ chuẩn: lệch đều ±20 ms
(audit_out/word_sync)."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unicodedata
from collections.abc import Callable

import numpy as np

from app.config.paths import MODELS_DIR

REPO = "nguyenvulebinh/wav2vec2-base-vietnamese-250h"
MODEL_DIR = MODELS_DIR / "align_vi"
WEIGHTS = MODEL_DIR / "w2v_vi.npz"
VOCAB = MODEL_DIR / "vocab.json"
DOWNLOAD_MB, DISK_MB = 380, 190
SR = 16000
_STRIDES = (5, 2, 2, 2, 2, 2, 2)          # bộ trích đặc trưng: 7 lớp conv → mỗi khung 320 mẫu = 20 ms
_HEADS, _HIDDEN = 12, 768


def model_ready() -> bool:
    return WEIGHTS.exists() and VOCAB.exists()


def read_torch_zip(path) -> dict[str, np.ndarray]:
    """Đọc state dict PyTorch (file .bin dạng zip, torch ≥ 1.6) bằng zipfile + pickle — KHÔNG cần torch (bản đóng gói
    .exe không kèm torch). Chỉ dựng lại tensor (OrderedDict + _rebuild_tensor_v2), lớp lạ thì báo lỗi."""
    import collections
    import io
    import pickle
    import zipfile

    kinds = {"FloatStorage": np.float32, "HalfStorage": np.float16, "DoubleStorage": np.float64,
             "LongStorage": np.int64, "IntStorage": np.int32, "ShortStorage": np.int16, "CharStorage": np.int8,
             "ByteStorage": np.uint8, "BoolStorage": np.bool_}
    with zipfile.ZipFile(path) as z:
        pkl = next(n for n in z.namelist() if n.endswith("data.pkl"))
        root = pkl[:-len("data.pkl")]
        cache: dict[str, np.ndarray] = {}

        def rebuild(arr, offset, size, stride, *_):
            return np.lib.stride_tricks.as_strided(arr[offset:], shape=tuple(size),
                                                   strides=tuple(s * arr.itemsize for s in stride)).copy()

        class _Unpickler(pickle.Unpickler):
            def find_class(self, module, name):
                if module == "collections" and name == "OrderedDict":
                    return collections.OrderedDict
                if module == "torch._utils" and name == "_rebuild_tensor_v2":
                    return rebuild
                if module == "torch._utils" and name == "_rebuild_parameter":
                    return lambda data, *_: data
                if module == "torch" and name in kinds:
                    return kinds[name]
                raise pickle.UnpicklingError(f"Không đọc được lớp {module}.{name} trong file model")

            def persistent_load(self, pid):
                kind, key = pid[1], str(pid[2])           # ('storage', kiểu, khoá, thiết bị, số phần tử)
                if key not in cache:
                    cache[key] = np.frombuffer(z.read(f"{root}data/{key}"), dtype=kind)
                return cache[key]

        return dict(_Unpickler(io.BytesIO(z.read(pkl))).load())


def install(log: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> bool:
    """Tải model gốc (~380 MB) → đổi sang numpy float16 (~190 MB). Không cần torch (đọc file gốc bằng zipfile)."""
    from huggingface_hub import hf_hub_download

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=MODEL_DIR) as tmp:
        if log:
            log(f"Tải model căn chữ ({REPO}, ~{DOWNLOAD_MB} MB)…")
        vocab = hf_hub_download(REPO, "vocab.json", local_dir=tmp)
        bin_path = hf_hub_download(REPO, "pytorch_model.bin", local_dir=tmp)
        if cancel is not None and cancel.is_set():
            return False
        if log:
            log("Đổi model sang dạng gọn (~190 MB)…")
        sd = read_torch_zip(bin_path)
        pre = "wav2vec2.encoder.pos_conv_embed.conv."
        g = sd.pop(pre + "weight_g", None)
        v = sd.pop(pre + "weight_v", None)
        if g is None:                          # file lưu kiểu mới (parametrizations)
            g, v = sd.pop(pre + "parametrizations.weight.original0"), sd.pop(pre + "parametrizations.weight.original1")
        g, v = g.astype(np.float32), v.astype(np.float32)
        sd["pos_w"] = g * v / np.sqrt((v * v).sum(axis=(0, 1), keepdims=True))   # weight norm (dim=2) gộp sẵn
        sd.pop("wav2vec2.masked_spec_embed", None)
        part = MODEL_DIR / "w2v_vi.part.npz"
        np.savez(part, **{k.removeprefix("wav2vec2."): np.asarray(t, dtype=np.float32).astype(np.float16)
                          for k, t in sd.items()})
        shutil.copyfile(vocab, VOCAB)
        part.replace(WEIGHTS)
    if log:
        log("✓ Đã cài bộ căn chữ nổi.")
    return model_ready()


def _gelu(x: np.ndarray) -> np.ndarray:
    """GELU dạng tanh (lệch < 0,0005 so với bản erf — không đổi kết quả căn), tính tại chỗ cho đỡ cấp phát."""
    y = x * x
    y *= x
    y *= np.float32(0.044715)
    y += x
    y *= np.float32(0.7978845608)
    np.tanh(y, out=y)
    y += np.float32(1.0)
    y *= x
    y *= np.float32(0.5)
    return y


def _ln(x: np.ndarray, w: np.ndarray, b: np.ndarray, eps: float = 1e-5) -> np.ndarray:
    m = x.mean(-1, keepdims=True)
    d = x - m
    return d / np.sqrt((d * d).mean(-1, keepdims=True) + eps) * w + b


def _conv(x: np.ndarray, w: np.ndarray, stride: int) -> np.ndarray:
    """Conv1d không bias: x (T, Cin) · w (Cout, Cin, k) → (T', Cout)."""
    win = np.lib.stride_tricks.sliding_window_view(x, w.shape[2], axis=0)[::stride]     # (T', Cin, k)
    return win.reshape(win.shape[0], -1) @ w.reshape(w.shape[0], -1).T


def _viterbi(logp: np.ndarray, tokens: list[int], blank: int) -> np.ndarray | None:
    """Đường CTC tốt nhất đi qua đúng chuỗi ``tokens`` → (khung đầu, khung cuối) của từng token; None nếu không đủ khung."""
    T, L = logp.shape[0], len(tokens)
    S = 2 * L + 1
    z = np.full(S, blank, dtype=np.int64)
    z[1::2] = tokens
    skip = np.zeros(S, dtype=bool)          # được nhảy qua blank: hai token liền nhau khác nhau
    if L > 1:
        tk = np.asarray(tokens)
        skip[3::2] = tk[1:] != tk[:-1]
    neg = np.float32(-1e30)
    em = logp[:, z]
    dp = np.full(S, neg, dtype=np.float32)
    dp[0], dp[1] = em[0, 0], em[0, 1]
    back = np.zeros((T, S), dtype=np.int8)
    p1 = np.empty(S, dtype=np.float32)
    p2 = np.empty(S, dtype=np.float32)
    for t in range(1, T):
        p1[0] = neg
        p1[1:] = dp[:-1]
        p2[:2] = neg
        p2[2:] = dp[:-2]
        p2[~skip] = neg
        best = np.maximum(dp, p1)
        arg = (p1 > dp).astype(np.int8)
        m2 = p2 > best
        best[m2] = p2[m2]
        arg[m2] = 2
        dp = best + em[t]
        back[t] = arg
    s = S - 1 if dp[S - 1] >= dp[S - 2] else S - 2
    if dp[s] < -1e29:
        return None
    spans = np.full((L, 2), -1, dtype=np.int64)
    for t in range(T - 1, -1, -1):
        if s % 2:
            k = s // 2
            if spans[k, 1] < 0:
                spans[k, 1] = t
            spans[k, 0] = t
        s -= int(back[t, s])
    return spans


class Aligner:
    """wav2vec2-base CTC tiếng Việt (numpy). Nạp ~0,5 s, chạy ~0,15 × thời lượng giọng trên CPU."""

    def __init__(self) -> None:
        with np.load(WEIGHTS) as z:
            self.p = {k: z[k].astype(np.float32) for k in z.files}
        self.vocab: dict[str, int] = json.loads(VOCAB.read_text(encoding="utf-8"))
        self.blank, self.sep = self.vocab["<pad>"], self.vocab["|"]

    def logprobs(self, a: np.ndarray) -> np.ndarray:
        """Âm thanh 16 kHz mono → log-xác suất từng ký tự, mỗi khung 20 ms."""
        p = self.p
        x = ((a - a.mean()) / np.sqrt(a.var() + 1e-7)).astype(np.float32)[:, None]
        for i, s in enumerate(_STRIDES):
            x = _conv(x, p[f"feature_extractor.conv_layers.{i}.conv.weight"], s)
            if i == 0:                                       # GroupNorm 512 nhóm = chuẩn hoá từng kênh theo thời gian
                m = x.mean(0, keepdims=True)
                d = x - m
                x = d / np.sqrt((d * d).mean(0, keepdims=True) + 1e-5) \
                    * p["feature_extractor.conv_layers.0.layer_norm.weight"] \
                    + p["feature_extractor.conv_layers.0.layer_norm.bias"]
            x = _gelu(x)
        x = _ln(x, p["feature_projection.layer_norm.weight"], p["feature_projection.layer_norm.bias"])
        h = x @ p["feature_projection.projection.weight"].T + p["feature_projection.projection.bias"]
        T = h.shape[0]
        # vị trí: conv nhóm (16 nhóm × 48 kênh, nhân 128) + GELU
        win = np.lib.stride_tricks.sliding_window_view(np.pad(h, ((64, 64), (0, 0))), 128, axis=0)[:T]
        w = p["pos_w"]
        pos = np.empty_like(h)
        g = _HIDDEN // 16
        for gi in range(16):
            sl = slice(g * gi, g * gi + g)
            pos[:, sl] = win[:, sl, :].reshape(T, -1) @ w[sl].reshape(g, -1).T
        h = _ln(h + _gelu(pos + p["encoder.pos_conv_embed.conv.bias"]), p["encoder.layer_norm.weight"],
                p["encoder.layer_norm.bias"])
        hd = _HIDDEN // _HEADS
        for i in range(12):
            q = f"encoder.layers.{i}."
            Q, K, V = ((h @ p[q + f"attention.{n}_proj.weight"].T + p[q + f"attention.{n}_proj.bias"])
                       .reshape(T, _HEADS, hd).transpose(1, 0, 2) for n in "qkv")
            att = (Q * np.float32(hd ** -0.5)) @ K.transpose(0, 2, 1)
            att = np.exp(att - att.max(-1, keepdims=True))
            att /= att.sum(-1, keepdims=True)
            o = (att @ V).transpose(1, 0, 2).reshape(T, _HIDDEN)
            o = o @ p[q + "attention.out_proj.weight"].T + p[q + "attention.out_proj.bias"]
            h = _ln(h + o, p[q + "layer_norm.weight"], p[q + "layer_norm.bias"])
            f = _gelu(h @ p[q + "feed_forward.intermediate_dense.weight"].T + p[q + "feed_forward.intermediate_dense.bias"])
            f = f @ p[q + "feed_forward.output_dense.weight"].T + p[q + "feed_forward.output_dense.bias"]
            h = _ln(h + f, p[q + "final_layer_norm.weight"], p[q + "final_layer_norm.bias"])
        lg = h @ p["lm_head.weight"].T + p["lm_head.bias"]
        m = lg.max(-1, keepdims=True)
        return lg - m - np.log(np.exp(lg - m).sum(-1, keepdims=True))

    def ids(self, word: str) -> list[int]:
        return [self.vocab[ch] for ch in unicodedata.normalize("NFC", word.lower()) if ch in self.vocab and ch != "|"]

    def align(self, a: np.ndarray, words: list[str], spoken: list[str] | None = None) -> list[tuple[float, float]] | None:
        """(bắt đầu, kết thúc) từng chữ — giây tính từ đầu đoạn ``a``. ``spoken``: cách đọc từng chữ (số → chữ…).
        None nếu không căn được (đoạn quá ngắn so với số ký tự)."""
        if len(a) < SR * 0.2 or not words:
            return None
        logp = self.logprobs(a)
        return self.align_logp(logp, len(a) / SR / logp.shape[0], words, spoken)

    def align_logp(self, logp: np.ndarray, step: float, words: list[str],
                   spoken: list[str] | None = None) -> list[tuple[float, float]] | None:
        """Như ``align`` nhưng trên log-xác suất đã tính (một khúc của cả đoạn dài — chạy model theo khối lớn cho nhanh);
        mốc tính từ khung đầu của ``logp``."""
        if logp.shape[0] < 10 or not words:
            return None
        tokens: list[int] = []
        owner: list[int] = []
        for k, w in enumerate(spoken or words):
            ids = self.ids(w)
            if not ids:
                continue
            if tokens:
                tokens.append(self.sep)
                owner.append(-1)
            tokens += ids
            owner += [k] * len(ids)
        if not tokens:
            return None
        spans = _viterbi(logp, tokens, self.blank)
        if spans is None:
            return None
        first: list[float | None] = [None] * len(words)
        last: list[float | None] = [None] * len(words)
        for (f0, f1), k in zip(spans, owner):
            if k >= 0:
                if first[k] is None:
                    first[k] = f0 * step
                last[k] = (f1 + 1) * step
        if all(f is None for f in first):
            return None
        end_all = logp.shape[0] * step
        out: list[tuple[float, float]] = []
        for k in range(len(words)):
            if first[k] is None:                 # chữ không có ký tự đọc được (ký hiệu…): dính vào sau chữ trước
                t = out[-1][1] if out else 0.0
                out.append((t, t))
                continue
            nxt = next((first[j] for j in range(k + 1, len(words)) if first[j] is not None), None)
            # chữ kéo tới lúc chữ sau bắt đầu; trước chỗ nghỉ (≥ 0,25 s) thì dừng sau ký tự cuối một chút
            if nxt is not None and nxt - last[k] < 0.25:
                end = nxt
            else:
                end = min(end_all, last[k] + (0.12 if nxt is not None else 0.25))
            out.append((first[k], max(first[k] + 0.02, end)))
        return out
