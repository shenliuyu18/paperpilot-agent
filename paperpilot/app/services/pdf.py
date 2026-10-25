from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from pathlib import Path

import pymupdf

from .. import config
from . import identifiers, ocr

HEADING_FULL = re.compile(r"^(?:[IVXLC]+)\.\s+[A-Z][A-Z0-9 ,/&:()\-]{2,90}$")
HEADING_SUB = re.compile(r"^[A-Z]\.\s+[A-Z][A-Za-z0-9 ,/&:()\-]{2,90}$")
HEADING_NUM = re.compile(r"^\d+(?:\.\d+){0,3}\.?\s+[A-Z][A-Za-z0-9 ,/&:()\-]{2,90}$")
HEADING_CAPS = re.compile(r"^[A-Z][A-Z0-9 ,/&:()\-]{3,90}$")
NON_HEADING = re.compile(r"^(fig(?:ure)?\.?\s*\d|table\s+[ivxlc\d]|https?://|doi\b|arxiv\b|keywords?\b|index terms\b)", re.I)
CAPTION_RE = re.compile(
    r"^(fig(?:ure)?\.?\s*\d+[a-z]?|table\s+(?:[ivxlc]+|\d+))\s*[\.:—\-]?\s*(.{15,})$",
    re.I,
)
UNIT_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*-?\s*"
    r"(TOPS/W|TOPS|GOPS|SOP|MS/s|GS/s|GB|TB|MB|KB|hours?|billion|million|%|×|x|nm|µm|μm|pJ|fJ|nJ|µW|μW|mW|ms|MHz|GHz|mV|dB|bit|b|倍|纳米|微米|皮焦|纳焦|毫瓦|微瓦|毫伏|吉赫|兆赫|比特|位)"
    r"(?![A-Za-z])",
    re.I,
)
STOP_WORDS = re.compile(
    r"abstract|@|university|department|institute|school of|laborator|college|academy",
    re.I,
)
LIGATURES = str.maketrans(
    {
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬀ": "ff",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "ﬅ": "ft",
        "ﬆ": "st",
        "\u00ad": "",
        "\u200b": "",
        "\ufeff": "",
    }
)
COMMON_HEADINGS = {
    "introduction",
    "background",
    "related work",
    "related works",
    "literature review",
    "preliminaries",
    "problem formulation",
    "problem statement",
    "method",
    "methods",
    "methodology",
    "approach",
    "proposed method",
    "proposed approach",
    "system design",
    "architecture",
    "implementation",
    "implementation details",
    "experimental setup",
    "experiments",
    "experiment",
    "experimental results",
    "results",
    "evaluation",
    "results and discussion",
    "results and analysis",
    "discussion",
    "analysis",
    "ablation study",
    "ablation studies",
    "case study",
    "limitations",
    "limitation",
    "future work",
    "conclusion",
    "conclusions",
    "summary",
    "references",
    "bibliography",
    "acknowledgment",
    "acknowledgments",
    "acknowledgement",
    "acknowledgements",
    "appendix",
    "algorithm",
    "algorithms",
    "complexity analysis",
    "theoretical analysis",
}
AUTHOR_MARK_RE = re.compile(r"[∗†‡§¶λ∓]")
INSTITUTION_RE = re.compile(
    r"\b(?:universit(?:y|ät|é)|institute|department|dept\.?|school|college|academy|"
    r"laborator(?:y|ies)|research|eth|ist austria|meta ai|google|microsoft|nvidia|"
    r"openai|deepmind|facebook|hugging|ens|paris|zurich|austria|inc\.?|ltd\.?|"
    r"stanford|berkeley|princeton|harvard|mit|csail)\b",
    re.I,
)
NON_NAME_WORDS = {
    "abstract", "introduction", "conference", "workshop", "symposium", "journal",
    "preprint", "arxiv", "keywords", "index", "terms", "figure", "table", "copyright",
    "research", "university", "institute", "department", "school", "college",
    "academy", "laboratory", "labs", "inc", "ltd", "and", "the", "of", "for", "with",
    "from", "using", "via", "by", "on", "in", "to", "a", "an",
}
DATASET_TERMS = (
    "CIFAR-10", "CIFAR-100", "ImageNet", "MNIST", "N-MNIST", "DVS Gesture",
    "WRN-28-10", "ResNet", "ViT", "BERT", "Transformer", "SNN",
)
ADC_TERMS = ("SAR ADC", "SAR", "Flash ADC", "ramp ADC", "1-bit", "ADC-less", "ADC-free", "switched-capacitor", "charge-domain")


