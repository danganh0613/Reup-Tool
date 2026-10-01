"""Tên ngôn ngữ dễ hiểu cho mã ISO hiển thị trong giao diện: "vi" → "Tiếng Việt", "en-US" → "Tiếng Anh (Mỹ)".

Giá trị lưu trong cấu hình vẫn là mã (Whisper / Edge / Google cần mã); chỉ phần hiển thị dùng tên.
"""

from __future__ import annotations

LANGUAGE_NAMES: dict[str, str] = {
    "auto": "Tự nhận diện",
    "af": "Tiếng Afrikaans", "am": "Tiếng Amhara", "ar": "Tiếng Ả Rập", "az": "Tiếng Azerbaijan",
    "bg": "Tiếng Bulgaria", "bn": "Tiếng Bengal", "bs": "Tiếng Bosnia", "ca": "Tiếng Catalan",
    "cmn": "Tiếng Trung Phổ thông", "cs": "Tiếng Séc", "cy": "Tiếng Wales", "da": "Tiếng Đan Mạch",
    "de": "Tiếng Đức", "el": "Tiếng Hy Lạp", "en": "Tiếng Anh", "es": "Tiếng Tây Ban Nha",
    "et": "Tiếng Estonia", "eu": "Tiếng Basque", "fa": "Tiếng Ba Tư", "fi": "Tiếng Phần Lan",
    "fil": "Tiếng Philippines", "fr": "Tiếng Pháp", "ga": "Tiếng Ireland", "gl": "Tiếng Galicia",
    "gu": "Tiếng Gujarat", "he": "Tiếng Do Thái", "hi": "Tiếng Hindi", "hr": "Tiếng Croatia",
    "hu": "Tiếng Hungary", "hy": "Tiếng Armenia", "id": "Tiếng Indonesia", "is": "Tiếng Iceland",
    "it": "Tiếng Ý", "iu": "Tiếng Inuktitut", "ja": "Tiếng Nhật", "jv": "Tiếng Java",
    "ka": "Tiếng Gruzia", "kk": "Tiếng Kazakh", "km": "Tiếng Khmer", "kn": "Tiếng Kannada",
    "ko": "Tiếng Hàn", "lo": "Tiếng Lào", "lt": "Tiếng Litva", "lv": "Tiếng Latvia",
    "mk": "Tiếng Macedonia", "ml": "Tiếng Malayalam", "mn": "Tiếng Mông Cổ", "mr": "Tiếng Marathi",
    "ms": "Tiếng Mã Lai", "mt": "Tiếng Malta", "my": "Tiếng Miến Điện", "nb": "Tiếng Na Uy",
    "ne": "Tiếng Nepal", "nl": "Tiếng Hà Lan", "no": "Tiếng Na Uy", "pa": "Tiếng Punjab",
    "pl": "Tiếng Ba Lan", "ps": "Tiếng Pashto", "pt": "Tiếng Bồ Đào Nha", "ro": "Tiếng Romania",
    "ru": "Tiếng Nga", "si": "Tiếng Sinhala", "sk": "Tiếng Slovakia", "sl": "Tiếng Slovenia",
    "so": "Tiếng Somali", "sq": "Tiếng Albania", "sr": "Tiếng Serbia", "su": "Tiếng Sunda",
    "sv": "Tiếng Thụy Điển", "sw": "Tiếng Swahili", "ta": "Tiếng Tamil", "te": "Tiếng Telugu",
    "th": "Tiếng Thái", "tl": "Tiếng Tagalog", "tr": "Tiếng Thổ Nhĩ Kỳ", "uk": "Tiếng Ukraina",
    "ur": "Tiếng Urdu", "uz": "Tiếng Uzbek", "vi": "Tiếng Việt", "wuu": "Tiếng Ngô (Thượng Hải)",
    "yue": "Tiếng Quảng Đông", "zh": "Tiếng Trung", "zu": "Tiếng Zulu",
}

REGION_NAMES: dict[str, str] = {
    "VN": "Việt Nam", "US": "Mỹ", "GB": "Anh", "AU": "Úc", "CA": "Canada", "IN": "Ấn Độ", "IE": "Ireland",
    "NZ": "New Zealand", "ZA": "Nam Phi", "SG": "Singapore", "PH": "Philippines", "HK": "Hồng Kông",
    "KE": "Kenya", "NG": "Nigeria", "TZ": "Tanzania", "CN": "Trung Quốc", "TW": "Đài Loan", "JP": "Nhật Bản",
    "KR": "Hàn Quốc", "FR": "Pháp", "BE": "Bỉ", "CH": "Thụy Sĩ", "DE": "Đức", "AT": "Áo", "ES": "Tây Ban Nha",
    "MX": "Mexico", "AR": "Argentina", "CO": "Colombia", "CL": "Chile", "PE": "Peru", "VE": "Venezuela",
    "BR": "Brazil", "PT": "Bồ Đào Nha", "IT": "Ý", "NL": "Hà Lan", "RU": "Nga", "TH": "Thái Lan",
    "ID": "Indonesia", "MY": "Malaysia", "SA": "Ả Rập Xê Út", "AE": "UAE", "EG": "Ai Cập", "IL": "Israel",
    "TR": "Thổ Nhĩ Kỳ", "PL": "Ba Lan", "SE": "Thụy Điển", "NO": "Na Uy", "DK": "Đan Mạch", "FI": "Phần Lan",
    "UA": "Ukraina", "GR": "Hy Lạp", "CZ": "Séc", "HU": "Hungary", "RO": "Romania", "PK": "Pakistan",
    "BD": "Bangladesh", "LK": "Sri Lanka", "NP": "Nepal", "KH": "Campuchia", "LA": "Lào", "MM": "Myanmar",
    "US-POSIX": "Mỹ",
}


def language_label(code: str) -> str:
    """"vi" → "Tiếng Việt"; mã lạ giữ nguyên."""
    return LANGUAGE_NAMES.get((code or "").lower(), code)


def locale_label(code: str) -> str:
    """"en-US" → "Tiếng Anh (Mỹ)", "zh-CN-liaoning" → "Tiếng Trung (Trung Quốc, liaoning)", "vi" → "Tiếng Việt"."""
    if not code:
        return code
    parts = code.split("-")
    name = LANGUAGE_NAMES.get(parts[0].lower())
    if name is None:
        return code
    extra = [REGION_NAMES.get(p.upper(), p) for p in parts[1:]]
    return f"{name} ({', '.join(extra)})" if extra else name


class LocaleLabels(dict):
    """Truyền vào ``labels`` của Select/OptionRow khi danh sách mã chỉ biết lúc chạy (vd. giọng TTS):
    mọi mã đều tự ra tên qua ``locale_label``."""

    def get(self, key, default=None):          # noqa: D401 — Select/Popover chỉ dùng .get(v, v)
        return locale_label(key) if isinstance(key, str) else default

    def __bool__(self) -> bool:                 # dict rỗng là False → «labels or {}» sẽ bỏ mất nó
        return True
