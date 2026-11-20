import os

import pytest

from conftest import real_footage


def test_preset_name_is_stable():
    from autocut.pipeline import _slug, preset_for_refs

    assert preset_for_refs(["b", "a"]) == preset_for_refs(["a", "b"])
    assert preset_for_refs(["a"], "mine") == "mine"
    assert _slug("Funny 60 second vlog, with captions!") == "funny_60_second_vlog_with_captions"


@pytest.mark.skipif(not real_footage() or not os.environ.get("AUTOCUT_SLOW"),
                    reason="slow end-to-end test (set AUTOCUT_SLOW=1)")
def test_run_end_to_end(tmp_path):
    from autocut.pipeline import run
    from conftest import ROOT

    res = run([ROOT / "test_footage"], "calm 20 second vlog, no captions",
              refs=[ROOT / "references"], out_dir=tmp_path)
    o = res["outputs"][0]
    assert 17 <= o["duration"] <= 23 and os.path.exists(o["video"]) and os.path.exists(o["selection"])