def clean_text(text: str) -> str:
    text = text.translate(LIGATURES)
    text = text.replace("�C", "–")
    text = re.sub(r"(\d)��(\d)", r"\1×\2", text)
    text = text.replace("Abstract��", "Abstract—").replace("Index Terms��", "Index Terms—")
    text = text.replace("��", "—")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _valid_author_names(value: str) -> list[str]:
    cleaned = AUTHOR_MARK_RE.sub("", value)
    cleaned = re.sub(r"[\d*∗†‡§¶λ∓]+", "", cleaned).strip(" ,;")
    if not cleaned or "@" in cleaned or INSTITUTION_RE.search(cleaned):
        return []
    names = []
    for part in re.split(r",|;|\band\b", cleaned):
        name = " ".join(part.strip(" .").split())
        if not (5 <= len(name) <= 70):
            continue
        words = name.split()
        if not 2 <= len(words) <= 6:
            continue
        if any(word.lower().strip(".") in NON_NAME_WORDS for word in words):
            continue
        if not all(
            word[0].isupper() or word[0].isdigit() or word.lower() in {"van", "von", "de", "da", "di", "la", "le"}
            for word in words
        ):
            continue
        names.append(name)
    return names


def _extract_authors(doc: pymupdf.Document, first_page: str, title: str) -> list[str]:
    raw = ((doc.metadata or {}).get("author") or "").strip()
    if raw:
        authors = [part.strip() for part in re.split(r"[;；]|,\s*(?=[A-Z])|\band\b", raw) if part.strip()]
        result = [author for author in authors if 1 < len(author) <= 80][:12]
        if result:
            return result
    lines = [line.strip() for line in clean_text(first_page).splitlines() if line.strip()]
    abstract_index = len(lines)
    for index, line in enumerate(lines):
        if re.match(r"^abstract\b", line, re.I):
            abstract_index = index
            break
    title_lower = " ".join(title.lower().split())
    title_tokens = set(re.findall(r"[a-z0-9]{2,}", title_lower))
    authors: list[str] = []
    for line in lines[:min(abstract_index, 20)]:
        lower = line.lower()
        if len(line) > 120 or len(line) < 5:
            continue
        if lower in title_lower:
            continue
        if re.match(r"^(?:arxiv|doi|https?://|www\.|e-?mail|keywords|index terms|received|accepted|published)", lower):
            continue
        if re.match(r"^(?:19|20)\d{2}\b|^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", lower):
            continue
        if "@" in line or INSTITUTION_RE.search(line):
            continue
        if re.match(r"^(?:abstract|introduction)\b", lower):
            continue
        tokens = set(re.findall(r"[a-z0-9]{2,}", lower))
        if title_tokens and tokens and len(tokens & title_tokens) / len(tokens) >= 0.6:
            continue
        candidates = _valid_author_names(line)
        if not candidates:
            continue
        if (
            len(candidates) == 1
            and len(line.split()) <= 2
            and not AUTHOR_MARK_RE.search(line)
            and not authors
        ):
            continue
        for name in candidates:
            if name not in authors:
                authors.append(name)
        if len(authors) >= 12:
            break
    return authors[:12]


def _extract_year(doc: pymupdf.Document, first_page: str, filename: str) -> int | None:
    created = ((doc.metadata or {}).get("creationDate") or "").strip()
    match = re.match(r"D:((?:19|20)\d{2})", created)
    if match:
        return int(match.group(1))
    arxiv = re.search(r"(?<!\d)(\d{2})\d{2}\.\d{4,5}(?:v\d+)?", filename)
    if arxiv:
        year = 2000 + int(arxiv.group(1))
        if 1990 <= year <= 2100:
            return year
    years = [int(value) for value in re.findall(r"\b(19\d{2}|20\d{2})\b", first_page[:4000])]
    years = [year for year in years if 1990 <= year <= 2100]
    return max(years) if years else None


def _normalize_repeated_line(line: str) -> str:
    value = re.sub(r"\d+", "#", line.strip().lower())
    return re.sub(r"\s+", " ", value)


