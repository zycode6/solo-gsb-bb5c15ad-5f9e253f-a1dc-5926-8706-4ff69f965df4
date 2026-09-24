"""公开版转写（public transcript）引擎与端到端 API 测试。

Run with: python3 test_public_transcript_api.py  (or via pytest).
"""
import os
import shutil
import tempfile

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="public-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.publication import build_public_transcript  # noqa: E402

# s1 touches s2 (end 2 == start 2) with the same speaker 李明, so a word can
# span the boundary; s3 leaves a gap (4 -> 4.5) which breaks the chain; s4
# starts at 6 (touching s3's end) but with a different speaker.
DOC = {
    "segments": [
        {"id": "s1", "start": 0, "end": 2, "speaker": "李明", "text": "我叫 李明"},
        {"id": "s2", "start": 2, "end": 4, "speaker": "李明", "text": "在厂上班"},
        {"id": "s3", "start": 4.5, "end": 6, "speaker": "李明",
         "text": "gap后的李明"},
        {"id": "s4", "start": 6, "end": 8, "speaker": "王主任", "text": "我是王主任"},
    ],
    "annotations": [
        {"id": "ann-1", "start": 0, "end": 8, "speaker": "外人不应出现",
         "topic": "校注不进入公开版"},
    ],
}


def _seg_map(res):
    return {s["id"]: s for s in res["segments"]}


def test_whitespace_insensitive_within_segment():
    res = build_public_transcript(DOC, ["李 明"], [])
    seg = _seg_map(res)["s1"]
    # the two matched non-space chars are blocked; the original space remains
    assert seg["text"] == "我叫 ██"
    assert res["summary"]["sensitiveWordHits"] == 2  # s1 + s3
    assert res["summary"]["maskedCharacters"] == 4


def test_hit_spans_touching_same_speaker_segments():
    # s1 ends with "李明" ... touching chain text is "我叫李明在厂上班";
    # "李明在" crosses the s1/s2 boundary.
    res = build_public_transcript(DOC, ["李明在"], [])
    segs = _seg_map(res)
    assert segs["s1"]["text"] == "我叫 ██"
    assert segs["s2"]["text"] == "█厂上班"
    assert res["summary"]["sensitiveWordHits"] == 1


def test_gap_overlap_and_speaker_switch_break_chains():
    # gap: s2 [2,4) and s3 [4.5,6) do not touch -> no cross-boundary hit
    res = build_public_transcript(DOC, ["上班gap"], [])
    assert res["summary"]["sensitiveWordHits"] == 0
    assert _seg_map(res)["s2"]["text"] == "在厂上班"

    # speaker switch at the touching s3/s4 boundary breaks the chain too
    res2 = build_public_transcript(DOC, ["李明我是"], [])
    assert res2["summary"]["sensitiveWordHits"] == 0

    # overlap breaks chains as well: a [0,2) and b [1,3) overlap
    doc = {"segments": [
        {"id": "a", "start": 0, "end": 2, "speaker": "X", "text": "ab"},
        {"id": "b", "start": 1, "end": 3, "speaker": "X", "text": "cd"},
    ]}
    res3 = build_public_transcript(doc, ["abcd"], [])
    assert res3["summary"]["sensitiveWordHits"] == 0


def test_overlapping_hits_masked_once():
    # "ab" masks {a,b}, "bc" masks {b,c} -> union {a,b,c}, b only once
    doc = {"segments": [{"id": "x", "start": 0, "end": 1,
                         "speaker": "X", "text": "abc"}]}
    res = build_public_transcript(doc, ["ab", "bc"], [])
    assert res["segments"][0]["text"] == "███"
    assert res["summary"]["sensitiveWordHits"] == 2
    assert res["summary"]["maskedCharacters"] == 3
    assert res["summary"]["maskedSegments"] == 1

    # identical words (after stripping whitespace) are deduplicated
    res2 = build_public_transcript(doc, ["ab", " a b "], [])
    assert res2["sensitiveWords"] == ["ab"]
    assert res2["summary"]["sensitiveWordCount"] == 1
    assert res2["summary"]["sensitiveWordHits"] == 1


def test_speaker_anonymization_numbering():
    # first appearance in TIME order: s1/s2 (李明, 0s) before s4 (王主任, 6s)
    res = build_public_transcript(DOC, [], ["王主任", "李明"])
    segs = _seg_map(res)
    assert segs["s1"]["speaker"] == "说话人1"
    assert segs["s2"]["speaker"] == "说话人1"
    assert segs["s3"]["speaker"] == "说话人1"
    assert segs["s4"]["speaker"] == "说话人2"
    labels = {sp["name"]: sp["label"] for sp in res["anonymousSpeakers"]}
    assert labels == {"李明": "说话人1", "王主任": "说话人2"}

    # not selecting a speaker leaves the name untouched
    res2 = build_public_transcript(DOC, [], ["李明"])
    assert _seg_map(res2)["s4"]["speaker"] == "王主任"

    # the same input always yields the same numbers (preview == download)
    again = build_public_transcript(DOC, [], ["王主任", "李明"])
    assert again["anonymousSpeakers"] == res["anonymousSpeakers"]


