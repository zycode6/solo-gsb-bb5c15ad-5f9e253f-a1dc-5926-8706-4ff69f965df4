"""公开版转写引擎（public transcript）。

面向外发 / 公开场景，从**已保存**的转写片段生成一份只读公开版：

1. **敏感词遮盖**：匹配规则与引语检索一致——忽略查询词与片段文本中的所有
   Unicode 空白（``"李 明"`` 可命中文本 ``"李 明"`` 与 ``"李明"``），命中可
   **跨越多个片段**，但只拼接时间相接（上一片段 ``end`` 与下一片段 ``start``
   相等，允许微小浮点误差）且**说话人相同（非空）**的片段；片段间有空隙、
   重叠或说话人切换时，匹配链在该处断开。按命中字符在**原片段中的字符位置**
   把每个命中的**非空白字符**替换为方块 ``█``，原文空白原样保留；多个敏感词
   的命中（含相互重叠）按字符位置取并集，同一字符**只遮盖一次**。
2. **说话人匿名**：用户勾选的说话人在预览与下载中使用一致的匿名编号
   （``说话人1``、``说话人2`` …），编号按该说话人在时间顺序上的**首次出现**
   确定，未勾选的说话人名称原样保留。

生成结果**只含转写片段**（保留原片段 ID、起止时间与分段，不包含校注），
整个过程只读项目文档，原项目不会被修改。
"""
from __future__ import annotations

from typing import Any

EPS = 1e-6
BLOCK = "█"  # 遮盖一个命中字符所用的方块


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _normalize(word: str, case_sensitive: bool) -> str:
    """Drop all whitespace; optionally fold case for matching."""
    text = "".join(ch for ch in word if not ch.isspace())
    return text if case_sensitive else text.lower()


def _chain_key(seg: dict) -> tuple[float, float, int]:
    return (seg["start"], seg["end"], seg["_order"])


def _build_chains(segments: list[dict]) -> list[list[dict]]:
    """Group valid segments into maximal adjacent same-speaker runs.

    Mirrors :mod:`app.quotes`: two consecutive (time-sorted) segments are
    linked only when they touch in time (``prev.end == next.start``) and
    carry the same non-empty speaker. Gaps, overlaps, speaker switches and
    missing speakers break a chain.
    """
    ordered = sorted(segments, key=_chain_key)
    chains: list[list[dict]] = []
    for seg in ordered:
        if chains:
            prev = chains[-1][-1]
            connected = (
                bool(seg["speaker"])
                and seg["speaker"] == prev["speaker"]
                and abs(seg["start"] - prev["end"]) <= EPS
            )
            if connected:
                chains[-1].append(seg)
                continue
        chains.append([seg])
    return chains


def _collect_mask_positions(chain: list[dict], words: list[str],
                            mask: dict[int, set[int]],
                            case_sensitive: bool) -> int:
    """Find every (non-overlapping) word hit in one chain; mark masked chars.

    ``mask`` maps a segment's ``_order`` index to the set of original
    character positions to replace. Whitespace never enters the chain text
    and is therefore never marked. Returns the number of word hits found.
    """
    # (chain-local segment index, char index in the original segment text)
    positions: list[tuple[int, int]] = []
    parts: list[str] = []
    for si, seg in enumerate(chain):
        for ci, ch in enumerate(seg["text"]):
            if ch.isspace():
                continue
            positions.append((si, ci))
            parts.append(ch)
    hay = "".join(parts)
    if not case_sensitive:
        hay = hay.lower()
    if not hay:
        return 0

    hits = 0
    for raw_word, needle in words:
        if not needle:
            continue
        cursor = 0
        while True:
            at = hay.find(needle, cursor)
            if at < 0:
                break
            hits += 1
            for pos in range(at, at + len(needle)):
                si, ci = positions[pos]
                mask.setdefault(chain[si]["_order"], set()).add(ci)
            cursor = at + len(needle)
    return hits