def _strip_repeated_lines(pages: list[str]) -> list[str]:
    if len(pages) < 4:
        return pages
    counts: Counter = Counter()
    for page in pages:
        unique = {
            _normalize_repeated_line(line)
            for line in page.splitlines()
            if len(line.strip()) >= 8 and not NON_HEADING.match(line.strip())
        }
        counts.update(unique)
    threshold = max(3, int(len(pages) * 0.6))
    repeated = {line for line, count in counts.items() if count >= threshold}
    if not repeated:
        return pages
    cleaned = []
    for page in pages:
        kept = [
            line for line in page.splitlines()
            if _normalize_repeated_line(line) not in repeated
        ]
        cleaned.append("\n".join(kept))
    return cleaned


def _heading_core(line: str) -> str:
    value = line.strip()
    value = re.sub(r"^(?:[IVXLC]+|\d+(?:\.\d+)*|[A-Z])\.?\s+", "", value)
    value = value.strip(" .:-—")
    return re.sub(r"\s+", " ", value).lower()


def _looks_like_heading(line: str) -> bool:
    value = line.strip()
    if not (2 <= len(value) <= 100):
        return False
    if value.endswith((".", ",", ";", ":")):
        return False
    if NON_HEADING.match(value):
        return False
    if re.match(r"^(?:19|20)\d{2}\s", value):
        return False
    if any(char in value for char in "×=±"):
        return False
    if sum(char.isdigit() for char in value) > 6:
        return False
    if _heading_core(value) in COMMON_HEADINGS:
        return True
    if HEADING_FULL.match(value) or HEADING_SUB.match(value) or HEADING_NUM.match(value):
        return True
    if HEADING_CAPS.match(value):
        words = value.split()
        if len(words) < 2:
            return False
        if any(char in value for char in "/&"):
            return False
        if any(any(char.isdigit() for char in word) for word in words):
            return False
        if sum(len(word) for word in words) < 12 or max(len(word) for word in words) < 5:
            return False
        return len(words) <= 12
    return False


def _figure_hint(title: str, caption: str) -> str:
    value = (title + " " + caption).lower()
    if "table" in value:
        return "先看列定义、单位与测试条件；不要跨数据集或工艺直接比较。"
    if any(term in value for term in ("architecture", "overview", "block", "system", "diagram", "架构")):
        return "先看模块边界与数据流，再对照方法章节确认每个模块的作用。"
    if any(term in value for term in ("accuracy", "energy", "latency", "area", "throughput", "curve", "versus", "精度", "能耗", "延迟")):
        return "先看横纵轴、单位、baseline 与测试条件，再判断趋势和改善幅度。"
    return "先看标题、坐标轴/列定义与测试条件，再回到正文核对结论。"


def _extract_figures(pages: list[str], limit: int = 8) -> list[dict]:
    figures: list[dict] = []
    seen: set[str] = set()
    for page_number, raw_page in enumerate(pages, start=1):
        lines = [line.strip() for line in clean_text(raw_page).splitlines() if line.strip()]
        index = 0
        while index < len(lines):
            match = CAPTION_RE.match(lines[index])
            if not match:
                index += 1
                continue
            title = match.group(1).strip()
            caption = match.group(2).strip()
            cursor = index + 1
            while cursor < len(lines) and cursor < index + 4:
                nxt = lines[cursor]
                if CAPTION_RE.match(nxt) or _looks_like_heading(nxt):
                    break
                if nxt.lower().startswith(("references", "index terms")):
                    break
                caption += " " + nxt
                cursor += 1
            caption = " ".join(caption.split())[:420]
            key = title.lower()
            if len(caption) >= 30 and key not in seen:
                seen.add(key)
                figures.append(
                    {
                        "title": title,
                        "page": page_number,
                        "what": caption,
                        "how_to_read": _figure_hint(title, caption),
                    }
                )
            index = max(cursor, index + 1)
    return figures[:limit]


def _extract_doi(text: str) -> str:
    match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b", text or "")
    return match.group(0).rstrip(".,;)").lower() if match else ""


