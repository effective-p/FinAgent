from __future__ import annotations

import re


def parse_field(xml_text: str, tag: str) -> str:
    """Claude XML 응답에서 <tag>value</tag> 또는 <string name="tag">value</string> 를 추출한다.

    태그 안 공백·개행을 strip해서 반환한다.
    찾지 못하면 빈 문자열을 반환한다.
    """
    # 1) <tag>...</tag> 형식
    m = re.search(rf"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>", xml_text, re.DOTALL)
    if m:
        return m.group(1).strip()

    # 2) <string name="tag">...</string> 형식
    m = re.search(
        rf'<string\s+name="{re.escape(tag)}">(.*?)</string>',
        xml_text,
        re.DOTALL,
    )
    if m:
        return m.group(1).strip()

    return ""


def parse_output(xml_text: str, *tags: str) -> dict[str, str]:
    """여러 태그를 한 번에 추출해서 {tag: value} dict 로 반환한다."""
    return {tag: parse_field(xml_text, tag) for tag in tags}
