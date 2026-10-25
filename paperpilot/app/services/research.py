from __future__ import annotations

import re
from collections import Counter

from ..demo_data import GAPS
from . import analysis, ask, search

SCOPE_LABELS = {
    "all": "全部语料",
    "library": "我的研究库",
    "selected": "当前勾选论文",
}

METHOD_CLUSTERS = [
    ("ADC 近存转换", ("adc", "sar", "flash", "ramp", "conversion", "量化", "模数", "转换器")),
    ("电荷域/模拟计算", ("charge", "switched-capacitor", "capacitor", "analog", "电荷", "电容", "模拟")),
    ("随机/概率读出", ("stochastic", "random", "1-bit", "counting", "probability", "随机", "概率", "计数")),
    ("神经形态/稀疏", ("snn", "spiking", "neuromorphic", "dendrite", "top-k", " sparse", "神经形态", "脉冲", "稀疏")),
    ("可重构/宏单元", ("reconfigurable", "macro", "bit-cell", "array", "可重构", "阵列", "宏")),
    ("数字/近似计算", ("digital", "approximate", "quantization", "数字", "近似")),
    ("软硬件协同", ("training", "co-design", "hardware-aware", "蒸馏", "训练", "校准")),
    ("量化/模型压缩", ("quantization", "quantize", "compression", "pruning", "distillation", "8-bit", "4-bit", "fp16", "量化", "压缩", "剪枝", "蒸馏")),
    ("高效注意力/长上下文", ("attention", "flash attention", "long context", "kv cache", "sparse attention", "注意力", "长上下文", "长序列")),
    ("推理系统/边缘部署", ("inference", "serving", "kernel", "gpu", "latency", "throughput", "deployment", "on-device", "edge", "推理", "部署", "内核", "延迟", "吞吐", "端侧")),
    ("训练/微调", ("training", "fine-tuning", "finetune", "pre-training", "optimizer", "训练", "微调")),
]

DATASETS = (
    "CIFAR-10", "CIFAR-100", "ImageNet", "MNIST", "N-MNIST", "DVS Gesture",
    "WikiText-2", "WikiText-103", "LAMBADA", "MMLU", "GSM8K", "C4",
)
MODEL_NAMES = (
    "WRN-28-10", "ResNet", "ViT", "BERT", "Transformer", "SNN",
    "OPT", "LLaMA", "GPT-2", "BLOOM", "TinyChat",
)
CURATED_TOPIC_ANCHORS = (
    "存算", "存内", "近存", "cim", "adc", "模拟", "电荷", "神经形态",
    "脉冲", "sram", "rram", "忆阻", "工艺", "芯片", "电路", "放大器",
)


def _hardware_scope(topic: str, papers: list[dict]) -> bool:
    """Only apply the ADC/hardware clusters when the corpus is actually hardware."""
    text = " ".join(
        [str(topic or "")]
        + [
            f"{paper.get('title') or ''} {analysis.abstract_of(paper)[:600]}"
            for paper in papers
        ]
    ).lower()
    return any(anchor in text for anchor in CURATED_TOPIC_ANCHORS)

DRAFT_LABELS = {
    "related_work": "相关工作",
    "outline": "论文提纲",
    "abstract": "摘要骨架",
}


def _clean(value: str, limit: int = 240) -> str:
    text = " ".join(analysis.coerce_text(value, max(limit * 3, 1000)).split())
    if len(text) <= limit:
        return text
    window = text[:limit]
    boundaries = (
        "。", "；", ";", ".", "！", "!", "？", "?", "，", ",", "、", " ", "\n",
    )
    cut = max((window.rfind(marker) for marker in boundaries), default=-1)
    if cut >= max(20, int(limit * 0.55)):
        window = window[:cut]
    window = window.rstrip(" ,;，；、.!?!。．")
    return window + "…"


def _inline(value: str, limit: int = 240) -> str:
    return _clean(value, limit).rstrip("。．.!?！？ ")


def _sentence(value: str, limit: int = 260) -> str:
    text = _inline(value, limit)
    if not text or text.endswith(("…", "。", "．", "！", "!", "？", "?")):
        return text
    return text + "。"


_LEAD_PREFIXES = {
    "problem": ("针对", "面向", "为了解决"),
    "method": ("采用了", "采用", "提出了", "提出", "使用了", "使用", "基于"),
    "result": ("报告了", "报告", "实现了", "达到"),
}


def _strip_lead(value: str, kind: str) -> str:
    """Drop a connective the template already supplies.

    The sentence is built as "针对{problem}，采用{method}，报告{result}"; model
    output often starts with the same word, which used to produce "采用采用".
    """
    text = str(value or "").strip()
    for prefix in _LEAD_PREFIXES.get(kind, ()):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix):].lstrip("，, 、：:")
    return text


def _join_cjk(prefix: str, value: str) -> str:
    """Keep a space between a Chinese connective and a Latin-starting value."""
    text = str(value or "")
    if prefix and text and text[0].isascii() and text[0].isalnum():
        return f"{prefix} {text}"
    return f"{prefix}{text}"


def _cluster_of(paper: dict, read: dict, hardware: bool) -> str:
    if not hardware:
        return "相关工作"
    text = " ".join(
        [
            analysis.coerce_text(paper.get("title"), 500),
            analysis.coerce_text(paper.get("abstract"), 1000),
            analysis.coerce_text(read.get("problem"), 600),
            analysis.coerce_text(read.get("method"), 800),
            " ".join(analysis.coerce_text(item, 300) for item in (paper.get("novelty") or [])[:8]),
        ]
    ).lower()
    best_label = "其他"
    best_score = 0
    for label, keywords in METHOD_CLUSTERS:
        score = sum(1 for keyword in keywords if keyword in text)
        if score > best_score:
            best_label, best_score = label, score
    return best_label


def _datasets_of(paper: dict, read: dict, hardware: bool) -> list[str]:
    text = " ".join([
        analysis.coerce_text(paper.get("title"), 500),
        analysis.coerce_text(paper.get("abstract"), 1000),
        analysis.coerce_text(read.get("method"), 800),
    ]).lower()
    pool = DATASETS + MODEL_NAMES if hardware else DATASETS
    return [name for name in pool if name.lower() in text]