def _metric_name(sentence: str) -> str:
    value = sentence.lower()
    if any(term in value for term in ("energy efficiency", "pJ/SOP".lower(), "tops/w", "能效")):
        return "能效"
    if any(term in value for term in ("speedup", "speed-up", "faster", "wall-clock", "加速", "速度")):
        return "速度"
    if any(term in value for term in ("throughput", "gs/s", "ms/s", "吞吐")):
        return "吞吐"
    if any(term in value for term in ("memory footprint", "memory reduction", "model size", "gpu memory", "压缩", "显存", "内存")):
        return "内存/模型大小"
    if any(term in value for term in ("bitwidth", "bit-width", "bits per weight", "parameters", "billion", "参数", "位宽")):
        return "参数/位宽"
    if any(term in value for term in ("accurac", "top-1", "top1", "precision", "perplexity", "精度", "准确率")):
        return "精度"
    if any(term in value for term in ("latency", "delay", "cycle", "延迟")):
        return "延迟"
    if any(term in value for term in ("area", "mm2", "面积")):
        return "面积"
    if any(term in value for term in ("adc energy", "conversion energy", "comparison", "转换能耗", "比较次数")):
        return "ADC 开销"
    if any(term in value for term in ("power", "energy", "功耗", "能耗")):
        return "能耗"
    return "关键数值"


_ENERGY_UNITS = {"pj", "fj", "nj", "µw", "μw", "mw", "w", "tops/w", "pj/sop", "×", "倍"}


def _quantity_unit(token: str) -> str:
    return re.sub(r"^[\d.,\s\-]+", "", token).strip().lower()


def _metric_quantities(name: str, quantities: list[str]) -> list[str]:
    """Keep numbers that belong to the sentence's extracted metric.

    A sentence often mixes accuracy, latency and energy (for example
    "97.2% at 1.8 pJ/SOP"); listing every number under "能效" makes the result
    table look wrong. Keep the matching unit family and fall back to the
    original list only when no family matches.
    """
    if name in {"能效", "能耗", "ADC 开销"}:
        energy = [item for item in quantities if _quantity_unit(item) in _ENERGY_UNITS]
        return energy or [item for item in quantities if _quantity_unit(item) == "%"]
    if name == "精度":
        return [item for item in quantities if _quantity_unit(item) == "%"] or quantities
    if name == "面积":
        area = [item for item in quantities if _quantity_unit(item) in {"%", "mm2", "µm", "μm", "nm"}]
        return area or quantities
    if name == "延迟":
        delay = [item for item in quantities if _quantity_unit(item) in {"ms", "µs", "μs", "ns", "cycle", "%", "×", "倍"}]
        return delay or quantities
    return quantities


def _extract_results(sections: list[dict], abstract: str, limit: int = 5) -> list[dict]:
    corpus = [{"title": "Abstract", "page": 1, "text": abstract}] + list(sections)
    candidates: list[dict] = []
    seen: set[str] = set()
    for section in corpus:
        text = " ".join((section.get("text") or "").split())
        for sentence in re.split(r"(?<=[.!?。！？])\s+", text):
            sentence = sentence.strip()
            if len(sentence) < 24:
                continue
            quantities = []
            for match in UNIT_RE.finditer(sentence):
                token = match.group(0).strip()
                if token.lower() not in {item.lower() for item in quantities}:
                    quantities.append(token)
            if not quantities:
                continue
            if len(re.findall(r"\d+(?:\.\d+)?", sentence)) > 8:
                continue
            if re.match(r"^[\d\s.,%×\-]+", sentence):
                continue
            if re.search(r"\((?:a|b|c|d)\)", sentence):
                continue
            if re.search(r"\bFig(?:ure)?\.?\s*\d|\bTABLE\s+[IVXLC\d]", sentence, re.I):
                continue
            if re.search(r"\breports?\s+that\b|\baccording to\b", sentence, re.I) and re.search(r"\[\d+\]", sentence):
                continue
            if not re.search(
                r"achiev|improv|reduc|compared|baseline|demonstrat|outperform|measured|report|show|reach|save|increase|decrease|higher|lower|提升|降低|达到|实现",
                sentence.lower(),
            ):
                continue
            key = re.sub(r"\W+", "", sentence.lower())[:120]
            if key in seen:
                continue
            seen.add(key)
            score = 1.5 * min(len(quantities), 4)
            score += sum(
                0.5 for term in ("achiev", "improv", "reduc", "compared", "baseline", "demonstrat", "outperform")
                if term in sentence.lower()
            )
            name = _metric_name(sentence)
            metric_values = _metric_quantities(name, quantities)
            if not metric_values:
                continue
            candidates.append(
                {
                    "name": name,
                    "value": "、".join(metric_values[:4]),
                    "context": sentence[:420],
                    "section": section.get("title") or "正文",
                    "page": section.get("page") or 1,
                    "score": score,
                }
            )
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates[:limit]


