"""canonical JSON 指紋：key 順序／縮排不影響，值改變即變。"""

from __future__ import annotations

import json

from app.mlentry.fingerprint import canonical_hash, canonical_json


def test_canonical_hash_ignores_key_order_and_formatting():
    a = {"b": 1, "a": {"y": [1, 2], "x": "中"}}
    b = json.loads(json.dumps({"a": {"x": "中", "y": [1, 2]}, "b": 1}, indent=4))
    assert canonical_hash(a) == canonical_hash(b)
    assert len(canonical_hash(a)) == 12 and all(c in "0123456789abcdef" for c in canonical_hash(a))


def test_canonical_hash_changes_on_value_change():
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_canonical_json_is_compact_sorted_and_keeps_unicode():
    assert canonical_json({"b": 1, "a": "中"}) == '{"a":"中","b":1}'