def _profiles(papers: list[dict], topic: str = "") -> list[dict]:
    hardware = _hardware_scope(topic, papers)
    profiles = []
    for index, paper in enumerate(papers, start=1):
        read = analysis.read_paper(paper, "5min")
        limitations = [
            item for item in (read.get("limitations") or paper.get("limitations") or [])
            if item and not str(item).startswith(("规则抽取未识别", "待结构化抽取"))
            and not analysis._is_method_like(str(item))
        ]
        metrics = []
        for result in paper.get("results", []) or []:
            name = str(result.get("name") or "").strip()
            if name and name not in metrics:
                metrics.append(name)
        for key in ("工艺/验证", "ADC 方案", "网络/任务"):
            value = (paper.get("metrics") or {}).get(key)
            if value and str(value) not in {"未提及"} and key not in metrics:
                metrics.append(key)
        baselines = []
        for result in paper.get("results", []) or []:
            baseline = str(result.get("baseline") or "").strip()
            if baseline and baseline not in {"未提及", "未比较"} and baseline not in baselines:
                baselines.append(baseline)
        limitation_evidence = next(
            (item for item in paper.get("evidence", []) if item.get("field") == "limitation"),
            None,
        )
        profiles.append(
            {
                "cite": f"P{index}",
                "paper": paper,
                "read": read,
                "cluster": _cluster_of(paper, read, hardware),
                "limitations": limitations,
                "limitation_evidence": limitation_evidence,
                "datasets": _datasets_of(paper, read, hardware),
                "metrics": metrics[:5],
                "baselines": baselines[:3],
                "domain": "hardware" if hardware else "general",
            }
        )
    return profiles


def _scope_papers(scope: str, paper_ids: list[str]) -> list[dict]:
    return ask._scope_papers(scope, paper_ids)


def _relevant_gaps(topic: str) -> list[dict]:
    low_topic = (topic or "").lower()
    if not any(anchor in low_topic for anchor in CURATED_TOPIC_ANCHORS):
        return []
    tokens = set(ask._tokenize(topic))
    scored = []
    for gap in GAPS:
        text = " ".join([gap["title"], gap["question"], gap["why_gap"]]).lower()
        score = sum(1 for token in tokens if token in text)
        if score:
            scored.append((score, gap))
    scored.sort(key=lambda item: (-item[0], -(item[1]["novelty"] + item[1]["impact"])))
    return [gap for _, gap in scored[:3]]


def _limitation_of(limitation: str) -> str:
    text = " ".join(str(limitation or "").split())
    text = re.sub(
        r"^(?:limitations?|note that|in terms of limitations?,?)\s*[:：,\-—]?\s*",
        "",
        text,
        flags=re.I,
    ).strip()
    if re.fullmatch(r"(?:we leave this exploration to future work|future work)\.?", text, re.I):
        return ""
    return text


def _generated_questions(topic: str, profiles: list[dict], limit: int = 3) -> list[dict]:
    questions = []
    metadata_question_seen = False
    for profile in sorted(profiles, key=lambda item: (item["paper"].get("year") or 0), reverse=True):
        paper = profile["paper"]
        read = profile["read"]
        limitations = [
            _limitation_of(item)
            for item in profile["limitations"]
            if _limitation_of(item)
        ]
        if not limitations:
            hardware = profile.get("domain") == "hardware"
            metric = profile["metrics"][0] if profile["metrics"] else (
                "能效、精度或鲁棒性" if hardware else "答案正确性、忠实度或任务成功率"
            )
            dataset = profile["datasets"][0] if profile["datasets"] else "最小公开数据集"
            method = read.get("method") or ""
            method_text = "该工作的方法" if not method or method == "摘要未给出明确方法" else _inline(method, 50)
            problem = read.get("problem") or paper.get("abstract") or paper.get("title")
            short_title = _inline(paper.get("title") or topic, 46)
            questions.append(
                {
                    "id": f"gen-{profile['cite'].lower()}-{len(questions) + 1}",
                    "source": "coverage",
                    "title": f"{_inline(paper.get('title') or topic, 32)}：跨条件验证",
                    "question": f"在“{topic}”中，如何验证《{short_title}》的方法在另一数据集或测试条件下的稳定性与代价？",
                    "why_gap": (
                        f"{profile['cite']} 给出了问题与方法，但当前材料没有明确局限或跨条件验证；"
                        "先补这一证据缺口。"
                    ),
                    "evidence": [
                        f"{profile['cite']} {paper.get('title')} · Abstract: {_inline(problem, 140)}"
                    ],
                    "evidence_papers": [paper.get("id")],
                    "novelty": 3,
                    "feasibility": 4,
                    "impact": 3,
                    "experiment": (
                        f"在 {dataset} 上复现 {method_text}，更换一个条件（数据域、指标口径或模型规模），"
                        f"比较 {metric} 与 baseline 的变化并报告失败条件。"
                    ),
                    "risks": [
                        "证据来自摘要级材料，需先核对全文与测试条件",
                        ("新条件可能引入额外标定/训练开销" if hardware else "新条件可能引入额外训练或推理开销"),
                    ],
                }
            )
            if len(questions) >= limit:
                break
            continue
        limitation = limitations[0]
        is_metadata_limitation = "摘要" in limitation and (
            "硅验证" in limitation or "工艺" in limitation
        )
        if is_metadata_limitation and metadata_question_seen:
            continue
        if is_metadata_limitation:
            metadata_question_seen = True
        hardware = profile.get("domain") == "hardware"
        metric = profile["metrics"][0] if profile["metrics"] else (
            "能效、精度或鲁棒性" if hardware else "答案正确性、忠实度或任务成功率"
        )
        dataset = profile["datasets"][0] if profile["datasets"] else "最小公开数据集"
        method = read.get("method") or "现有方法"
        evidence = profile["limitation_evidence"]
        evidence_text = (
            f"{profile['cite']} {paper.get('title')} · {evidence.get('section')} p.{evidence.get('page')}: "
            f"{evidence.get('quote')}"
            if evidence
            else f"{profile['cite']} {paper.get('title')}：{limitation}"
        )
        questions.append(
            {
                "id": f"gen-{profile['cite'].lower()}-{len(questions) + 1}",
                "source": "limitation",
                "title": f"{_inline(limitation, 38)}：验证与边界",
                "question": f"在“{topic}”中，如何验证并缓解“{_inline(limitation, 60)}”对{metric}的影响？",
                "why_gap": f"{profile['cite']} 的证据显示“{_inline(limitation, 100)}”，当前材料没有给出对应条件下的验证。",
                "evidence": [evidence_text],
                "evidence_papers": [paper.get("id")],
                "novelty": 4 if paper.get("full_text") else 3,
                "feasibility": 4 if dataset != "最小公开数据集" else 3,
                "impact": 4,
                "experiment": (
                    f"在 {dataset} 上复现 {_inline(method, 50)}，加入“{_inline(limitation, 50)}”对应的"
                    f"消融或{'校准' if hardware else '对照'}实验，比较 {metric} 与 baseline 的变化。"
                ),
                "risks": [
                    "证据可能来自摘要级材料，需先核对全文与测试条件",
                    ("新条件可能引入额外标定/训练开销" if hardware else "新条件可能引入额外训练或推理开销"),
                ],
            }
        )
        if len(questions) >= limit:
            break
    return questions