def _extract_limitations(sections: list[dict], limit: int = 3) -> list[str]:
    found: list[str] = []
    for section in sections:
        text = " ".join((section.get("text") or "").split())
        for sentence in re.split(r"(?<=[.!?。！？])\s+", text):
            low = sentence.lower()
            if len(sentence) < 24:
                continue
            if re.search(r"\bFig(?:ure)?\.?\s*\d|\bTABLE\s+[IVXLC\d]", sentence, re.I):
                continue
            if any(
                term in low
                for term in (
                    "limitation", "future work", "not considered", "not evaluated",
                    "outside the present", "remain open", "remains challenging",
                    "unverified", "future research", "does not currently", "do not provide",
                    "not supported", "we leave", "future direction",
                    "局限", "未验证", "未来工作", "未考虑",
                )
            ):
                value = sentence[:280].strip()
                if value not in found:
                    found.append(value)
            if len(found) >= limit:
                return found
    return found


def _extract_pdf_metrics(pages: list[str], sections: list[dict], figures: list[dict], full_text: str = "") -> dict:
    head = clean_text("\n".join(pages[:3]))
    node = re.search(r"(\d{2,3})\s*-?\s*nm", head, re.I)
    process = f"{node.group(1)} nm" if node else "未提及"
    low = head.lower()
    silicon_test = bool(
        re.search(
            r"silicon results|measured (?:chip|prototype|macro|nlq|output)|our .*fabricat|fabricated .*our|test chip",
            low,
        )
    )
    simulated = any(term in low for term in ("simulation", "schematic", "post-layout"))
    if silicon_test:
        verification = "硅测/实测"
    elif simulated:
        verification = "仿真"
    else:
        verification = "未提及"
    adc_terms = [term for term in ADC_TERMS if term.lower() in low]
    task_terms = [term for term in DATASET_TERMS if term.lower() in low]
    refs_match = re.search(r"\n(?:references|bibliography)\b", full_text, re.I)
    refs_tail = full_text[refs_match.start():] if refs_match else ""
    reference_count = len(re.findall(r"\[\d+\]", refs_tail))
    return {
        "工艺/验证": f"{process} · {verification}" if process != "未提及" else verification,
        "ADC 方案": " / ".join(dict.fromkeys(adc_terms)) or "未提及",
        "网络/任务": " / ".join(dict.fromkeys(task_terms)) or "未提及",
        "图表": len(figures),
        "参考文献": reference_count or "未提及",
    }


def _title_from_ocr(first_page: str) -> str:
    """Take the top lines of OCR output, stopping at blank/authors/abstract."""
    lines = first_page.splitlines()
    title_lines: list[str] = []
    for line in lines[:12]:
        text = " ".join(line.split())
        if not text:
            if title_lines:
                break
            continue
        if re.match(r"^(?:abstract|index terms|keywords|introduction|doi|arxiv)", text, re.I):
            break
        if AUTHOR_MARK_RE.search(text) or "@" in text or INSTITUTION_RE.search(text):
            break
        if text.count(",") >= 2:
            break
        title_lines.append(text[:180])
        if len(" ".join(title_lines)) >= 220 or len(title_lines) >= 4:
            break
    return " ".join(title_lines).strip(" -–")


def _extract_title(doc: pymupdf.Document, first_page: str, ocr_used: bool = False) -> str:
    if ocr_used:
        ocr_title = _title_from_ocr(first_page)
        if len(ocr_title) >= 4:
            return ocr_title
    meta_title = clean_text((doc.metadata or {}).get("title", "") or "")
    meta_title = " ".join(meta_title.split())
    if (
        5 <= len(meta_title) <= 300
        and meta_title.lower() not in {"untitled", "microsoft word"}
        and not re.search(r"\.(?:pdf|docx?|tex)$", meta_title, re.I)
        and not AUTHOR_MARK_RE.search(meta_title)
    ):
        return meta_title
    lines = [line.strip() for line in first_page.splitlines() if line.strip()]
    title_lines: list[str] = []
    for line in lines[:16]:
        if STOP_WORDS.search(line):
            break
        if re.match(r"^(?:arxiv|doi|https?://|preprint|copyright|©)", line, re.I):
            break
        if len(line) < 4:
            continue
        if line.count(",") >= 2 or AUTHOR_MARK_RE.search(line):
            break
        if title_lines and INSTITUTION_RE.search(line):
            break
        title_lines.append(line)
        if len(title_lines) >= 4:
            break
    title = " ".join(title_lines).strip(" -–")
    return title or meta_title or "Untitled paper"


