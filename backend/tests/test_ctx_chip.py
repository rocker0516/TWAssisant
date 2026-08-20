from app.research.ctx_matrix.chip import chip_templates
from app.research.ctx_matrix.templates import expand


def test_chip_templates_cover_spec():
    tpls = chip_templates()
    ids = {t.id for t in tpls}
    # spec §5 的六個子家族都要在
    for prefix in ("A1", "A3", "B4", "C1", "D4", "E1", "F1", "F4"):
        assert any(i.startswith(prefix) for i in ids), prefix


def test_chip_templates_expand():
    out = expand(chip_templates())
    # A1 n∈{3,5,10,15} → 4 個；總展開數應遠大於模板數
    assert sum(1 for t in out if t.id.startswith("A1_")) == 4
    assert len(out) >= 40