def _curated_questions(profiles: list[dict], gaps: list[dict]) -> list[dict]:
    questions = []
    for gap in gaps:
        questions.append(
            {
                "id": gap["id"],
                "source": "curated",
                "title": gap["title"],
                "question": gap["question"],
                "why_gap": gap["why_gap"],
                "evidence": gap["evidence"],
                "evidence_papers": [
                    item.split(" · ")[0] for item in gap["evidence"] if " · " in item
                ],
                "novelty": gap["novelty"],
                "feasibility": gap["feasibility"],
                "impact": gap["impact"],
                "experiment": gap["experiment"],
                "risks": ["需要确认该缺口在当前材料中仍成立", "最小实验依赖数据/工艺条件"],
            }
        )
    return questions


def _reading_path(profiles: list[dict]) -> list[dict]:
    used: set[str] = set()

    def pick(candidates: list[dict], limit: int, why: str) -> list[dict]:
        result = []
        for profile in candidates:
            paper_id = profile["paper"].get("id")
            if not paper_id or paper_id in used:
                continue
            used.add(paper_id)
            result.append(
                {
                    "paper_id": paper_id,
                    "cite": profile["cite"],
                    "title": profile["paper"].get("title"),
                    "year": profile["paper"].get("year"),
                    "venue": profile["paper"].get("venue"),
                    "cluster": profile["cluster"],
                    "why": why,
                }
            )
            if len(result) >= limit:
                break
        return result

    by_year = sorted(profiles, key=lambda item: item["paper"].get("year") or 9999)
    foundation = pick(by_year, 1, "先建立问题定义、指标口径与基线。")
    methods = [item for item in profiles if item["paper"].get("full_text") or item["read"].get("sections")]
    method_stage = pick(methods or by_year, 2, "精读方法与证据链，提炼可迁移的设计选择。")
    frontier = pick(
        sorted(profiles, key=lambda item: item["paper"].get("year") or 0, reverse=True),
        2,
        "对照最新结果、边界条件与未解决问题。",
    )
    if not frontier and profiles:
        latest = max(profiles, key=lambda item: item["paper"].get("year") or 0)
        frontier = [
            {
                "paper_id": latest["paper"].get("id"),
                "cite": latest["cite"],
                "title": latest["paper"].get("title"),
                "year": latest["paper"].get("year"),
                "venue": latest["paper"].get("venue"),
                "cluster": latest["cluster"],
                "why": "二刷：带着方法与证据视角回看最新工作。",
            }
        ]
    return [
        {"id": "foundation", "label": "建立问题", "papers": foundation},
        {"id": "method", "label": "方法与证据", "papers": method_stage},
        {"id": "frontier", "label": "前沿与空白", "papers": frontier},
    ]


def _experiment_plan(profiles: list[dict], questions: list[dict]) -> dict:
    hardware = bool(profiles) and profiles[0].get("domain") == "hardware"
    datasets = list(dict.fromkeys(name for item in profiles for name in item["datasets"]))
    metrics = list(dict.fromkeys(name for item in profiles for name in item["metrics"]))
    baselines = list(dict.fromkeys(name for item in profiles for name in item["baselines"]))
    hardware_conditions = list(
        dict.fromkeys(
            str((item["paper"].get("metrics") or {}).get("工艺/验证"))
            for item in profiles
            if (item["paper"].get("metrics") or {}).get("工艺/验证") not in {None, "未提及"}
        )
    )
    risks = list(
        dict.fromkeys(
            _clean(item, 90)
            for profile in profiles
            for item in profile["limitations"][:2]
        )
    )[:4]
    minimal = questions[0]["experiment"] if questions else (
        "先复现一篇最接近的工作，再增加单变量消融。"
        if hardware
        else "先复现一篇最接近的工作，再增加单变量对照。"
    )
    if hardware:
        return {
            "domain": "hardware",
            "datasets": (datasets or ["最小公开数据集（待选定）"])[:6],
            "metrics": (metrics or ["任务精度", "能效", "延迟", "面积"])[:8],
            "baselines": (baselines or ["论文报告的最强 baseline（待核对）"])[:5],
            "hardware": hardware_conditions or ["仿真 / 硅测条件（待核对）"],
            "steps": [
                "固定一个最小数据集、网络/负载和评价指标，记录随机种子。",
                "复现选定 baseline，先对齐论文的测试条件与指标口径。",
                "加入候选改动的单变量消融，同时记录精度、能耗与面积。",
                "在第二组数据/工艺角上复核，报告失败案例与代价。",
            ],
            "minimal_experiment": minimal,
            "risks": risks or ["论文未给全部超参数", "测试条件与 baseline 不一致"],
        }
    return {
        "domain": "general",
        "datasets": (datasets or ["待选定的公开基准或自有数据"])[:6],
        "metrics": (metrics or ["任务成功率", "答案正确性", "忠实度"])[:8],
        "baselines": (baselines or ["论文报告的最强 baseline（待核对）"])[:5],
        "hardware": ["不涉及硬件条件"],
        "steps": [
            "固定最小数据、评价指标与随机种子，记录预处理与版本。",
            "复现选定 baseline，先对齐论文的测试条件与指标口径。",
            "加入候选改动的单变量对照，记录质量、延迟与成本。",
            "在第二组数据或场景上复核，报告失败案例与代价。",
        ],
        "minimal_experiment": minimal,
        "risks": risks or ["论文未给全部超参数", "测试条件与 baseline 不一致"],
    }