def _split_sections(pages: list[str]) -> list[dict]:
    sections: list[dict] = []
    current = {"title": "Abstract", "page": 1, "lines": []}
    in_references = False
    for page_number, raw_page in enumerate(pages, start=1):
        page_text = clean_text(raw_page)
        for line in page_text.splitlines():
            stripped = line.strip()
            if not stripped:
                if current["lines"] and current["lines"][-1] != "":
                    current["lines"].append("")
                continue
            if in_references:
                if re.match(r"^appendi(?:x|ces)\b", stripped, re.I):
                    in_references = False
                else:
                    continue
            if len(stripped) <= 6 and re.fullmatch(
                r"(?:\d{1,3}(?:\.\d+)*|[ivxlc]+\.?|[A-Z]\.?)",
                stripped,
                re.I,
            ):
                continue
            if re.match(r"^abstract\b", stripped, re.I):
                rest = re.sub(r"^abstract\s*[—\-:：]?\s*", "", stripped, flags=re.I)
                # The lines collected so far are title/authors, not abstract body.
                current = {"title": "Abstract", "page": page_number, "lines": []}
                if rest:
                    current["lines"].append(rest)
                continue
            if re.match(r"^(index terms|keywords)\b", stripped, re.I):
                text = "\n".join(current["lines"]).strip()
                if text and current["title"]:
                    sections.append({"title": current["title"], "page": current["page"], "text": text[:7000]})
                current = {"title": "", "page": page_number, "lines": []}
                continue
            is_heading = stripped.upper() == "ABSTRACT" or _looks_like_heading(stripped)
            if is_heading:
                text = "\n".join(current["lines"]).strip()
                if text and current["title"]:
                    sections.append(
                        {
                            "title": current["title"],
                            "page": current["page"],
                            "text": text[:7000],
                        }
                    )
                current = {"title": stripped, "page": page_number, "lines": []}
                if _heading_core(stripped) in {"references", "bibliography"}:
                    current = {"title": "", "page": page_number, "lines": []}
                    in_references = True
            elif current["title"]:
                current["lines"].append(stripped)
    text = "\n".join(current["lines"]).strip()
    if text and current["title"]:
        sections.append(
            {"title": current["title"], "page": current["page"], "text": text[:7000]}
        )
    # Drop references and empty sections.
    return [s for s in sections if s["title"] and s["text"] and len(s["text"]) > 40][:18]


def _first_sentences(text: str, count: int = 3) -> str:
    parts = re.split(r"(?<=[.!?。！？])\s+", " ".join(text.split()))
    return " ".join(parts[:count]).strip()


