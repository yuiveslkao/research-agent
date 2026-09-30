import pytest

from rharness.spec import SpecError, canonical_json, load_spec, spec_hash, validate
from conftest import make_spec


def test_hash_ignores_comments_and_key_order(tmp_path):
    a = make_spec(tmp_path)
    spec_a, _ = load_spec(a)
    text = a.read_text().replace('purpose = "test"', '# a comment\npurpose = "test"')
    b = tmp_path / "b.toml"
    b.write_text(text)
    spec_b, _ = load_spec(b)
    assert spec_hash(spec_a) == spec_hash(spec_b)
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_hash_changes_with_params(tmp_path):
    spec, _ = load_spec(make_spec(tmp_path))
    other = {**spec, "params": {"x": 2}}
    assert spec_hash(spec) != spec_hash(other)


@pytest.mark.parametrize("mutate,msg", [
    (lambda s: s.update(schema="run/9"), "schema"),
    (lambda s: s.update(mode="confirmation"), "confirmation"),
    (lambda s: s["resources"].update(gpu=True), "gpu"),
    (lambda s: s["resources"].update(mem_gb=64), "mem_gb"),
    (lambda s: s["code"].update(cmd="julia x.jl"), "argv"),
    (lambda s: s["numerics"].update(determinism="tolerance"), "rel_tol"),
    (lambda s: s["numerics"].update(device="gpu"), "cpu"),
    (lambda s: s["code"].update(paths=[]), "code.paths"),
    (lambda s: s["code"].update(paths=["../etc"]), "without '..'"),
    (lambda s: s["code"].update(paths=["/etc"]), "relative"),
    (lambda s: s["code"].update(paths=["--output=x"]), "relative"),
])
def test_validation_rejects(tmp_path, mutate, msg):
    spec, _ = load_spec(make_spec(tmp_path))
    mutate(spec)
    with pytest.raises(SpecError, match=msg):
        validate(spec)