def _feasibility_detail(question: dict, profiles: list[dict], plan: dict) -> dict:
    datasets = plan.get("datasets") or []
    metrics = plan.get("metrics") or []
    hardware_domain = plan.get("domain") == "hardware"
    hardware = " ".join(plan.get("hardware") or []) if hardware_domain else ""
    experiment = str(question.get("experiment") or "")
    combined = " ".join([experiment, hardware, " ".join(datasets)])
    evidence_papers = question.get("evidence_papers") or []
    profile_by_id = {
        item["paper"].get("id"): item for item in profiles if item["paper"].get("id")
    }
    matched_profiles = [profile_by_id[pid] for pid in evidence_papers if pid in profile_by_id]
    silicon = hardware_domain and any(
        word in combined.lower() for word in ("硅验证", "流片", "tapeout", "工艺角", "silicon")
    )
    full_text = any(item["paper"].get("full_text") for item in matched_profiles)
    real_dataset = any("待" not in str(name) and "selected" not in str(name).lower() for name in datasets)

    data_access = 3 + (1 if real_dataset else 0) + (1 if matched_profiles else 0)
    if silicon:
        data_access = min(data_access, 3)
    data_access = max(1, min(5, data_access))
    data_reason = (
        f"可用数据集：{'、'.join(datasets[:2]) or '尚未选定'}；"
        + (
            "涉及硅测/流片条件，需要实验室资源。"
            if silicon
            else ("可先用公开数据或仿真复现。" if hardware_domain else "可先用公开数据或离线评测复现。")
        )
    )

    method_complexity = 3
    if silicon:
        method_complexity -= 1
    if any(word in experiment for word in ("复现", "最小", "单变量")):
        method_complexity += 1
    method_complexity = max(1, min(5, method_complexity))
    method_reason = (
        "以最小复现和单变量消融为主，方法链条可控。"
        if method_complexity >= 4
        else (
            "涉及额外的工艺/系统条件，需要先确认工具链与标定成本。"
            if hardware_domain
            else "涉及额外系统条件，需要先确认工具链与实现成本。"
        )
    )

    time_budget = 4
    if silicon:
        time_budget -= 1
    if any(word in experiment for word in ("所有", "全量", "全数据集")):
        time_budget -= 1
    if "最小实验" in experiment or "先" in experiment:
        time_budget += 1
    time_budget = max(1, min(5, time_budget))
    time_reason = (
        "可拆成本学期内的最小实验与一次复核。"
        if time_budget >= 4
        else (
            "实验范围可能超出一个学期，建议缩小条件或先做仿真验证。"
            if hardware_domain
            else "实验范围可能超出一个学期，建议缩小条件或先做离线验证。"
        )
    )

    structured_evidence = any(" p." in str(line) or "·" in str(line) for line in (question.get("evidence") or []))
    evidence_support = 2 + min(3, len(evidence_papers)) + (1 if full_text else 0) + (1 if structured_evidence else 0)
    evidence_support = max(1, min(5, evidence_support))
    evidence_reason = (
        f"{len(evidence_papers)} 篇候选证据，"
        + ("其中包含全文，可核对方法与测试条件。" if full_text else "目前以摘要/题录为主，需补全文核对。")
    )

    dimensions = [
        {"id": "data", "label": "数据可及性", "score": data_access, "reason": data_reason},
        {"id": "method", "label": "方法复杂度", "score": method_complexity, "reason": method_reason},
        {"id": "time", "label": "时间预算", "score": time_budget, "reason": time_reason},
        {"id": "evidence", "label": "文献支撑", "score": evidence_support, "reason": evidence_reason},
    ]
    overall = int(round(sum(item["score"] for item in dimensions) / len(dimensions) + 1e-6))
    overall = max(1, min(5, overall))
    if overall >= 4:
        verdict = "可开题"
        summary = "四个维度较均衡，可先做最小验证再扩大范围。"
    elif overall >= 3:
        verdict = "需收窄"
        summary = "存在至少一个短板，建议先缩小数据/工艺条件或补全文证据。"
    else:
        verdict = "风险偏高"
        summary = "数据、时间或方法条件不足，建议调整问题或寻找替代设计。"
    return {
        "overall": overall,
        "verdict": verdict,
        "summary": summary,
        "dimensions": dimensions,
        "datasets": datasets[:3],
        "metrics": metrics[:3],
    }


def _argument_card(question: dict, profiles: list[dict], plan: dict) -> dict:
    datasets = (plan.get("datasets") or [])[:3]
    metrics = (plan.get("metrics") or [])[:3]
    risks = question.get("risks") or plan.get("risks") or []
    dataset = datasets[0] if datasets else "最小公开数据集"
    metric = metrics[0] if metrics else "主要指标"
    hardware = plan.get("domain") == "hardware"
    return {
        "claim": question.get("question") or "",
        "gap": question.get("why_gap") or "",
        "evidence": (question.get("evidence") or [])[:3],
        "evidence_papers": question.get("evidence_papers") or [],
        "method": question.get("experiment") or plan.get("minimal_experiment") or "",
        "data": datasets,
        "metric": metrics,
        "success_criterion": (
            f"在{dataset}上，{metric}相对 baseline 出现可重复改善，"
            + ("同时明确报告能耗/时间/面积等代价与失败条件。" if hardware else "同时明确报告时间、成本与失败条件。")
        ),
        "failure_mode": risks[0] if risks else "证据不足或跨条件复现失败",
        "scope": "一个学期内可完成的最小研究问题；先证伪，再扩展。",
    }


