"""Content fingerprint（Spec A §0 規則 7）：全專案只保留一種指紋規格。

canonical JSON = sort_keys、無空白、保留 unicode、非 JSON 型別以 str() 序列化；hash = sha256[:12]。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()[:12]