def test_segments_only_and_preservation():
    res = build_public_transcript(DOC, ["李明"], ["李明"])
    # no annotations, and each segment keeps exactly the public fields
    assert set(res) >= {"segments", "anonymousSpeakers", "sensitiveWords",
                        "summary"}
    assert "annotations" not in res
    for seg in res["segments"]:
        assert set(seg) == {"id", "start", "end", "speaker", "text"}
    # ids, times and segmentation are preserved
    ids_times = [(s["id"], s["start"], s["end"]) for s in res["segments"]]
    assert ids_times == [("s1", 0, 2), ("s2", 2, 4),
                         ("s3", 4.5, 6), ("s4", 6, 8)]
    # original order is preserved even when a later segment starts earlier
    doc = {"segments": [
        {"id": "late", "start": 5, "end": 6, "speaker": "A", "text": "二"},
        {"id": "early", "start": 0, "end": 1, "speaker": "A", "text": "一"},
    ]}
    res2 = build_public_transcript(doc, [], ["A"])
    assert [s["id"] for s in res2["segments"]] == ["late", "early"]
    # numbering follows first appearance in time (early [0,1) first), while
    # the output segmentation keeps the original document order
    assert [s["speaker"] for s in res2["segments"]] == ["说话人1", "说话人1"]


def test_invalid_segments_and_empty_input():
    doc = {"segments": [
        {"id": "bad", "start": 9, "end": 5, "speaker": "李明", "text": "丢弃"},
        {"id": "ok", "start": 0, "end": 1, "speaker": "李明", "text": "保留"},
    ]}
    res = build_public_transcript(doc, ["丢弃"], ["李明"])
    assert [s["id"] for s in res["segments"]] == ["ok"]
    # the selected speaker is still numbered even without a valid occurrence
    assert res["anonymousSpeakers"][0]["label"] == "说话人1"

    for words, speakers in (([], []), (["   "], ["  "])):
        try:
            build_public_transcript(DOC, words, speakers)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


def test_case_sensitivity():
    doc = {"segments": [{"id": "x", "start": 0, "end": 1,
                         "speaker": "A", "text": "AbC abc"}]}
    insensitive = build_public_transcript(doc, ["abc"], [])
    assert insensitive["segments"][0]["text"] == "███ ███"
    sensitive = build_public_transcript(doc, ["abc"], [], case_sensitive=True)
    assert sensitive["segments"][0]["text"] == "AbC ███"
    assert sensitive["summary"]["sensitiveWordHits"] == 1


def test_api():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        p = c.post("/api/projects", json={"name": "公开版访谈"}).json()
        pid = p["id"]
        c.put(f"/api/projects/{pid}",
              json={"name": "公开版访谈", "document": DOC})

        body = {"sensitiveWords": ["李 明"], "speakers": ["李明"],
                "caseSensitive": False}
        r = c.post(f"/api/projects/{pid}/public-transcript/preview",
                   json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["projectId"] == pid
        assert data["projectName"] == "公开版访谈"
        segs = {s["id"]: s for s in data["segments"]}
        assert segs["s1"]["text"] == "我叫 ██"
        assert segs["s1"]["speaker"] == "说话人1"
        # annotations never leak into the public copy
        assert all("topic" not in s for s in data["segments"])
        assert data["summary"]["maskedCharacters"] == 4

        # no words and no speakers -> 400
        assert c.post(f"/api/projects/{pid}/public-transcript/preview",
                      json={"sensitiveWords": [], "speakers": []}
                      ).status_code == 400

        # unknown project -> 404
        assert c.post("/api/projects/nope/public-transcript/preview",
                      json=body).status_code == 404

        # download returns an attachment with the same payload; the saved
        # project document must remain unchanged afterwards.
        r = c.post(f"/api/projects/{pid}/public-transcript/download",
                   json=body)
        assert r.status_code == 200, r.text
        cd = r.headers["content-disposition"]
        assert "attachment" in cd and "%E5%85%AC%E5%BC%80%E7%89%88" in cd
        pkg = r.json()
        assert pkg["segments"] == data["segments"]
        assert pkg["anonymousSpeakers"] == data["anonymousSpeakers"]
        assert pkg["summary"] == data["summary"]
        after = c.get(f"/api/projects/{pid}").json()
        assert after["document"] == DOC

        print("ALL PUBLIC-TRANSCRIPT API TESTS PASSED")


def run():
    test_whitespace_insensitive_within_segment()
    test_hit_spans_touching_same_speaker_segments()
    test_gap_overlap_and_speaker_switch_break_chains()
    test_overlapping_hits_masked_once()
    test_speaker_anonymization_numbering()
    test_segments_only_and_preservation()
    test_invalid_segments_and_empty_input()
    test_case_sensitivity()
    test_api()


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