def _advisor_brief(topic: str, questions: list[dict], profiles: list[dict], plan: dict) -> dict:
    top = questions[0] if questions else {}
    datasets = plan.get("datasets") or ["最小公开数据集（待选定）"]
    metrics = plan.get("metrics") or ["主要指标"]
    hardware_domain = plan.get("domain") == "hardware"
    hardware = (plan.get("hardware") or ["仿真 / 硅测条件（待核对）"]) if hardware_domain else []
    evidence = (top.get("evidence") or [])[:2]
    questions_for_advisor = [
        {
            "question": "这个研究缺口是否真实存在，值得投入一个学期吗？",
            "options": [
                "A. 先读关键证据并复现最小实验，再确认缺口",
                "B. 缩小到单一条件，只验证边界是否成立",
                "C. 转向相邻方法迁移，避开尚未收敛的缺口",
            ],
            "why": top.get("why_gap") or "需要先用证据确认缺口不是作者 future work。",
        },
        {
            "question": f"数据与实验条件如何落地？目前候选是 {datasets[0]}。",
            "options": [
                f"A. 先用 {datasets[0]} 跑最小实验",
                (
                    "B. 结合实验室已有数据/工艺条件，缩小验证范围"
                    if hardware_domain
                    else "B. 结合已有数据或公开数据，缩小验证范围"
                ),
                (
                    "C. 只做仿真验证，把硅测留作后续工作"
                    if hardware_domain
                    else "C. 先做离线评测，把真实部署留作后续工作"
                ),
            ],
            "why": "；".join(hardware[:2]) or "先确认数据可用性与评测口径，再决定是否扩大范围。",
        },
        {
            "question": "如果时间不够，优先保留哪一部分？",
            "options": [
                (
                    "A. 保最小复现与单变量消融，减少扩展实验"
                    if hardware_domain
                    else "A. 保最小复现与单变量对照，减少扩展实验"
                ),
                "B. 保留跨条件复核，缩小模型/数据规模",
                "C. 先完成证据综述与失败分析，降低实验承诺",
            ],
            "why": f"最小实验：{top.get('experiment') or plan.get('minimal_experiment')}",
        },
        {
            "question": "如何避免与已有工作重复？",
            "options": [
                f"A. 以 {metrics[0]} 为主线做统一口径对比",
                "B. 只针对一个未被验证的边界条件做深入实验",
                "C. 把贡献限定为复现、校准或负结果报告",
            ],
            "why": "候选问题的证据来自论文局限/缺口，需要在导师确认前补一次检索。",
        },
    ]
    anticipated = [
        {
            "question": "这个缺口是不是作者已经做完、只是没写进摘要？",
            "preparation": "先读 Discussion / Limitations / Future work，并核对全文与补充材料。",
            "evidence": evidence[0] if evidence else "准备一页证据摘录，标出页码与原文。",
        },
        {
            "question": "为什么选这个方案，而不是论文里的最强 baseline？",
            "preparation": "列出 baseline 的测试条件、指标口径与实现成本，说明可比性。",
            "evidence": "准备 baseline 对照表；不要只比较论文报告的单一数值。",
        },
        {
            "question": (
                "数据和算力/工艺条件从哪里来？"
                if hardware_domain
                else "数据和计算资源从哪里来？"
            ),
            "preparation": (
                f"确认 {datasets[0]} 的获取方式，并给出仿真/硅测两套备选。"
                if hardware_domain
                else f"确认 {datasets[0]} 的获取方式，并给出离线评测/真实场景两套备选。"
            ),
            "evidence": "准备数据许可、预处理脚本与最小实验时间表。",
        },
        {
            "question": "如果实验失败，这篇论文还成立吗？",
            "preparation": "预设可证伪的假设，并把负结果、失败条件与代价写成贡献的一部分。",
            "evidence": "准备失败判据与止损点，避免把预期提升当成结论。",
        },
    ]
    one_pager = "\n".join(
        [
            f"# 导师沟通一页纸：{topic}",
            "",
            f"我想讨论的问题：{top.get('question') or '一个可在学期内验证的研究问题。'}",
            "",
            f"为什么值得做：{top.get('why_gap') or '现有证据在跨条件下仍有缺口。'}",
            "",
            f"我准备怎么验证：{top.get('experiment') or plan.get('minimal_experiment')}",
            "",
            f"数据与指标：{datasets[0]}；{', '.join(metrics[:3])}",
            "",
            f"主要风险：{'；'.join((top.get('risks') or [])[:3]) or '证据不足、条件不一致'}",
            "",
            "我希望导师帮我判断：缺口是否成立、范围是否合适、资源是否够用。",
        ]
    )
    ask_template = (
        f"老师您好，我围绕“{topic}”整理了 1 篇选题 brief 和 "
        f"{len(questions_for_advisor)} 个带选项的问题，想占用您 15 分钟确认方向。"
        "我会先说明证据缺口、最小实验和备选方案，再请您判断是否需要收窄。"
    )
    return {
        "top_question": top.get("id"),
        "one_pager": one_pager,
        "ask_template": ask_template,
        "questions": questions_for_advisor,
        "anticipated": anticipated,
        "evidence_to_bring": evidence,
    }


def topic_studio(topic: str, scope: str = "all", paper_ids: list[str] | None = None) -> dict:
    papers = _scope_papers(scope, paper_ids or [])
    profiles = _profiles(papers, topic)
    cluster_counts = Counter(item["cluster"] for item in profiles)
    clusters = [
        {
            "label": label,
            "count": count,
            "share": round(count / max(len(profiles), 1) * 100, 1),
            "papers": [
                {"cite": item["cite"], "paper_id": item["paper"].get("id"), "title": item["paper"].get("title")}
                for item in profiles
                if item["cluster"] == label
            ],
        }
        for label, count in cluster_counts.most_common()
    ]
    years = [item["paper"].get("year") for item in profiles if item["paper"].get("year")]
    timeline = [
        {"year": year, "count": count}
        for year, count in sorted(Counter(years).items())
    ]
    gaps = _relevant_gaps(topic)
    questions = _curated_questions(profiles, gaps)
    generated = _generated_questions(topic, profiles, 3)
    seen_questions = {item["question"] for item in questions}
    for item in generated:
        if item["question"] not in seen_questions:
            questions.append(item)
            seen_questions.add(item["question"])
    questions.sort(key=lambda item: -(item["novelty"] + item["feasibility"] + item["impact"]))
    questions = questions[:5]
    reading_path = _reading_path(profiles)
    experiment_plan = _experiment_plan(profiles, questions)
    for question in questions:
        question["feasibility_detail"] = _feasibility_detail(question, profiles, experiment_plan)
        question["argument_card"] = _argument_card(question, profiles, experiment_plan)
    advisor = _advisor_brief(topic, questions, profiles, experiment_plan)
    argument_cards = [
        {
            "id": question.get("id"),
            "title": question.get("title"),
            "feasibility": question["feasibility_detail"]["overall"],
            **question["argument_card"],
        }
        for question in questions
    ]
    top_cluster = clusters[0]["label"] if clusters else "待定方向"
    top_question = questions[0] if questions else None
    datasets = experiment_plan["datasets"]
    metrics = experiment_plan["metrics"]
    hardware = bool(profiles) and profiles[0].get("domain") == "hardware"
    if hardware:
        thesis = (
            f"围绕“{topic}”，现有工作主要落在{ '、'.join(item['label'] for item in clusters[:3]) }等方向；"
            f"本选题聚焦“{top_question['title'] if top_question else '可验证的边界问题'}”，"
            f"以{top_cluster}为方法基线，在{datasets[0]}上验证{metrics[0]}与代价的权衡。"
        )
        contributions = [
            f"系统比较 {len(profiles)} 篇相关工作的{top_cluster}路线，建立统一指标口径。",
            (
                f"针对“{_clean(top_question['title'], 40)}”提出可验证的改进或校准方案。"
                if top_question
                else "从证据缺口提炼一个可在学期内完成的最小研究问题。"
            ),
            f"在 {datasets[0]} 上报告 {', '.join(metrics[:3])} 与 baseline 的对照，并公开失败案例。",
        ]
        keywords = list(dict.fromkeys([top_cluster, *[item["label"] for item in clusters[1:3]], topic]))[:6]
    else:
        thesis = (
            f"围绕“{topic}”，本简报汇总 {len(profiles)} 篇相关工作的证据边界；"
            f"选题聚焦“{top_question['title'] if top_question else '可验证的边界问题'}”，"
            "先核对原文条件，再设计最小验证。"
        )
        contributions = [
            f"系统比较 {len(profiles)} 篇相关工作的问题定义、方法与证据边界。",
            (
                f"针对“{_clean(top_question['title'], 40)}”提出可验证的改进或对照方案。"
                if top_question
                else "从证据缺口提炼一个可在学期内完成的最小研究问题。"
            ),
            f"在可复现的最小实验上报告 {metrics[0]} 与 baseline 的对照，并公开失败案例。",
        ]
        keywords = [topic]
    scope_text = (
        f"{len(profiles)} 篇论文 · {len(clusters)} 个方法簇 · "
        f"{min(years) if years else '—'}–{max(years) if years else '—'}"
        if hardware
        else f"{len(profiles)} 篇论文 · {min(years) if years else '—'}–{max(years) if years else '—'}"
    )
    brief = {
        "working_title": f"{topic}：面向{_clean(top_question['title'], 30)}的研究" if top_question else f"{topic}：现状、边界与可验证问题",
        "thesis": thesis,
        "contributions": contributions,
        "scope": scope_text,
        "keywords": keywords,
        "risks": experiment_plan["risks"][:3],
    }
    profile_payload = [
        {
            "cite": item["cite"],
            "paper_id": item["paper"].get("id"),
            "title": item["paper"].get("title"),
            "year": item["paper"].get("year"),
            "venue": item["paper"].get("venue"),
            "cluster": item["cluster"],
            "limitations": item["limitations"][:2],
            "datasets": item["datasets"],
            "metrics": item["metrics"][:3],
        }
        for item in profiles
    ]
    return {
        "topic": topic,
        "scope": scope,
        "scope_label": SCOPE_LABELS.get(scope, "全部语料"),
        "stats": {
            "papers": len(profiles),
            "clusters": len(clusters),
            "questions": len(questions),
            "year_range": f"{min(years)}–{max(years)}" if years else "—",
        },
        "clusters": clusters,
        "timeline": timeline,
        "questions": questions,
        "reading_path": reading_path,
        "experiment_plan": experiment_plan,
        "advisor": advisor,
        "argument_cards": argument_cards,
        "brief": brief,
        "papers": profile_payload,
        "note": "研究版图与候选问题由规则聚类和证据缺口生成，用于选题讨论；不是对研究价值的最终判断。",
    }


