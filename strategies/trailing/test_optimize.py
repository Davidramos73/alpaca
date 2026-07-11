import importlib.util
import json
import os


def _load_trailing_optimize():
    path = os.path.join(os.path.dirname(__file__), "optimize.py")
    spec = importlib.util.spec_from_file_location("trailing_optimize_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trailing_optimize = _load_trailing_optimize()
regenerate_manifest = trailing_optimize.regenerate_manifest


def _write_equity_json(path, **overrides):
    payload = {
        "symbol": "TSLA",
        "date_start": "2026-01-01",
        "date_end": "2026-06-28",
        "interval_minutes": 1,
        "starting_cash": 100000.0,
    }
    payload.update(overrides)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_groups_base_and_trail_files_into_one_run(tmp_path):
    symbol_dir = tmp_path / "TSLA"
    symbol_dir.mkdir()
    _write_equity_json(symbol_dir / "optimize_TSLA_20260710_120000_equity.json")
    _write_equity_json(symbol_dir / "optimize_TSLA_20260710_120000_trail_0.5_equity.json")
    _write_equity_json(symbol_dir / "optimize_TSLA_20260710_120000_trail_1.0_equity.json")

    regenerate_manifest(str(tmp_path))

    with open(tmp_path / "manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert len(manifest) == 1
    run = manifest[0]
    assert run["symbol"] == "TSLA"
    assert run["run_ts"] == "20260710_120000"
    assert run["base_file"] == "TSLA/optimize_TSLA_20260710_120000_equity.json"
    assert [t["trail_pct"] for t in run["trail_files"]] == [0.5, 1.0]
    assert run["trail_files"][0]["file"] == "TSLA/optimize_TSLA_20260710_120000_trail_0.5_equity.json"


def test_run_without_base_file_is_dropped(tmp_path):
    symbol_dir = tmp_path / "TSLA"
    symbol_dir.mkdir()
    _write_equity_json(symbol_dir / "optimize_TSLA_20260710_130000_trail_0.5_equity.json")

    regenerate_manifest(str(tmp_path))

    with open(tmp_path / "manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest == []


def test_multiple_runs_sorted_by_run_ts_desc(tmp_path):
    symbol_dir = tmp_path / "TSLA"
    symbol_dir.mkdir()
    _write_equity_json(symbol_dir / "optimize_TSLA_20260710_090000_equity.json")
    _write_equity_json(symbol_dir / "optimize_TSLA_20260710_150000_equity.json")

    regenerate_manifest(str(tmp_path))

    with open(tmp_path / "manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert [r["run_ts"] for r in manifest] == ["20260710_150000", "20260710_090000"]