def parse_pdf_bytes(data: bytes, filename: str) -> dict:
    if not data.startswith(b"%PDF"):
        raise ValueError("文件不是有效的 PDF。")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise ValueError("PDF 超过 25 MB 限制。")

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - corrupt files are user input
        raise ValueError("PDF 无法打开或文件已损坏。") from exc
    if doc.page_count == 0:
        raise ValueError("PDF 没有可读页面。")
    if doc.page_count > 80:
        raise ValueError("PDF 页数超过 80 页，请上传单篇论文。")

    pages = _strip_repeated_lines([page.get_text("text") for page in doc])
    full_text = clean_text("\n".join(pages))
    ocr_used = False
    ocr_page_count = 0
    ocr_attempted = 0
    ocr_truncated = False
    if len(full_text.strip()) < 80:
        if ocr.enabled():
            ocr_pages = ocr.extract_pages(doc)
            ocr_text = clean_text("\n".join(ocr_pages))
            if len(ocr_text.strip()) >= 80:
                pages = _strip_repeated_lines(ocr_pages)
                full_text = clean_text("\n".join(pages))
                ocr_used = True
                ocr_page_count = sum(1 for item in pages if item.strip())
                ocr_attempted = len(ocr_pages)
                ocr_truncated = ocr_attempted < doc.page_count
            else:
                raise ValueError(
                    "PDF 没有可提取的文字（可能是扫描件），本地 OCR 也未识别出足够内容；"
                    "请提高扫描清晰度，或先对扫描件做 OCR。"
                )
        else:
            raise ValueError(
                "PDF 没有可提取的文字（可能是扫描件）；当前实例未启用本地 OCR，"
                "请上传可搜索 PDF，或先对扫描件做 OCR。"
            )
    first_page = clean_text(pages[0]) if pages else ""
    title = _extract_title(doc, first_page, ocr_used=ocr_used)
    authors = _extract_authors(doc, first_page, title)
    year = _extract_year(doc, first_page, filename)
    figures = _extract_figures(pages)
    sections = _split_sections(pages)
    abstract = next(
        (s["text"] for s in sections if s["title"].lower().startswith("abstract")),
        _first_sentences(full_text, 8),
    )
    doi = _extract_doi(full_text)
    results = [
        {key: value for key, value in item.items() if key != "score"}
        for item in _extract_results(sections, abstract)
    ]
    limitations = _extract_limitations(sections)
    metrics = {
        "页数": doc.page_count,
        "字符数": len(full_text),
        **_extract_pdf_metrics(pages, sections, figures, full_text),
        "可核对数值": len(results),
    }
    ocr_warning = (
        f"仅 OCR 了前 {ocr_attempted} 页，其余页面未识别；可调高 PAPERPILOT_OCR_MAX_PAGES。"
        if ocr_truncated else ""
    )
    if ocr_used:
        metrics["OCR 页数"] = ocr_page_count
        if ocr_truncated:
            metrics["OCR 截断"] = True

    paper_id = "upload-" + uuid.uuid4().hex[:8]
    pdf_path = config.UPLOAD_DIR / f"{paper_id}.pdf"
    pdf_path.write_bytes(data)

    parsed = {
        "id": paper_id,
        "filename": Path(filename).name,
        "page_count": doc.page_count,
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "pages": len(pages),
        "chars": len(full_text),
        "sections": sections,
        "figures": figures,
        "results": results,
        "metrics": metrics,
        "limitations": limitations,
        "ocr_used": ocr_used,
        "ocr_pages": ocr_page_count,
        "ocr_truncated": ocr_truncated,
        "ocr_warning": ocr_warning,
    }
    (config.PARSED_DIR / f"{paper_id}.json").write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result_evidence = [
        {
            "field": "result",
            "label": f"结果 · {item['name']}",
            "section": item["section"],
            "page": item["page"],
            "quote": item["context"][:320],
        }
        for item in results[:2]
    ]
    limitation_evidence = []
    for limitation in limitations:
        needle = re.sub(r"\s+", " ", limitation)[:50].lower()
        for section in sections:
            if needle and needle in re.sub(r"\s+", " ", section["text"]).lower():
                limitation_evidence.append(
                    {
                        "field": "limitation",
                        "label": "局限",
                        "section": section["title"],
                        "page": section["page"],
                        "quote": limitation[:320],
                    }
                )
                break
    section_evidence = [
        {
            "field": "text",
            "label": section["title"],
            "section": section["title"],
            "page": section["page"],
            "quote": section["text"][:280],
        }
        for section in sections[:4]
    ]
    evidence = []
    seen_quotes = set()
    for item in result_evidence + limitation_evidence + section_evidence:
        key = item["quote"][:120]
        if key not in seen_quotes:
            seen_quotes.add(key)
            evidence.append(item)

    paper = {
        "id": paper_id,
        "doi": doi,
        "title": title,
        "authors": authors,
        "year": year,
        "venue": "用户上传 PDF",
        "source": "upload",
        "source_label": "本地 PDF",
        "url": f"https://doi.org/{doi}" if doi else "",
        "pdf_url": "",
        "demo": False,
        "full_text": True,
        "ocr_used": ocr_used,
        "ocr_pages": ocr_page_count,
        "ocr_truncated": ocr_truncated,
        "ocr_warning": ocr_warning,
        "extraction_level": (
            f"上传 PDF · 本地 OCR（{ocr_page_count} 页，规则抽取）"
            if ocr_used else "上传 PDF · 全文解析（规则抽取）"
        ),
        "abstract": abstract,
        "tldr": _first_sentences(abstract, 3),
        "problem": "待结构化抽取：可配置 LLM 后自动生成；当前先展示原文节选。",
        "method": "待结构化抽取：可配置 LLM 后自动生成；当前先展示原文节选。",
        "novelty": [],
        "assumptions": [],
        "metrics": metrics,
        "results": results,
        "baselines": [],
        "limitations": limitations,
        "code_url": None,
        "evidence": evidence,
        "sections": sections,
        "figures": figures,
        "translation": [],
        "repo_plan": {
            "environment": ["待补充：论文依赖、数据集、代码仓库"],
            "steps": ["先确认论文是否提供开源代码", "复现最小实验", "记录环境与随机种子", "对照论文指标"],
            "minimal_experiment": "从论文中最小的数据集/模型配置开始，先跑通一次前向与评估。",
            "risks": ["论文未给全部超参数", "数据预处理差异", "计算资源不足"],
            "deliverables": ["复现日志", "指标对照表", "问题清单"],
        },
        "code_scaffold": "# TODO: 配置 LLM 后，PaperPilot 会根据方法章节生成复现代码骨架。\n",
        "slides": [
            "研究问题",
            "方法概览",
            "关键实验",
            "主要结果",
            "局限与启示",
        ],
        "quiz": [],
        "reviewer_questions": [],
        "course_case": "待生成：可将本文映射到课程知识点。",
    }
    return paper