def _reference(profile: dict) -> dict:
    paper = profile["paper"]
    return {
        "cite": profile["cite"],
        "paper_id": paper.get("id"),
        "title": paper.get("title"),
        "authors": paper.get("authors", []),
        "year": paper.get("year"),
        "venue": paper.get("venue"),
        "doi": paper.get("doi"),
        "url": paper.get("url"),
    }


def _related_work(topic: str, profiles: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = {}
    for profile in profiles:
        grouped.setdefault(profile["cluster"], []).append(profile)
    sections = []
    for cluster, items in sorted(grouped.items(), key=lambda pair: -len(pair[1])):
        if cluster == "相关工作":
            opener = f"围绕“{topic}”，以下 {len(items)} 篇工作按问题、方法与证据边界对照。"
        elif len(items) > 1:
            opener = f"在{cluster}方向，已有 {len(items)} 篇工作可按问题、方法与证据边界对照。"
        else:
            opener = ""
        paragraphs = [opener] if opener else []
        for profile in items[:4]:
            paper = profile["paper"]
            read = profile["read"]
            problem = read.get("problem") or paper.get("abstract") or paper.get("title")
            method = read.get("method") or "提出了一种面向该问题的方案"
            result = (
                f"{analysis.coerce_text(paper['results'][0].get('name'), 80)} "
                f"{analysis.coerce_text(paper['results'][0].get('value'), 80)}"
                if paper.get("results")
                else "报告了实验或仿真评估"
            )
            limitation = profile["limitations"][0] if profile["limitations"] else "仍需核对更多测试条件"
            paragraphs.append(
                _sentence(
                    f"{profile['cite']} {_join_cjk('针对', _strip_lead(_inline(problem, 60), 'problem'))}，"
                    f"{_join_cjk('采用', _strip_lead(_inline(method, 70), 'method'))}，"
                    f"{_join_cjk('报告', _strip_lead(_inline(result, 45), 'result'))}；"
                    f"其边界是{_inline(limitation, 70)}。",
                    430,
                )
            )
        sections.append(
            {
                "title": cluster,
                "paragraphs": paragraphs,
                "citations": [item["cite"] for item in items[:4]],
            }
        )
    if len(grouped) == 1:
        summary = [
            f"共汇总 {len(profiles)} 篇相关工作；以上按论文逐篇对照问题、方法与证据边界。",
            "不同工作的数据集、评价指标与测试条件并不一致，相关工作中的数值不应直接排序。",
            "可从局限性与未测组合中提炼研究问题，而不是把 future work 直接当作创新点。",
        ]
    else:
        summary = [
            f"现有工作分布在{len(grouped)}个方法簇；其中{max(grouped, key=lambda key: len(grouped[key]))}方向的证据最集中。",
            "不同工作的数据集、评价指标与测试条件并不一致，相关工作中的数值不应直接排序。",
            "可从局限性与未测组合中提炼研究问题，而不是把 future work 直接当作创新点。",
        ]
    return {"sections": sections, "summary": summary}


def _outline(topic: str, profiles: list[dict], plan: dict, questions: list[dict]) -> dict:
    groups = Counter(item["cluster"] for item in profiles)
    top_question = questions[0] if questions else None
    sections = [
        {
            "title": "摘要骨架",
            "bullets": [
                f"问题：{topic}中的核心瓶颈与现有边界。",
                f"方法：围绕{(top_question['title'].split('：')[0] if top_question else '可验证缺口')}提出候选方案，并与现有方法对照。",
                f"验证：在{', '.join(plan['datasets'][:2])}上报告{', '.join(plan['metrics'][:3])}。",
                "贡献：方法/校准/评测三选一，避免同时宣称过多贡献。",
            ],
        },
        {
            "title": "1 引言",
            "bullets": [
                f"背景：用 2–3 句说明{topic}为何重要。",
                f"缺口：{top_question['why_gap'] if top_question else '现有工作在跨条件下缺少验证。'}",
                f"本文问题：{top_question['question'] if top_question else '一个可在学期内验证的具体问题。'}",
                "贡献列表：与实验计划一一对应。",
            ],
        },
        {
            "title": "2 相关工作",
            "bullets": [
                f"{cluster}：{count} 篇，比较问题定义、方法与证据边界。"
                for cluster, count in groups.most_common()
            ]
            + ["结尾用一段对比表总结差异，避免逐篇流水账。"],
        },
        {
            "title": "3 方法",
            "bullets": [
                "画出数据流与关键模块，标注与 baseline 的差异。",
                f"说明候选改动如何作用于{', '.join(plan['metrics'][:2])}。",
                "明确假设、超参数与不适用的场景。",
            ],
        },
        {
            "title": "4 实验设计",
            "bullets": [
                f"数据集：{', '.join(plan['datasets'][:3])}。",
                f"指标：{', '.join(plan['metrics'][:4])}。",
                f"baseline：{', '.join(plan['baselines'][:3])}。",
                f"最小实验：{plan['minimal_experiment']}",
            ],
        },
        {
            "title": "5 预期结果与风险",
            "bullets": [
                "预期趋势要写成可证伪的假设，而不是承诺提升。",
                f"风险：{'; '.join(plan['risks'][:3])}",
                "失败实验同样要报告：至少给出失败条件与代价。",
            ],
        },
        {"title": "6 结论", "bullets": ["回答问题、总结证据、给出边界与下一步。"]},
    ]
    return {"sections": sections}


def _abstract(topic: str, profiles: list[dict], plan: dict, questions: list[dict]) -> str:
    top_question = questions[0] if questions else None
    methods = "、".join(dict.fromkeys(item["cluster"] for item in profiles))[:80]
    return (
        f"针对{topic}中“{(top_question['title'].split('：')[0] if top_question else '跨条件下证据不足')}”的问题，"
        f"本文提出一种结合{methods or '现有方法'}的研究方案。"
        f"我们在{', '.join(plan['datasets'][:2])}上以{', '.join(plan['metrics'][:3])}为指标，"
        f"与{', '.join(plan['baselines'][:2])}进行对照，并报告代价与失败条件。"
        "当前结果为待验证的模板骨架，所有数字需在实验完成后回填，不得用预期值替代实测值。"
    )


def draft(topic: str, kind: str = "related_work", scope: str = "all",
          paper_ids: list[str] | None = None) -> dict:
    papers = _scope_papers(scope, paper_ids or [])
    if not papers:
        raise ValueError("当前范围没有论文，无法生成草稿。")
    profiles = _profiles(papers, topic)
    questions = topic_studio(topic, scope, paper_ids)["questions"]
    plan = _experiment_plan(profiles, questions)
    references = [_reference(item) for item in profiles]
    if kind == "outline":
        body = _outline(topic, profiles, plan, questions)
        title = f"论文提纲：{topic}"
        markdown_parts = [f"# {title}", "", "> 模板草稿：需人工改写，数字与结论必须回原文核对。", ""]
        for section in body["sections"]:
            markdown_parts.append(f"## {section['title']}")
            markdown_parts.extend(f"- {bullet}" for bullet in section["bullets"])
            markdown_parts.append("")
    elif kind == "abstract":
        text = _abstract(topic, profiles, plan, questions)
        body = {"text": text}
        title = f"摘要骨架：{topic}"
        markdown_parts = [f"# {title}", "", "> 模板草稿，不可直接作为最终摘要。", "", text, ""]
    else:
        kind = "related_work"
        body = _related_work(topic, profiles)
        title = f"相关工作草稿：{topic}"
        markdown_parts = [f"# {title}", "", "> 模板草稿：由结构化字段与证据拼接，需人工改写与核对。", ""]
        for section in body["sections"]:
            markdown_parts.append(f"## {section['title']}")
            markdown_parts.extend(section["paragraphs"])
            markdown_parts.append("")
        markdown_parts.append("## 对比小结")
        markdown_parts.extend(f"- {item}" for item in body["summary"])
        markdown_parts.append("")
    markdown_parts.append("## 参考文献")
    for ref in references:
        authors = ", ".join(ref["authors"][:3]) + (" 等" if len(ref["authors"]) > 3 else "")
        markdown_parts.append(
            f"- [{ref['cite']}] {authors}. {ref['title']}. {ref['venue']}, {ref['year']}."
            + (f" DOI: {ref['doi']}" if ref.get("doi") else "")
        )
    markdown = "\n".join(markdown_parts).strip() + "\n"
    return {
        "topic": topic,
        "kind": kind,
        "kind_label": DRAFT_LABELS.get(kind, "草稿"),
        "scope": scope,
        "scope_label": SCOPE_LABELS.get(scope, "全部语料"),
        "title": title,
        "body": body,
        "references": references,
        "markdown": markdown,
        "paper_count": len(profiles),
        "disclaimer": "模板草稿用于搭结构，不是最终论文文本；每条论断都要回到原文核对，数字不得用预期值代替实测值。",
    }


CHINESE_GENERIC = {
    "本文", "文提", "提出", "出一", "一种", "该方", "方法", "并且", "并在", "以及",
    "其中", "一个", "我们", "研究", "结果", "表明", "显示", "可以", "能够", "通过",
    "论文", "工作", "实验", "评估", "分别", "同时", "但是", "然而", "因此", "由于",
    "对于", "的是", "中的", "上的", "以及", "及其", "进行", "实现", "采用", "具有",
}


def _concept_tokens(text: str, expand: bool = True) -> set[str]:
    source = search._expand_query(text) if expand else text
    tokens: set[str] = set()
    for token in ask._tokenize(source):
        if token in CHINESE_GENERIC:
            continue
        if re.fullmatch(r"[a-z0-9][a-z0-9.\-]*", token) and len(token) > 3:
            if token.endswith("es") and len(token) > 4:
                token = token[:-2]
            elif token.endswith("s") and not token.endswith("ss"):
                token = token[:-1]
        tokens.add(token)
    return tokens


def _is_latin(token: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9.\-]*", token))


def _overlap_ratio(claim_tokens: set[str], evidence_tokens: set[str]) -> float:
    if not claim_tokens:
        return 0.0
    overall = len(claim_tokens & evidence_tokens) / len(claim_tokens)
    claim_latin = {token for token in claim_tokens if _is_latin(token)}
    if not claim_latin:
        return overall
    evidence_latin = {token for token in evidence_tokens if _is_latin(token)}
    latin = len(claim_latin & evidence_latin) / len(claim_latin)
    return max(overall, latin)


def _quantity_keys(values: list[dict]) -> set[str]:
    keys = set()
    for item in values:
        keys.add(re.sub(r"\s+", "", str(item.get("text") or "").lower()))
        keys.add(str(item.get("value") or "").lower())
    return keys


def check_claims(text: str, scope: str = "all", paper_ids: list[str] | None = None) -> dict:
    papers = _scope_papers(scope, paper_ids or [])
    passages = ask._passages(papers)
    cleaned_lines = []
    for line in (text or "").splitlines():
        value = line.strip()
        if not value or value.startswith(("#", ">", "|", "```")):
            continue
        if re.match(r"^[-*]\s*\[P\d+\]", value):
            continue
        cleaned_lines.append(value)
    source_text = "\n".join(cleaned_lines)
    sentences = [
        part.strip()
        for part in re.split(r"[。！？!?]+|\n+|(?<=\.)\s+", source_text)
        if len(part.strip()) >= 10
    ][:40]
    results = []
    for sentence in sentences:
        ranked = ask._rank(sentence, passages, 3)
        unique_ranked = []
        seen_ranked = set()
        for candidate in ranked:
            key = (candidate["paper_id"], candidate["section"])
            if key in seen_ranked:
                continue
            seen_ranked.add(key)
            unique_ranked.append(candidate)
        ranked = unique_ranked
        claim_quantities = ask._quantities(sentence)
        if not ranked:
            results.append(
                {
                    "sentence": sentence,
                    "level": "unsupported",
                    "label": "未找到证据",
                    "overlap": 0,
                    "citation": None,
                    "citations": [],
                    "quantities": claim_quantities,
                    "missing_quantities": [item["text"] for item in claim_quantities],
                    "quantity_match": False,
                    "issues": ["当前范围内没有检索到可引用的匹配证据"],
                    "suggestion": "补充引用、缩小论断范围，或把该句改为待验证假设。",
                }
            )
            continue
        evidence_items = ranked[:3]
        best = evidence_items[0]
        combined_text = " ".join(item["text"] for item in evidence_items)
        claim_tokens = _concept_tokens(sentence, expand=True)
        evidence_tokens = _concept_tokens(combined_text, expand=False)
        overlap = _overlap_ratio(claim_tokens, evidence_tokens)
        evidence_quantities = ask._quantities(combined_text, 24)
        evidence_keys = _quantity_keys(evidence_quantities)
        missing_items = [
            item
            for item in claim_quantities
            if re.sub(r"\s+", "", item["text"].lower()) not in evidence_keys
            and str(item["value"]) not in evidence_keys
        ]
        if missing_items and evidence_items:
            paper_ids = {item["paper_id"] for item in evidence_items}
            extras = []
            for passage in passages:
                if passage["paper_id"] not in paper_ids:
                    continue
                keys = _quantity_keys(ask._quantities(passage["text"], 12))
                remaining = []
                for item in missing_items:
                    normalized = re.sub(r"\s+", "", item["text"].lower())
                    if normalized in keys or str(item["value"]) in keys:
                        if passage not in extras:
                            extras.append(passage)
                    else:
                        remaining.append(item)
                missing_items = remaining
                if not missing_items:
                    break
            if extras:
                for extra in extras[:2]:
                    index = next(
                        (
                            position
                            for position, item in enumerate(evidence_items)
                            if item["paper_id"] == extra["paper_id"]
                            and item["section"] == extra["section"]
                        ),
                        None,
                    )
                    if index is None:
                        evidence_items.append(extra)
                    else:
                        evidence_items[index] = extra
                combined_text = " ".join(item["text"] for item in evidence_items)
                evidence_tokens = _concept_tokens(combined_text, expand=False)
                overlap = _overlap_ratio(claim_tokens, evidence_tokens)
        missing = [item["text"] for item in missing_items]
        quantity_match = bool(claim_quantities) and not missing
        if overlap >= 0.45 and not missing:
            level, label = "supported", "直接支持"
        elif overlap >= 0.18 or (quantity_match and overlap >= 0.08):
            level, label = "partial", "部分支持"
        else:
            level, label = "unsupported", "证据不足"
        issues = []
        if missing:
            issues.append("数值未在匹配证据中出现：" + "、".join(missing[:4]))
        if overlap < 0.35:
            issues.append("措辞与证据重合度较低，建议核对引用或改写")
        if not claim_quantities:
            issues.append("该句没有可核对数值；若为结论性表述，建议补充定量证据")
        if level == "supported":
            suggestion = "可保留，但仍需核对测试条件与引用范围。"
        elif level == "partial":
            suggestion = "建议缩小论断、补充第二篇证据，或把绝对化措辞改为条件化表述。"
        else:
            suggestion = "当前证据不足以支持该句，建议补实验/补引用或删除强结论。"
        results.append(
            {
                "sentence": sentence,
                "level": level,
                "label": label,
                "overlap": round(overlap, 2),
                "citation": {
                    "paper_id": best["paper_id"],
                    "title": best["title"],
                    "section": best["section"],
                    "page": best["page"],
                    "quote": ask._clip(best["text"], 300),
                    "score": best["score"],
                },
                "citations": [
                    {
                        "paper_id": item["paper_id"],
                        "title": item["title"],
                        "section": item["section"],
                        "page": item["page"],
                        "quote": ask._clip(item["text"], 300),
                        "score": item.get("score", best["score"]),
                    }
                    for item in evidence_items
                ],
                "quantities": claim_quantities,
                "missing_quantities": missing,
                "quantity_match": quantity_match,
                "issues": issues,
                "suggestion": suggestion,
            }
        )
    counts = Counter(item["level"] for item in results)
    quantity_issues = sum(1 for item in results if item["missing_quantities"])
    quality = (
        counts.get("supported", 0) + counts.get("partial", 0) * 0.5
    ) / max(len(results), 1)
    if quality >= 0.7 and not quantity_issues:
        overall, overall_label = "strong", "证据较充分"
    elif quality >= 0.4:
        overall, overall_label = "mixed", "需要补充引用"
    else:
        overall, overall_label = "weak", "证据不足"
    if quantity_issues and overall == "mixed":
        overall_label = "需要补充引用与数值"
    cited = {}
    for item in results:
        citation = item.get("citation")
        if citation and citation["paper_id"] not in cited:
            cited[citation["paper_id"]] = citation
    return {
        "scope": scope,
        "scope_label": SCOPE_LABELS.get(scope, "全部语料"),
        "summary": {
            "total": len(results),
            "supported": counts.get("supported", 0),
            "partial": counts.get("partial", 0),
            "unsupported": counts.get("unsupported", 0),
            "quantity_issues": quantity_issues,
            "overall": overall,
            "overall_label": overall_label,
        },
        "results": results,
        "citations": list(cited.values()),
        "papers_considered": len(papers),
        "note": "核查结果是“文本与检索证据的重合度”与数值一致性，不等同于事实真伪判定；请回到引用原文核对测试条件。",
    }