def build_public_transcript(document: dict, raw_words: Any,
                            raw_speakers: Any,
                            case_sensitive: bool = False) -> dict:
    """Build the read-only public-transcript preview / download payload.

    See the module docstring for the masking and anonymization rules. The
    result contains only transcript segments (original ids, times and
    segmentation preserved); the saved document is never modified.
    """
    # de-duplicate words after whitespace stripping, keeping first-seen order
    words: list[tuple[str, str]] = []
    seen_words: set[str] = set()
    for raw in raw_words or []:
        if not isinstance(raw, str):
            continue
        needle = _normalize(raw, case_sensitive)
        if not needle or needle in seen_words:
            continue
        seen_words.add(needle)
        words.append((raw, needle))

    # de-duplicate selected speaker names (exact, non-empty match)
    selected: list[str] = []
    seen_speakers: set[str] = set()
    for raw in raw_speakers or []:
        if not isinstance(raw, str):
            continue
        name = raw.strip()
        if not name or name in seen_speakers:
            continue
        seen_speakers.add(name)
        selected.append(name)

    if not words and not selected:
        raise ValueError("请至少输入一个敏感词或选择一位需匿名的说话人")

    raw_segments = (document or {}).get("segments", [])
    if not isinstance(raw_segments, list):
        raw_segments = []

    valid: list[dict] = []
    for order, raw in enumerate(raw_segments):
        if not isinstance(raw, dict):
            continue
        start, end = _num(raw.get("start")), _num(raw.get("end"))
        if start is None or end is None or end < start:
            continue
        speaker = raw.get("speaker")
        valid.append({
            "_order": order,
            "start": start,
            "end": end,
            "speaker": str(speaker) if speaker is not None else "",
            "text": raw.get("text") if isinstance(raw.get("text"), str) else "",
        })

    selected_set = set(selected)

    # anonymous numbers follow the first appearance of each selected speaker
    # in time order (ties broken by original order)
    numbering: dict[str, int] = {}
    for seg in sorted(valid, key=_chain_key):
        if seg["speaker"] in selected_set and seg["speaker"] not in numbering:
            numbering[seg["speaker"]] = len(numbering) + 1
    # a name only present on ill-formed segments still gets a consistent number
    for name in selected:
        if name not in numbering:
            numbering[name] = len(numbering) + 1

    # ---- sensitive-word masking across time-touching same-speaker chains ----
    mask: dict[int, set[int]] = {}
    total_hits = 0
    if words:
        for chain in _build_chains(valid):
            total_hits += _collect_mask_positions(
                chain, words, mask, case_sensitive)

    masked_segment_count = 0
    masked_char_count = 0
    for order, positions in mask.items():
        if positions:
            masked_segment_count += 1
            masked_char_count += len(positions)

    def public_speaker(name: str) -> str:
        number = numbering.get(name)
        return f"说话人{number}" if number is not None else name

    # output keeps the ORIGINAL segment order and segmentation; every segment
    # record is reduced to id / start / end / speaker / text (no annotations)
    valid_by_order = {seg["_order"]: seg for seg in valid}
    segments_out: list[dict] = []
    for order in sorted(valid_by_order):
        seg = valid_by_order[order]
        chars = list(seg["text"])
        for ci in mask.get(order, ()):  # union of all hits; masked once
            chars[ci] = BLOCK
        segments_out.append({
            "id": str(raw_segments[order].get("id") or ""),
            "start": seg["start"],
            "end": seg["end"],
            "speaker": public_speaker(seg["speaker"]),
            "text": "".join(chars),
        })

    return {
        "segments": segments_out,
        "anonymousSpeakers": [
            {"name": name, "label": f"说话人{numbering[name]}",
             "number": numbering[name]}
            for name in sorted(numbering, key=lambda n: numbering[n])
        ],
        "sensitiveWords": [raw for raw, _ in words],
        "summary": {
            "segmentCount": len(segments_out),
            "sensitiveWordCount": len(words),
            "sensitiveWordHits": total_hits,
            "maskedSegments": masked_segment_count,
            "maskedCharacters": masked_char_count,
            "anonymousSpeakerCount": len(numbering),
        },
    }