def local_pdf_path(paper_id: str) -> Path | None:
    """Return the stored PDF for an uploaded or bundled paper, if present."""
    if not identifiers.is_safe_id(str(paper_id or "")):
        return None
    candidates = [
        config.UPLOAD_DIR / f"{paper_id}.pdf",
        config.DATA_DIR / "demo_pdfs" / f"{paper_id}.pdf",
    ]
    return next((path for path in candidates if path.is_file()), None)


def page_count(paper_id: str) -> int:
    """Page count of the stored PDF, or 0 when there is no readable file."""
    path = local_pdf_path(paper_id)
    if path is None:
        return 0
    try:
        doc = pymupdf.open(path)
    except Exception:  # noqa: BLE001 - a damaged file has no viewable pages
        return 0
    try:
        return int(doc.page_count)
    finally:
        doc.close()


def render_page(paper_id: str, page: int, dpi: int = 150) -> bytes | None:
    """Render one PDF page to PNG; return None for missing files/pages."""
    path = local_pdf_path(paper_id)
    if path is None:
        return None
    try:
        doc = pymupdf.open(path)
    except Exception:  # noqa: BLE001 - a damaged upload should become a 404
        return None
    try:
        if page < 1 or page > doc.page_count:
            return None
        pixmap = doc[page - 1].get_pixmap(dpi=max(72, min(int(dpi), 220)), alpha=False)
        return pixmap.tobytes("png")
    except (RuntimeError, ValueError):
        return None
    finally:
        doc.close()


def _quote_candidates(quote: str) -> list[str]:
    normalized = " ".join(str(quote or "").split())
    if not normalized:
        return []
    candidates = [normalized]
    for part in re.split(r"(?<=[.!?。！？；;])\s+", normalized):
        if len(part) >= 16:
            candidates.append(part[:220])
    for length in (180, 120, 60):
        if len(normalized) >= length:
            candidates.append(normalized[:length])
    seen = set()
    result = []
    for item in candidates:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result[:8]


def locate_quote(paper_id: str, page: int, quote: str) -> dict | None:
    """Locate a quote in a local PDF page and return normalized rectangles."""
    path = local_pdf_path(paper_id)
    if path is None:
        return None
    try:
        doc = pymupdf.open(path)
    except Exception:  # noqa: BLE001
        return None
    try:
        if page < 1 or page > doc.page_count:
            return None
        page_obj = doc[page - 1]
        width = float(page_obj.rect.width) or 1.0
        height = float(page_obj.rect.height) or 1.0
        rects = []
        for candidate in _quote_candidates(quote):
            try:
                found = page_obj.search_for(candidate)
            except (RuntimeError, ValueError):
                found = []
            for rect in found:
                item = {
                    "x0": round(max(0.0, min(1.0, rect.x0 / width)), 5),
                    "y0": round(max(0.0, min(1.0, rect.y0 / height)), 5),
                    "x1": round(max(0.0, min(1.0, rect.x1 / width)), 5),
                    "y1": round(max(0.0, min(1.0, rect.y1 / height)), 5),
                }
                if item not in rects:
                    rects.append(item)
            if rects:
                break
        return {
            "page": page,
            "page_count": doc.page_count,
            "rects": rects[:12],
            "matched": bool(rects),
        }
    finally:
        doc.close()
