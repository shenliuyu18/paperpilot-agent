from __future__ import annotations

"""Built-in demo corpus.

The metadata and abstracts come from public arXiv records. Structured fields
marked with ``extraction_level`` are PaperPilot's demo extractions. Papers with
``full_text=True`` also have selected full-text excerpts parsed from their PDFs.
The UI labels the extraction level so nobody mistakes a summary for the paper.
"""

GLOSSARY = {
    "compute-in-memory": "存内计算",
    "CIM": "存内计算",
    "analog compute-in-memory": "模拟存内计算",
    "SAR ADC": "逐次逼近型模数转换器",
    "ADC": "模数转换器",
    "sense amplifier": "灵敏放大器（感放大器）",
    "SA": "灵敏放大器",
    "ENOB": "有效位数",
    "FoM": "品质因数",
    "Top-1 accuracy": "Top-1 准确率",
    "hardware-aware training": "硬件感知训练",
    "prefix reuse": "前缀复用",
    "in-memory ADC": "存内模数转换器",
    "IMA": "存内模数转换器",
    "SNN": "脉冲神经网络",
    "LIF": "漏积分放电神经元",
    "KWN": "Top-K 胜者选择",
    "NLD": "非线性树突",
    "switched-capacitor": "开关电容",
    "charge redistribution": "电荷再分配",
    "read noise": "读噪声",
    "stochastic": "随机的",
    "low-rank approximation": "低秩近似",
    "singular value": "奇异值",
}

from .glossary import EXTRA_GLOSSARY, GLOSSARY_CATEGORY_OF

for _term, _zh in EXTRA_GLOSSARY.items():
    GLOSSARY.setdefault(_term, _zh)
for _term in GLOSSARY:
    GLOSSARY_CATEGORY_OF.setdefault(_term, "芯片与存算")

PAPERS = [
    {
        "id": "2609.24288v1",
        "title": "Circuit-Architecture-Training Co-Design with Regenerative-SA Similarity Sensing for Aggressive SAR Skipping in Analog Compute-in-Memory",
        "authors": ["Yufei Liu", "Shuang Liu", "Junjie Wang"],
        "year": 2026,
        "venue": "arXiv preprint",
        "source": "arxiv",
        "url": "http://arxiv.org/abs/2609.24288v1",
        "pdf_url": "https://arxiv.org/pdf/2609.24288v1",
        "demo": False,
        "full_text": True,
        "extraction_level": "全文节选 + 结构化抽取",
        "tldr": "把“相邻 CIM 输出往往很相似”变成硬件加速机会：用再生型感放大器做相似性检测，命中后复用 SAR 高位前缀、跳过若干次比较，再用硬件感知训练补偿误差。55 nm 原理图仿真下，WRN-28-10 的 SAR 比较次数减少 48.19%，Top-1 只下降 1.1 个百分点。",
        "problem": "模拟存内计算中 ADC 是功耗和面积瓶颈；相邻输出存在数值相似性，但传统 SAR 仍逐次完整转换。",
        "method": "两级互补输入再生 SA 组成双侧相似窗口，在 ADC 转换当前样本时并行比较相邻样本；若 NEAR，则复用 k 位 MSB、跳过 k 次 SAR 比较；用硬件感知蒸馏训练补偿 SA 扰动和前缀复用编码误差。",
        "novelty": [
            "把 SA 的再生特性用于模拟输出相似性检测，而不只是做一次电压比较",
            "相似性引导的 SAR 前缀复用与跳比较机制，把“数值局部性”变成可执行控制",
            "把 SA 扰动和前缀复用误差注入 teacher-student 训练，跨 CNN 与 Transformer 验证",
        ],
        "assumptions": [
            "相邻 CIM 输出具有可利用的数值局部性",
            "SA 扰动和前缀复用误差可以在训练中建模",
            "TT、1.2 V、27 °C 的原理图仿真能代表主要行为",
        ],
        "metrics": {
            "工艺/验证": "55 nm CMOS，原理图仿真（非流片）",
            "ADC 方案": "共享 SAR ADC + SA 相似性检测",
            "网络/任务": "WRN-28-10 / ResNet20 / DeiT，CIFAR-100",
            "Top-1": "77.3%（W4A4 baseline 78.4%）",
            "SAR 比较": "减少 48.19%",
            "ADC 能耗": "减少 27.18%",
            "单次转换余量": "0.52 pJ/conversion",
            "检测器能耗": "57.10 / 61.31 fJ per 2.5 ns event（两类窗口）",
        },
        "results": [
            {"name": "WRN-28-10 Top-1", "value": "77.3%", "baseline": "78.4% (W4A4)", "condition": "40 mV / skip-6 代表性工作点，摘要报告"},
            {"name": "SAR 比较减少", "value": "48.19%", "baseline": "native SAR", "condition": "CIFAR-100，W4A4，ISAAC-based CIM 模型"},
            {"name": "参考 ADC 能耗", "value": "-27.18%", "baseline": "未跳比较", "condition": "计入检测器开销后的能量预算分析"},
            {"name": "ResNet20 Top-1", "value": "66.7%", "baseline": "67.0% (W4A4)", "condition": "20 mV / skip-4，比较减少 27.33%"},
            {"name": "DeiT Top-1", "value": "72.8%", "baseline": "75.5% (W4A4)", "condition": "20 mV / skip-4，误差感知训练后"},
        ],
        "baselines": ["W4A4 baseline（78.4%）", "FP32（82.1%）", "native SAR（不跳比较）"],
        "limitations": [
            "只有 55 nm 原理图仿真，没有硅验证",
            "工艺角变化和时钟相位噪声未纳入模型",
            "SA 扰动只用 25 个代表性事件提取，未给出全范围波形峰值误差界",
            "模拟相似不保证数字前缀一致，必须依赖训练补偿",
        ],
        "code_url": None,
        "evidence": [
            {"field": "problem", "label": "ADC 是主要瓶颈", "section": "Abstract", "page": 1,
             "quote": "Analog-to-digital conversion constitutes a major energy and area bottleneck in analog compute-in-memory (CIM) systems."},
            {"field": "problem", "label": "相邻输出相似", "section": "I. Introduction", "page": 1,
             "quote": "Neighboring CIM outputs often exhibit value similarity, creating opportunities to reduce redundant ADC effort."},
            {"field": "method", "label": "SA 相似性检测器", "section": "II. Proposed Work", "page": 2,
             "quote": "As shown in Fig. 1, our detector is implemented in a 55-nm CMOS process based on the complementary-input StrongARM topology, and consists of two complementary SA cores, fixed-ratio resistive threshold front-ends, and CMOS decision/latch logic."},
            {"field": "method", "label": "732 个合法事件全通过", "section": "II. Proposed Work", "page": 2,
             "quote": "Across 43 discrete common-mode settings spanning 0–1.2 V, all 732 legal tested guard events are correctly classified: 390/390 and 342/342."},
            {"field": "method", "label": "前缀复用与跳比较", "section": "II. Proposed Work", "page": 2,
             "quote": "For a B-bit SAR ADC, reusing the first k MSBs partitions the input range into coarse voltage intervals of width Wk = 2^(B−k) VLSB = VFS / 2^k."},
            {"field": "method", "label": "硬件感知蒸馏", "section": "II. Proposed Work", "page": 3,
             "quote": "The student is optimized using L = LCE + λLKD, and a straight-through estimator passes gradients through nondifferentiable operations."},
            {"field": "result", "label": "主要结果", "section": "Abstract", "page": 1,
             "quote": "On WRN-28-10, the proposed approach achieves 77.3% Top-1 accuracy (W4A4 baseline: 78.4%) while reducing SAR comparisons by 48.19% across the evaluated layers."},
            {"field": "limitation", "label": "模型边界", "section": "II.C", "page": 2,
             "quote": "Process variation and clock-phase noise are outside the present model."},
        ],
        "sections": [
            {"title": "Abstract", "page": 1, "text": "This work presents a circuit–architecture–training co-design framework that exploits sense-amplifier (SA) regeneration to detect analog-output similarity and reduce SAR comparisons in compute-in-memory (CIM) systems. Hardware-aware training incorporates circuit-characterized SA disturbance and encoding errors caused by prefix reuse, enabling aggressive comparison skipping. The detector is characterized through 55-nm CMOS schematic simulations, with system-level evaluation on WRN-28-10, ResNet20, and DeiT using an ISAAC-based W4A4 CIM model. On WRN-28-10, the proposed approach achieves 77.3% Top-1 accuracy (W4A4 baseline: 78.4%) while reducing SAR comparisons by 48.19% across the evaluated layers. Energy-budget analysis estimates a 27.18% reduction in reference ADC energy after detector overhead, leaving 0.52 pJ per conversion to accommodate additional control and peripheral costs."},
            {"title": "I. Introduction — ADC 瓶颈与数值局部性", "page": 1, "text": "Analog-to-digital conversion constitutes a major energy and area bottleneck in analog compute-in-memory (CIM) systems. For example, ISAAC reports that ADCs account for 58% of the tile power and 31% of the tile area. Several prior works have reduced ADC overhead in analog CIM systems. Neighboring CIM outputs often exhibit value similarity, creating opportunities to reduce redundant ADC effort. Jo et al. exploited this locality using auxiliary comparators to bypass selected SAR decisions. Building on this principle, we exploit similarity between held analog results at the interface between parallel crossbar outputs and shared SAR ADCs, using a lightweight detector to guide SAR-comparison skipping for the next conversion."},
            {"title": "II.A — 模拟相似性检测器", "page": 2, "text": "As shown in Fig. 1, our detector is implemented in a 55-nm CMOS process based on the complementary-input StrongARM topology, and consists of two complementary SA cores, fixed-ratio resistive threshold front-ends, and CMOS decision/latch logic. The passive resistive front-end sets the two-sided similarity window with minimal active-energy overhead, while the complementary-input SA pair provides low-energy comparison over the full common-mode range. At nominal TT conditions (1.2 V, 27 °C), the two configurations have designed half-window widths of 19 and 38 mV. Across 43 discrete common-mode settings spanning 0–1.2 V, all 732 legal tested guard events are correctly classified: 390/390 and 342/342."},
            {"title": "II.B — 相似性引导的 SAR 跳位", "page": 2, "text": "We place the SA-based similarity detector at the interface between a highly parallel analog CIM array and its shared SAR-ADC readout; while the ADC converts the held result Sn, the detector concurrently compares Sn with Sn+1 to prepare the similarity decision for the next conversion. For a B-bit SAR ADC, reusing the first k MSBs partitions the input range into coarse voltage intervals of width Wk = 2^(B−k) VLSB = VFS / 2^k. NEAR means reuse k MSBs and skip k comparisons; FAR means native SAR. However, |Sn+1 − Sn| < T does not guarantee that Sn and Sn+1 share the same k-bit prefix, so (T, k) is a joint aggressiveness design space rather than an error-free mapping."},
            {"title": "II.C — 硬件感知误差适配", "page": 3, "text": "Introducing the SA detector and aggressive SAR skipping adds two modeled nonidealities: SA-induced voltage disturbance and SAR encoding errors from prefix reuse. Process variation and clock-phase noise are outside the present model. For each training image, a frozen clean W4A4 teacher produces a reference prediction, while a trainable W4A4 student executes the hardware-impaired path. Its forward pass explicitly includes SA-induced voltage disturbance and SAR prefix reuse; the voltage shift precedes quantization, while NEAR/FAR decisions control prefix reuse and the resulting encoding errors. The student is optimized using L = LCE + λLKD, and a straight-through estimator passes gradients through nondifferentiable operations."},
            {"title": "III — 结果与跨模型验证", "page": 4, "text": "All detector results are obtained from nominal 55-nm CMOS schematic simulations at 1.2 V, TT, and 27 °C. For WRN-28-10, the FP32 and W4A4 baselines achieve 82.1% and 78.4% Top-1 accuracy, respectively. For ResNet20, the 20-mV/skip-4 operating point achieves 66.7% Top-1 accuracy, closely matching the 67.0% W4A4 reference, while reducing SAR comparisons by 27.33%. For DeiT, training substantially mitigates the degradation caused by hardware errors: at the 20-mV setting, skip-4/5/6 accuracies improve from 48.7/31.9/24.7% without error-aware training to 72.8/71.5/70.2%, respectively."},
        ],
        "figures": [
            {"title": "图 1 — 双侧相似性检测器", "page": 2, "what": "两个互补输入 SA 把相邻保持电压的差值映射成 NEAR/FAR 标志。", "how_to_read": "先看输入窗口 T，再看 SA 阵列如何把“差值是否落在窗口内”变成数字标志。"},
            {"title": "图 3 — teacher-student 训练流", "page": 3, "what": "冻结的干净 W4A4 teacher 指导带硬件误差的 student。", "how_to_read": "注意误差注入的位置：电压扰动在量化之前，前缀复用改变的是 SAR 搜索范围。"},
            {"title": "公式 (1) — 前缀复用的量化间隔", "page": 2, "what": "Wk = VFS / 2^k，k 越大，保留的搜索区间越窄，节省的比较次数越多。", "how_to_read": "把 k 理解为“直接抄前 k 位”，T 理解为“敢抄的电压容差”。"},
            {"title": "公式 (5) — 蒸馏损失", "page": 3, "what": "L = LCE + λLKD，分类损失与 teacher 蒸馏损失共同训练 student。", "how_to_read": "LKD 让 student 的输出分布靠近干净 teacher，从而间接学习硬件误差的影响。"},
        ],
        "translation": [
            {"en": "Neighboring CIM outputs often exhibit value similarity, creating opportunities to reduce redundant ADC effort.", "zh": "相邻的 CIM 输出往往具有数值相似性，这为减少冗余的 ADC 工作提供了机会。"},
            {"en": "For a B-bit SAR ADC, reusing the first k MSBs partitions the input range into coarse voltage intervals.", "zh": "对于一个 B 位 SAR ADC，复用前 k 个最高有效位会把输入范围划分成更粗的电压区间。"},
            {"en": "On WRN-28-10, the proposed approach achieves 77.3% Top-1 accuracy while reducing SAR comparisons by 48.19%.", "zh": "在 WRN-28-10 上，该方法达到 77.3% 的 Top-1 准确率，同时将 SAR 比较次数减少 48.19%。"},
        ],
        "repo_plan": {
            "environment": ["Python 3.11+", "PyTorch 2.x", "NumPy", "ISAAC 风格的 W4A4 CIM 模型", "55 nm PDK（电路级复现需要授权）"],
            "steps": [
                "先复现 clean W4A4 baseline，确认 WRN-28-10 在 CIFAR-100 上接近 78.4%",
                "用行为级模型实现 T/k 两个旋钮，扫描 20/40 mV × skip-4/5/6",
                "加入 SA 扰动与前缀复用误差注入，跑 teacher-student 蒸馏",
                "复现 SAR 比较次数、ADC 能耗预算和跨模型结果",
                "最后再做 55 nm 电路级 SA 检测器仿真，核对 732 个 guard event",
            ],
            "minimal_experiment": "只做行为级：在 1000 张 CIFAR-100 验证图上模拟相邻输出相似性，比较 native SAR 与 skip-6 的比较次数和 Top-1 变化。",
            "risks": ["PDK 授权限制", "ISAAC 模型版本差异", "SA 扰动提取样本不足", "训练随机种子导致 ±0.3% 波动"],
            "deliverables": ["复现实验表", "T/k 扫描曲线", "误差注入消融", "复现风险清单"],
        },
        "code_scaffold": '''"""Behavioral scaffold: similarity-guided SAR prefix reuse.

This is a teaching model for the paper's control idea, not a 55-nm circuit.
It shows the (T, k) trade-off: a larger window / more skipped bits saves
comparisons, but can reuse the wrong prefix near a quantization boundary.
"""
from dataclasses import dataclass

BITS = 8
VREF = 1.0


def sar_code(vin: float, bits: int = BITS, vref: float = VREF) -> int:
    """Ideal SAR conversion, MSB first."""
    code, trial, step = 0, vref / 2, vref / 4
    for i in range(bits):
        if vin >= trial:
            code |= 1 << (bits - 1 - i)
            trial += step
        else:
            trial -= step
        step /= 2
    return code


def code_to_voltage(code: int, bits: int = BITS, vref: float = VREF) -> float:
    return code / (2 ** bits) * vref


@dataclass
class SkipResult:
    code: int
    comparisons: int
    reused_prefix: bool
    error_lsb: float


def skip_sar(prev_vin: float, curr_vin: float, k: int = 4,
             threshold: float = 0.04, bits: int = BITS,
             vref: float = VREF) -> SkipResult:
    """Reuse the previous k-bit prefix when |curr - prev| < threshold."""
    prev_code = sar_code(prev_vin, bits, vref)
    if abs(curr_vin - prev_vin) >= threshold or k <= 0:
        curr_code = sar_code(curr_vin, bits, vref)
        return SkipResult(curr_code, bits, False, 0.0)

    shift = bits - k
    code = (prev_code >> shift) << shift
    lo = code_to_voltage(code, bits, vref)
    hi = code_to_voltage(code + (1 << shift), bits, vref)

    # Continue SAR only inside the reused coarse interval.
    trial, step = (lo + hi) / 2, (hi - lo) / 4
    for i in range(shift):
        if curr_vin >= trial:
            code |= 1 << (shift - 1 - i)
            trial += step
        else:
            trial -= step
        step /= 2

    ideal = sar_code(curr_vin, bits, vref)
    return SkipResult(code, shift, True, code - ideal)


if __name__ == "__main__":
    import random

    random.seed(7)
    full = skipped = errors = 0
    total_err = 0.0
    for _ in range(2000):
        prev = random.random()
        curr = min(1.0, max(0.0, prev + random.gauss(0, 0.02)))
        r = skip_sar(prev, curr, k=4, threshold=0.04)
        full += BITS
        skipped += r.comparisons
        if r.error_lsb:
            errors += 1
            total_err += abs(r.error_lsb)
    print(f"comparisons: {skipped}/{full} = {skipped/full:.1%}")
    print(f"prefix reuse events with code error: {errors}/2000")
    print(f"mean absolute error when wrong: {total_err/max(errors,1):.2f} LSB")
''',
        "slides": [
            "标题：当 ADC 学会“抄近路”——相似性引导的 SAR 跳比较",
            "问题：ADC 占 CIM tile 功耗 58%、面积 31%",
            "观察：相邻模拟输出存在数值局部性",
            "方法：双侧 SA 相似窗口 + NEAR/FAR 控制",
            "关键公式：Wk = VFS / 2^k",
            "训练：teacher-student + 硬件误差注入",
            "结果：SAR 比较 -48.19%，Top-1 77.3% vs 78.4%",
            "边界：原理图仿真，PVT 与时钟噪声未建模",
            "启示：模拟局部性可以变成数字控制信号",
        ],
        "quiz": [
            {"q": "为什么“电压相近”不保证可以直接复用数字前缀？", "a": "因为两个相近电压可能落在不同的量化区间边界两侧，复用前缀会把第二次转换限制在错误的搜索范围。"},
            {"q": "k 增大时，比较次数和精度分别怎么变？", "a": "k 越大，跳过的 SAR 比较越多，节省越多；但前缀复用更激进，编码错误风险更高，需要训练补偿。"},
            {"q": "这篇工作的验证边界是什么？", "a": "55 nm CMOS 原理图仿真 + 系统级模型评估，没有流片；PVT 和时钟相位噪声未纳入模型。"},
            {"q": "teacher-student 中 teacher 的作用是什么？", "a": "冻结的干净 W4A4 teacher 提供参考分布，student 在带 SA 扰动和前缀复用误差的路径上学习，靠近 teacher 的输出。"},
        ],
        "reviewer_questions": [
            "只有 TT 角、25 个扰动样本，如何证明检测器在 PVT 变化下仍然可靠？",
            "77.3% 对应哪个具体工作点？与 78.4% 基线是否在同一测试子集上比较？",
            "SAR 比较次数减少 48.19%，但检测器本身的能量和面积开销是否已完整计入系统 FoM？",
        ],
        "course_case": "模拟集成电路设计 / 存算一体专题：用这篇论文讲“模拟非理想性如何进入训练闭环”，10 分钟课程可拆成 ADC 瓶颈、相似性检测、前缀复用、误差训练四段。",
    },
    {
        "id": "2609.07059v3",
        "title": "NOVA-CIM: Noise- and Correlation-Tolerant Stochastic Interfaces for Analog Compute-in-Memory",
        "authors": ["NOVA-CIM authors"],
        "year": 2026,
        "venue": "arXiv preprint",
        "source": "arxiv",
        "url": "http://arxiv.org/abs/2609.07059v3",
        "pdf_url": "https://arxiv.org/pdf/2609.07059v3",
        "demo": False,
        "full_text": False,
        "extraction_level": "摘要级抽取",
        "tldr": "用随机参考的 1-bit 感知替代多比特 ADC 读出，再通过轻量计数重建结果；把列电流变成比较概率，让零均值动态读噪声在多次采样中被平均掉。ViT-Base 在读噪声下保持 84.48% Top-1，接近 BF16 的 84.51%。",
        "problem": "模拟 CIM 依赖 ADC 直接量化带噪声的列电流，推理精度对读噪声、有效行缩放和 ADC 精度高度敏感。",
        "method": "随机参考 1-bit 感知 + 轻量计数重建；把列电流转换为比较概率，在概率域对随机样本做时间平均，降低对高分辨率 ADC 的依赖；给出动态读噪声与输入比特流相关性的统一鲁棒性分析。",
        "novelty": ["用随机参考 1-bit 感知替代多比特 ADC 读出", "概率域时间平均抑制零均值动态读噪声", "分析空间输入比特流相关性对瞬时电流方差的影响"],
        "assumptions": ["读噪声主要为零均值动态噪声", "随机样本之间可以独立/可交换", "计数重建的轻量数字逻辑开销可接受"],
        "metrics": {
            "工艺/验证": "MAC 级实验 + ViT-Base 端到端评估（摘要未给工艺）",
            "ADC 方案": "随机参考 1-bit 感知 + 计数重建",
            "Top-1": "84.48%（读噪声下） vs BF16 84.51%",
            "MAC 偏差": "SNG 复用时接近 0",
            "误差趋势": "RMSE 与 SCC 随预测增长",
        },
        "results": [
            {"name": "ViT-Base Top-1", "value": "84.48%", "baseline": "84.51% (BF16)", "condition": "读噪声下，端到端评估"},
            {"name": "MAC bias", "value": "接近 0", "baseline": "—", "condition": "SNG 复用场景"},
            {"name": "RMSE / SCC", "value": "按预测增长", "baseline": "—", "condition": "随机数发生器复用"},
        ],
        "baselines": ["BF16 84.51%", "多比特 ADC 读出（传统方案）"],
        "limitations": ["摘要未报告工艺、硅验证、能量/面积绝对值", "随机采样的吞吐和延迟开销未在摘要中量化", "鲁棒性结论依赖噪声模型假设"],
        "code_url": None,
        "evidence": [
            {"field": "problem", "label": "噪声敏感", "section": "Abstract", "page": 1,
             "quote": "its reliance on ADC-based readout, which directly quantizes noisy column currents, makes inference accuracy highly sensitive to analog read noise, active-row scaling, and ADC precision."},
            {"field": "method", "label": "随机 1-bit 读出", "section": "Abstract", "page": 1,
             "quote": "replacing multi-bit ADC readout with random-reference 1-bit sensing and reconstructing results through lightweight counting."},
            {"field": "method", "label": "概率域平均", "section": "Abstract", "page": 1,
             "quote": "this probability-domain readout averages zero-mean dynamic read noise over stochastic samples while reducing dependence on high-resolution ADCs."},
            {"field": "result", "label": "ViT-Base 结果", "section": "Abstract", "page": 1,
             "quote": "under read noise, Top-1 accuracy remains 84.48% near the 84.51% bfloat16 (BF16) baseline"},
        ],
        "sections": [
            {"title": "Abstract", "page": 1, "text": "Analog compute-in-memory (CIM) enables energy-efficient model acceleration, but its reliance on ADC-based readout, which directly quantizes noisy column currents, makes inference accuracy highly sensitive to analog read noise, active-row scaling, and ADC precision. In this paper, we present NOVA-CIM, a noise- and correlation-tolerant stochastic interface for analog CIM by replacing multi-bit ADC readout with random-reference 1-bit sensing and reconstructing results through lightweight counting. By converting column currents into comparison probabilities, this probability-domain readout averages zero-mean dynamic read noise over stochastic samples while reducing dependence on high-resolution ADCs. We provide a unified robustness analysis showing that dynamic read noise is suppressed through temporal averaging and that spatial input-bitstream correlation increases instantaneous current variance rather than introducing first-order MAC bias. MAC-level experiments and end-to-end evaluation on ViT-Base validate the analysis: under read noise, Top-1 accuracy remains 84.48% near the 84.51% bfloat16 (BF16) baseline; under stochastic number generator (SNG) reuse, MAC bias stays near zero while root-mean-square error (RMSE) and stochastic cross-correlation (SCC) grow as predicted."},
        ],
        "figures": [
            {"title": "随机参考 1-bit 感知", "page": 1, "what": "把模拟列电流与随机参考比较，得到 0/1 流，再用计数估计原值。", "how_to_read": "关注“比较概率”如何编码电流幅度，以及为什么多次采样能平均零均值噪声。"},
        ],
        "translation": [
            {"en": "ADC-based readout directly quantizes noisy column currents, making inference accuracy highly sensitive to analog read noise.", "zh": "基于 ADC 的读出会直接量化带噪声的列电流，使推理精度对模拟读噪声高度敏感。"},
            {"en": "This probability-domain readout averages zero-mean dynamic read noise over stochastic samples.", "zh": "这种概率域读出通过随机样本对零均值动态读噪声做平均。"},
            {"en": "Under read noise, Top-1 accuracy remains 84.48% near the 84.51% BF16 baseline.", "zh": "在读噪声下，Top-1 准确率保持在 84.48%，接近 BF16 基线的 84.51%。"},
        ],
        "repo_plan": {
            "environment": ["Python 3.11+", "PyTorch", "ViT-Base 预训练权重", "自定义随机参考读出模拟器"],
            "steps": ["复现 BF16 ViT-Base baseline", "实现随机参考 1-bit 感知与计数重建", "注入零均值动态读噪声", "扫描采样数、SNG 复用与相关性", "对比 Top-1、RMSE、SCC"],
            "minimal_experiment": "在单个 ViT block 的 MAC 输出上模拟 1-bit 随机读出，比较 16/64/256 次采样的重建误差。",
            "risks": ["摘要未给工艺细节", "随机数质量影响结果", "端到端吞吐未报告"],
            "deliverables": ["噪声模型", "采样数扫描", "精度-开销曲线"],
        },
        "code_scaffold": '''"""Scaffold: stochastic 1-bit readout by counting.

A random reference r is compared with the analog current x. The fraction of
ones estimates P(x > r), from which x can be reconstructed if r's distribution
is known. This is a teaching model, not the paper's silicon implementation.
"""
import random


def one_bit_sample(x: float, r: float) -> int:
    return int(x > r)


def reconstruct(samples: list[int], r_low: float = 0.0, r_high: float = 1.0) -> float:
    p = sum(samples) / len(samples)
    return r_low + p * (r_high - r_low)


if __name__ == "__main__":
    random.seed(0)
    x = 0.37
    for n in (16, 64, 256, 1024):
        bits = [one_bit_sample(x + random.gauss(0, 0.02), random.random()) for _ in range(n)]
        print(n, round(reconstruct(bits), 4))
''',
        "slides": [
            "问题：ADC 直接量化噪声列电流，精度受读噪声牵制",
            "思路：用随机参考 1-bit 感知替代多比特 ADC",
            "重建：比较概率 → 轻量计数",
            "为什么有效：零均值噪声在时间平均中被抑制",
            "结果：ViT-Base 84.48% vs BF16 84.51%",
            "边界：摘要级证据，缺少硅验证与绝对开销",
        ],
        "quiz": [
            {"q": "随机参考 1-bit 感知如何编码模拟电流的大小？", "a": "通过与随机参考比较得到 0/1 流，比较为 1 的概率随电流增大而增大，计数即可估计概率。"},
            {"q": "为什么时间平均能抑制读噪声？", "a": "零均值动态噪声在多次独立采样中相互抵消，平均后的方差下降。"},
        ],
        "reviewer_questions": [
            "随机参考的失配和相关性会不会引入系统性偏差？",
            "达到相同精度需要多少次 1-bit 采样？吞吐和能量代价是多少？",
            "摘要没有工艺与硅验证，如何证明接口在真实阵列上可扩展？",
        ],
        "course_case": "混合信号集成电路：用随机参考读出讲“用统计换精度”，适合作为 ADC 章节的拓展案例。",
    },
    {
        "id": "2608.11116v1",
        "title": "You Only Charge Once 2.0: An End-to-End Analog Computing-in-Memory Architecture with Reconfigurable Switched Capacitors",
        "authors": ["Charge-CIM authors"],
        "year": 2026,
        "venue": "arXiv preprint",
        "source": "arxiv",
        "url": "http://arxiv.org/abs/2608.11116v1",
        "pdf_url": "https://arxiv.org/pdf/2608.11116v1",
        "demo": False,
        "full_text": False,
        "extraction_level": "摘要级抽取",
        "tldr": "针对模拟 CIM 的“ADC 墙”，用开关电容电荷再分配把输入转换、模拟 MAC、加权移位相加和读出量化统一到同一套电容阵列里，减少独立转换器和中间 ADC 调用。评估显示 ADC 能耗降低 91.7%，能效提升 2.7 倍。",
        "problem": "模拟 CIM 的 ADC 消耗大量能量和面积；位切片执行会反复调用 ADC，低分辨率读出和时间复用又带来精度或串行化代价。",
        "method": "用可重构开关电容作为统一的计算与转换基底：同一电容阵列完成输入转换、模拟 MAC、加权 shift-and-add 和读出量化；差分读出路径在量化时合并成对部分和。",
        "novelty": ["把计算与转换统一到同一套开关电容阵列", "差分读出合并部分和，减少独立 ADC 调用", "端到端数据流支持 CNN 到 Transformer"],
        "assumptions": ["电荷再分配精度满足目标分辨率", "电容匹配与寄生在可接受范围", "评估 setup 能代表目标负载"],
        "metrics": {
            "工艺/验证": "架构级评估（摘要未给工艺）",
            "ADC 方案": "开关电容电荷再分配 + 差分读出",
            "ADC 能耗": "降低 91.7%",
            "能效": "提升 2.7×",
            "吞吐": "提升 2.0×",
            "负载": "CNN 到 Transformer 的 DNN benchmark",
        },
        "results": [
            {"name": "ADC 能耗", "value": "-91.7%", "baseline": "对比方案", "condition": "论文评估 setup"},
            {"name": "能效", "value": "2.7×", "baseline": "SOTA charge-domain CIM", "condition": "论文评估 setup"},
            {"name": "吞吐", "value": "2.0×", "baseline": "SOTA charge-domain CIM", "condition": "论文评估 setup"},
        ],
        "baselines": ["SOTA charge-domain CIM accelerator", "传统 ADC + 位切片方案"],
        "limitations": ["摘要未给工艺与硅验证", "91.7% 是特定评估 setup 下的相对值", "电容匹配、寄生和校准细节未在摘要展开"],
        "code_url": None,
        "evidence": [
            {"field": "problem", "label": "ADC 墙", "section": "Abstract", "page": 1,
             "quote": "modern ACiM accelerators are often limited by the \"ADC wall\": analog-to-digital converters consume a large fraction of energy and area, while bit-sliced execution repeatedly invokes these converters."},
            {"field": "method", "label": "统一电容基底", "section": "Abstract", "page": 1,
             "quote": "Charge-CIM addresses this bottleneck by using switched-capacitor charge redistribution as a unified computing and conversion substrate."},
            {"field": "result", "label": "主要结果", "section": "Abstract", "page": 1,
             "quote": "experimental results show that Charge-CIM reduces ADC energy by 91.7% under our evaluation setup and improves energy efficiency by 2.7x and throughput by 2.0x compared to the state-of-the-art charge-domain CIM accelerator."},
        ],
        "sections": [
            {"title": "Abstract", "page": 1, "text": "Analog Computing-in-Memory (ACiM) accelerates deep neural networks by keeping weights inside memory arrays and executing dot products in the analog domain. However, modern ACiM accelerators are often limited by the \"ADC wall\": analog-to-digital converters consume a large fraction of energy and area, while bit-sliced execution repeatedly invokes these converters. Existing designs reduce this cost with low-resolution readout or time multiplexing, but they either lose output fidelity or introduce serialization overhead. Charge-CIM addresses this bottleneck by using switched-capacitor charge redistribution as a unified computing and conversion substrate. The same capacitor fabric performs input conversion, analog MAC, weighted shift-and-add, and readout quantization, reducing both standalone converter overhead and intermediate ADC invocations. A differential readout path further combines paired partial sums during ADC quantization, providing a highly compact and energy-efficient solution for array integration. With dataflow architecture support, we evaluated Charge-CIM on a suite of DNN benchmarks, from CNNs to Transformer models, and experimental results show that Charge-CIM reduces ADC energy by 91.7% under our evaluation setup and improves energy efficiency by 2.7x and throughput by 2.0x compared to the state-of-the-art charge-domain CIM accelerator."},
        ],
        "figures": [
            {"title": "统一电荷域数据流", "page": 1, "what": "同一电容阵列承担输入转换、MAC、移位相加与量化。", "how_to_read": "重点看“哪些中间步骤不再需要独立 ADC”。"},
        ],
        "translation": [
            {"en": "The same capacitor fabric performs input conversion, analog MAC, weighted shift-and-add, and readout quantization.", "zh": "同一套电容阵列完成输入转换、模拟乘累加、加权移位相加和读出量化。"},
            {"en": "Charge-CIM reduces ADC energy by 91.7% under our evaluation setup.", "zh": "在其评估设置下，Charge-CIM 将 ADC 能耗降低了 91.7%。"},
            {"en": "A differential readout path combines paired partial sums during ADC quantization.", "zh": "差分读出路径在 ADC 量化过程中合并成对的部分和。"},
        ],
        "repo_plan": {
            "environment": ["Python/NumPy 行为级模型", "电容失配与寄生模型", "CNN/Transformer 量化推理框架"],
            "steps": ["建立开关电容电荷再分配的行为模型", "复现输入转换/MAC/量化统一流程", "加入电容失配和寄生", "对比传统 ADC + 位切片的能耗与吞吐"],
            "minimal_experiment": "在一个 8×8 电容阵列上模拟一次电荷域 MAC 与读出，扫描电容失配标准差。",
            "risks": ["摘要未给工艺细节", "电容匹配模型决定结论", "相对提升依赖 baseline 选择"],
            "deliverables": ["行为级模型", "失配敏感性分析", "能耗/吞吐对比"],
        },
        "code_scaffold": '''"""Scaffold: charge-redistribution dot product.

Capacitors store input/weight charges and share them on a common node.
This illustrates the unified compute/convert idea, not a circuit-accurate model.
"""
import numpy as np


def charge_mac(x: np.ndarray, w: np.ndarray, c: np.ndarray) -> float:
    q = np.sum(c * x * w)
    return float(q / np.sum(c))


if __name__ == "__main__":
    rng = np.random.default_rng(3)
    x = rng.uniform(0, 1, 16)
    w = rng.uniform(-1, 1, 16)
    c = np.ones(16)
    print("MAC =", charge_mac(x, w, c))
''',
        "slides": [
            "问题：ADC 墙与位切片的重复转换",
            "思路：电荷再分配统一计算与转换",
            "数据流：输入转换 → MAC → 移位相加 → 量化",
            "结果：ADC 能耗 -91.7%，能效 2.7×，吞吐 2.0×",
            "边界：评估 setup 与电容匹配",
        ],
        "quiz": [
            {"q": "为什么“统一计算与转换”能减少 ADC 调用？", "a": "因为部分转换和累加在电荷域完成，不再需要每个中间结果都经过独立 ADC。"},
            {"q": "电容失配会带来什么影响？", "a": "会破坏电荷分配的精度，导致 MAC 结果和量化结果出现偏差，需要校准或冗余设计。"},
        ],
        "reviewer_questions": [
            "91.7% 的 ADC 能耗下降相对于哪个 baseline、在什么 workload 下成立？",
            "电容失配、寄生和温度变化如何影响有效分辨率？",
            "统一电容阵列是否限制了可重构性和支持的位宽范围？",
        ],
        "course_case": "模拟集成电路设计：用“电荷再分配”讲开关电容电路的計算与转换一体化。",
    },
    {
        "id": "2606.08947v1",
        "title": "NeuDW-CIM: a 65-nm 0.8-pJ/Sop Reconfigurable Neuromorphic Compute-in-Memory Macro with Nonlinear Dendrites and K-Winners",
        "authors": ["Junyi Yang", "Yahan Yang", "Shuai Dong", "Biyan Zhou", "Ye Ke", "Zhengnan Fu", "Xin Si", "An Guo", "Peng Zhou", "Arindam Basu"],
        "year": 2026,
        "venue": "arXiv preprint",
        "source": "arxiv",
        "url": "http://arxiv.org/abs/2606.08947v1",
        "pdf_url": "https://arxiv.org/pdf/2606.08947v1",
        "demo": False,
        "full_text": True,
        "extraction_level": "全文节选 + 结构化抽取",
        "tldr": "65 nm 硅验证的神经形态 CIM macro：用 twin 9T 单元支持三值输入/权重，用可重构非线性 IMA 支持 NLD 模式，用 Top-K 早停降低 LIF 延迟。实测能效 0.8 pJ/SOP，N-MNIST 97.2%，DVS Gesture 95.5%。",
        "problem": "传统模拟 CIM + 数字 LIF 面临 ADC 开销、串行膜电位更新吞吐低、事件传感器三值输入映射效率低、低精度下 SNN 精度受限。",
        "method": "twin 9T bitcell + multi-VDD 支持三值/多比特权重；可重构非线性 IMA 实现 NLD；KWN 模式用 ramp ADC 做 top-K 选择与早停，只更新胜者神经元的膜电位；NLQ + SNL 提升精度。",
        "novelty": [
            "twin 9T + multi-VDD 在紧凑面积内支持三值输入/权重与多比特权重",
            "可重构非线性 IMA 同时服务 NLD 与 KWN 两种模式",
            "Top-K 早停同时降低 IMA 转换延迟与数字 LIF 更新延迟",
        ],
        "assumptions": ["SNN 事件稀疏性可利用", "Top-K 胜者足以承载主要信息", "测试数据集能代表边缘事件视觉负载"],
        "metrics": {
            "工艺/验证": "65 nm CMOS，硅验证",
            "ADC 方案": "可重构非线性 IMA + ramp ADC",
            "能效": "0.8 pJ/SOP（1.6× 提升）",
            "N-MNIST": "97.2%",
            "DVS Gesture": "95.5%",
            "IMA 延迟": "降低 30%",
            "数字 LIF 延迟": "降低 10×",
            "IMA 面积开销": "12%",
        },
        "results": [
            {"name": "能效", "value": "0.8 pJ/SOP", "baseline": "1.6× 提升", "condition": "KWN 模式，稀疏更新"},
            {"name": "N-MNIST", "value": "97.2%", "baseline": "—", "condition": "NLD 模式，实测"},
            {"name": "DVS Gesture", "value": "95.5%", "baseline": "—", "condition": "NLD 模式，实测"},
            {"name": "IMA 转换延迟", "value": "-30%", "baseline": "无早停", "condition": "KWN 早停"},
            {"name": "数字 LIF 延迟", "value": "-10×", "baseline": "串行更新", "condition": "Top-K 胜者更新"},
        ],
        "baselines": ["传统 ADC + 数字 LIF", "全神经元膜电位更新", "二值输入 CIM"],
        "limitations": ["面向 SNN/事件视觉，通用 DNN 适用性未验证", "Top-K 的 K 值与精度/延迟权衡需要调参", "仅在 N-MNIST 与 DVS Gesture 上给出实测精度"],
        "code_url": None,
        "evidence": [
            {"field": "problem", "label": "ADC 与 LIF 瓶颈", "section": "I. Introduction", "page": 1,
             "quote": "First, it requires an analog-to-digital Converter (ADC) after the analog Multiply-Accumulate (MAC), which increases power and area overheads."},
            {"field": "method", "label": "非线性 IMA", "section": "I. Introduction", "page": 1,
             "quote": "NL reconfigurable in-memory ADC (IMA) with small area overhead (12%): implementing different NL activations with measured average error <1 LSB"},
            {"field": "method", "label": "KWN 早停", "section": "I. Introduction", "page": 1,
             "quote": "KWN with early stop: reducing IMA latency by 30% and digital LIF latency by 10×, boosting energy efficiency by 1.6×"},
            {"field": "result", "label": "实测能效", "section": "Abstract", "page": 1,
             "quote": "NeuDW-CIM achieves a measured energy efficiency (EE) of 0.8 pJ/SOP (1.6× improvement)."},
        ],
        "sections": [
            {"title": "Abstract", "page": 1, "text": "This work presents NeuDW-CIM, a highly efficient neuromorphic Compute-in-Memory (CIM) macro for Spiking Neural Networks (SNNs) implemented in 65 nm CMOS. The design introduces a custom twin 9T bit-cell for ternary inputs/weights and a reconfigurable non-linear In-Memory ADC (IMA). The macro supports two specialized modes: 1) Nonlinear Dendrite (NLD) mode, which utilizes reconfigurable IMA to emulate biological dendritic functions, achieving measured accuracies of 97.2% on N-MNIST and 95.5% on DVS Gesture; and 2) Top-K Winner (KWN) mode, featuring an early-stopping mechanism that reduces IMA conversion latency by 30% and digital LIF latency by 10×. Benefiting from the sparse update in KWN mode, NeuDW-CIM achieves a measured energy efficiency (EE) of 0.8 pJ/SOP (1.6× improvement)."},
            {"title": "I. Introduction — 四个架构挑战", "page": 1, "text": "Digital LIF following analog CIM provides flexibility, but faces several architectural challenges. First, it requires an analog-to-digital Converter (ADC) after the analog Multiply-Accumulate (MAC), which increases power and area overheads. Moreover, it suffers from poor throughput due to serial membrane potential (Vmem) update of the digital LIF. Third, the output of event sensors is normally ternary with ON/OFF events, which require more channels when mapped to conventional binary input CIM. Lastly, the accuracy of SNN on a limited-precision analog CIM has been limited."},
            {"title": "I. Introduction — 四个设计特征", "page": 1, "text": "To overcome these issues, a Neuromorphic CIM with Dendrites and Winners (NeuDW-CIM) supporting top-K winner (KWN) and Non-linear (NL) dendrites (NLD) modes is proposed with the following features: 1) NL reconfigurable in-memory ADC (IMA) with small area overhead (12%): implementing different NL activations with measured average error <1 LSB; 2) KWN with early stop: reducing IMA latency by 30% and digital LIF latency by 10×, boosting energy efficiency by 1.6×; 3) custom twin 9T bit-cell with multi-VDD: supporting ternary inputs/weights with enhanced weight precision and minimal overhead; 4) accuracy optimization: integrating NL quantization (NLQ) and a sensitive neuron list (SNL) to enhance inference accuracy in KWN mode."},
            {"title": "II. Architecture — Twin 9T 与 Top-K 数据流", "page": 2, "text": "Fig. 2 depicts the architecture of the NeuDW-CIM with two modes. It consists of 256×128 and 46×128 SRAM arrays for MAC and NL-IMA, respectively. The digital controller configures the ramp ADC for dual task of NL quantization and selection of top-K largest MAC results (K≪128) in KWN mode. Only these indices are used to update Vmem for the digital LIF, and the ramp is stopped early after the first K conversions, resulting in saving ADC/LIF latency and energy. In NLD mode, various biological dendritic functions with NL activations are integrated into the network to enhance accuracy; these NL activations are implemented by the NL-IMA, where outputs from the ramp-IMA are digitized via a ripple counter."},
        ],
        "figures": [
            {"title": "图 1 — 传统方案 vs NeuDW-CIM", "page": 1, "what": "左边是 ADC + 串行数字 LIF 的四个挑战，右边是对应的四个设计特征。", "how_to_read": "把四个挑战与四个特征一一对应，理解每个电路模块为什么存在。"},
            {"title": "图 2 — 双模式架构", "page": 2, "what": "256×128 MAC 阵列 + 46×128 NL-IMA，同一套 IMA 在 NLD/KWN 模式下复用。", "how_to_read": "重点看 ramp ADC 如何同时承担非线性和 top-K 选择。"},
            {"title": "公式 (1) — Top-K 膜电位更新", "page": 2, "what": "只有 top-K 胜者神经元更新 Vmem，其余保持不变。", "how_to_read": "这是延迟和能耗下降的核心：把“全量更新”变成“稀疏更新”。"},
        ],
        "translation": [
            {"en": "The digital controller configures the ramp ADC for dual task of NL quantization and selection of top-K largest MAC results.", "zh": "数字控制器把 ramp ADC 配置为双重任务：非线性量化和选择最大的 Top-K 个 MAC 结果。"},
            {"en": "Only these indices are used to update Vmem for the digital LIF, and the ramp is stopped early after the first K conversions.", "zh": "只有这些索引对应的神经元会更新数字 LIF 的膜电位，ramp 在前 K 次转换后就提前停止。"},
            {"en": "NeuDW-CIM achieves a measured energy efficiency of 0.8 pJ/SOP.", "zh": "NeuDW-CIM 实现了 0.8 pJ/SOP 的实测能效。"},
        ],
        "repo_plan": {
            "environment": ["Python 3.11+", "PyTorch + snnTorch/Lava", "DVS 事件数据加载", "65 nm PDK（电路级复现需要授权）"],
            "steps": ["先复现 N-MNIST/DVS Gesture 的 SNN 软件基线", "实现 top-K 早停的行为级模型", "模拟非线性 IMA 的量化误差", "扫描 K 值与精度/延迟", "最后做 65 nm 电路级 macro 复现"],
            "minimal_experiment": "在 N-MNIST 上比较全量 LIF 更新与 top-K 更新（K=4/8/16）的精度和更新次数。",
            "risks": ["PDK 授权限制", "事件数据预处理差异", "top-K 选择的硬件非理想性", "SNN 训练随机性"],
            "deliverables": ["软件基线", "top-K 扫描", "延迟/能效模型", "复现风险清单"],
        },
        "code_scaffold": '''"""Behavioral scaffold: top-K LIF update.

Only the K neurons with the largest MAC results update their membrane
potential. This is a teaching model, not the paper's 65-nm macro.
"""
import numpy as np


def topk_lif_update(vmem: np.ndarray, mac: np.ndarray, k: int,
                    beta: float = 0.9, threshold: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    idx = np.argpartition(mac, -k)[-k:]
    new_vmem = vmem.copy()
    new_vmem[idx] = mac[idx] + beta * vmem[idx]
    spikes = (new_vmem >= threshold).astype(int)
    new_vmem[spikes.astype(bool)] = 0.0
    return new_vmem, spikes


if __name__ == "__main__":
    rng = np.random.default_rng(1)
    vmem = rng.uniform(0, 0.5, 128)
    mac = rng.normal(0, 0.3, 128)
    for k in (4, 8, 16):
        v, s = topk_lif_update(vmem, mac, k)
        print(f"K={k:2d} updated={k:2d} spikes={s.sum()}")
''',
        "slides": [
            "问题：ADC 开销、LIF 串行更新、三值事件输入、低精度 SNN",
            "方案：twin 9T + 可重构非线性 IMA + KWN 早停",
            "关键机制：只有 top-K 胜者更新膜电位",
            "结果：0.8 pJ/SOP，N-MNIST 97.2%，DVS 95.5%",
            "边界：SNN/事件视觉专用，K 值需调参",
            "启示：稀疏性不仅能省计算，也能省转换和状态更新",
        ],
        "quiz": [
            {"q": "KWN 模式为什么能同时降低 IMA 和 LIF 的延迟？", "a": "ramp ADC 只需完成前 K 次转换就能选出胜者，数字 LIF 也只更新这 K 个神经元，避免了全量转换和全量膜电位更新。"},
            {"q": "NLD 模式解决什么问题？", "a": "用可重构非线性 IMA 模拟生物树突的非线性激活，在不显著增加参数的情况下提升 SNN 精度。"},
            {"q": "这篇工作的实测边界是什么？", "a": "65 nm 硅验证，但主要在 N-MNIST 和 DVS Gesture 两个事件视觉任务上评估。"},
        ],
        "reviewer_questions": [
            "K 值如何选择？在不同稀疏度/任务下是否稳定？",
            "top-K 选择本身的开销是否已计入 0.8 pJ/SOP？",
            "NLD 的非线性激活在更大规模 DVS 数据集上是否仍然有效？",
        ],
        "course_case": "数字集成电路 / 神经形态计算：用 top-K 早停讲“稀疏性如何贯穿算法、架构与电路”。",
    },
    {
        "id": "2605.30814v1",
        "title": "A Reconfigurable Computing In-Memory Macro with Charge-sharing-based Weighted Accumulator",
        "authors": ["Reconfigurable CIM authors"],
        "year": 2026,
        "venue": "arXiv preprint",
        "source": "arxiv",
        "url": "http://arxiv.org/abs/2605.30814v1",
        "pdf_url": "https://arxiv.org/pdf/2605.30814v1",
        "demo": False,
        "full_text": False,
        "extraction_level": "摘要级抽取",
        "tldr": "面向 SRAM 模拟 CIM 的 ADC 开销、多比特输入延迟和读位线摆幅限制，提出 256×128 可重构阵列、仅占 3% 面积的 IMADC、电荷共享加权累加器 BSCHA 和 dual-8T 单元。摘要报告 IMADC 面积改善 9×、延迟改善 1.9×/6.6×、线性度改善 7×。",
        "problem": "SRAM 模拟 CIM 面临 ADC 开销大、多比特输入延迟高、读位线电压摆幅有限三个问题。",
        "method": "256×128 可重构阵列支持 1–7b 输入、2–4b 权重、1–7b 输出；IMADC 只占 3% 面积；BSCHA 用电荷共享做加权累加；dual-8T 单元通过解耦读路径和读字线欠驱动 cascode 提升线性度与位线摆幅。",
        "novelty": ["低面积 IMADC 与电荷共享加权累加器结合", "dual-8T 单元支持三值权重并改善线性度", "覆盖 1–7b 输入/输出的可重构位宽"],
        "assumptions": ["电荷共享累加的精度满足目标位宽", "BSCHA 的时序开销可被并行度掩盖", "相对改善可迁移到不同工艺"],
        "metrics": {
            "工艺/验证": "摘要未给工艺/测试方式",
            "阵列": "256×128",
            "位宽": "输入 1–7b，权重 2–4b，输出 1–7b",
            "IMADC 面积": "3% 开销，9× 改善",
            "BSCHA 延迟": "1.9× / 6.6× 改善（对比 PWM / 位切片）",
            "线性度": "7× 改善",
            "读位线摆幅": "3.5× 提升",
        },
        "results": [
            {"name": "IMADC 面积", "value": "3%", "baseline": "previous IMADC", "condition": "摘要报告"},
            {"name": "IMADC 面积改善", "value": "9×", "baseline": "previous IMADC", "condition": "摘要报告"},
            {"name": "BSCHA 延迟改善", "value": "1.9× / 6.6×", "baseline": "PWM / bit-slicing", "condition": "摘要报告"},
            {"name": "线性度改善", "value": "7×", "baseline": "传统单元", "condition": "摘要报告"},
            {"name": "读位线摆幅", "value": "3.5×", "baseline": "传统方案", "condition": "摘要报告"},
        ],
        "baselines": ["传统 IMADC", "PWM 模式", "位切片模式"],
        "limitations": ["摘要未给工艺、硅验证和绝对值", "相对改善依赖 baseline 与测试条件", "电荷共享的失配/寄生影响未展开"],
        "code_url": None,
        "evidence": [
            {"field": "problem", "label": "三个挑战", "section": "Abstract", "page": 1,
             "quote": "SRAM-based analog computing-in-memory demonstrates outstanding efficiency. However, it faces three critical challenges: significant ADC overhead, high latency for multi-bit inputs, and limited read bitline voltage."},
            {"field": "method", "label": "可重构阵列", "section": "Abstract", "page": 1,
             "quote": "this work proposes a multi-bit highly reconfigurable 256x128 in-memory computing array supporting 1-7b input, 2-4b weight, and 1-7b output."},
            {"field": "result", "label": "关键改善", "section": "Abstract", "page": 1,
             "quote": "The IMADC occupies only 3% area overhead, achieving a 9x improvement compared to previous IMADC"},
        ],
        "sections": [
            {"title": "Abstract", "page": 1, "text": "SRAM-based analog computing-in-memory demonstrates outstanding efficiency. However, it faces three critical challenges: significant ADC overhead, high latency for multi-bit inputs, and limited read bitline voltage. To address these issues, this work proposes a multi-bit highly reconfigurable 256x128 in-memory computing array supporting 1-7b input, 2-4b weight, and 1-7b output. Three key innovations are introduced: 1) The IMADC occupies only 3% area overhead, achieving a 9x improvement compared to previous IMADC; 2) The BSCHA reduces latency by 1.9x and 6.6x compared to traditional pulse-width modulation (PWM) and bit-slicing modes, respectively; 3) A dual-8T bitcell enabling ternary weight storage through a decoupled read path, integrated with a read wordline under-driven cascode technique, improves linearity of unit discharge current by 7x and increases the usable read bitline voltage by 3.5x."},
        ],
        "figures": [
            {"title": "IMADC + BSCHA 数据流", "page": 1, "what": "低面积 IMADC 与电荷共享加权累加器配合，支持多比特输入/输出。", "how_to_read": "关注位宽重构如何影响延迟和面积。"},
        ],
        "translation": [
            {"en": "The IMADC occupies only 3% area overhead, achieving a 9x improvement compared to previous IMADC.", "zh": "该 IMADC 仅占 3% 的面积开销，相比之前的 IMADC 实现了 9 倍改善。"},
            {"en": "The BSCHA reduces latency by 1.9x and 6.6x compared to traditional PWM and bit-slicing modes.", "zh": "与传统 PWM 和位切片模式相比，BSCHA 分别将延迟降低了 1.9 倍和 6.6 倍。"},
            {"en": "A dual-8T bitcell improves linearity of unit discharge current by 7x.", "zh": "dual-8T 单元将单位放电电流的线性度改善了 7 倍。"},
        ],
        "repo_plan": {
            "environment": ["Python/NumPy 行为级模型", "SRAM CIM 阵列模型", "ADC/电荷共享模型"],
            "steps": ["建立 256×128 阵列的行为级模型", "实现 IMADC 量化与 BSCHA 累加", "扫描 1–7b 输入/2–4b 权重", "对比面积、延迟、线性度"],
            "minimal_experiment": "在 8×8 子阵列上比较传统位切片与 BSCHA 的延迟和输出误差。",
            "risks": ["摘要未给工艺细节", "电荷共享失配模型不完整", "相对值依赖 baseline"],
            "deliverables": ["行为级模型", "位宽扫描", "面积/延迟估算"],
        },
        "code_scaffold": '''"""Scaffold: charge-sharing weighted accumulation.

This is a simple behavioral model of accumulating partial products with
shared charge, not a transistor-level implementation.
"""
import numpy as np


def bscha_accumulate(partials: np.ndarray, weights: np.ndarray) -> float:
    charge = np.sum(partials * weights)
    return float(charge / np.sum(weights))


if __name__ == "__main__":
    rng = np.random.default_rng(5)
    partials = rng.integers(0, 8, 32)
    weights = rng.integers(1, 5, 32)
    print("accumulated =", bscha_accumulate(partials, weights))
''',
        "slides": [
            "问题：ADC 开销、多比特延迟、位线摆幅",
            "方案：256×128 可重构阵列 + IMADC + BSCHA + dual-8T",
            "结果：面积 3%、9×；延迟 1.9×/6.6×；线性度 7×",
            "边界：摘要级相对指标",
        ],
        "quiz": [
            {"q": "BSCHA 主要解决什么问题？", "a": "用电荷共享做加权累加，降低多比特输入下的累加延迟和 ADC 调用开销。"},
            {"q": "dual-8T 单元的两个作用是什么？", "a": "支持三值权重存储，并通过解耦读路径与读字线欠驱动 cascode 改善线性度和位线摆幅。"},
        ],
        "reviewer_questions": [
            "3% 面积开销是否包含外围控制与校准电路？",
            "1.9×/6.6× 延迟改善对应什么输入位宽和阵列规模？",
            "电荷共享的电容失配如何影响 7b 输出的线性度？",
        ],
        "course_case": "数字集成电路：用可重构 SRAM CIM 讲“位宽、面积、延迟”的三方权衡。",
    },
]

_TASKS = {
    "2609.07059v3": "ViT-Base",
    "2608.11116v1": "CNN–Transformer DNN benchmarks",
    "2606.08947v1": "N-MNIST / DVS Gesture（SNN）",
    "2605.30814v1": "256×128 SRAM CIM macro（摘要未给任务）",
}
for _paper in PAPERS:
    _paper["curated"] = True
    if not _paper.get("abstract"):
        _paper["abstract"] = next(
            (
                section["text"]
                for section in _paper.get("sections", [])
                if section.get("title", "").lower().startswith("abstract")
            ),
            _paper.get("tldr", ""),
        )
    _paper["metrics"].setdefault("网络/任务", _TASKS.get(_paper["id"], "未提及"))

PAPERS_BY_ID = {paper["id"]: paper for paper in PAPERS}

DEMO_TOPIC = "面向存算一体的低功耗 ADC 设计"

GAPS = [
    {
        "id": "gap-pvt",
        "title": "相似性检测在 PVT 变化下的鲁棒性",
        "question": "当工艺角、电压和温度同时漂移时，SA 相似窗口的阈值如何保持可校准？",
        "why_gap": "P1 只在 TT/1.2 V/27 °C 下做原理图仿真，工艺角和时钟相位噪声明确未纳入模型。",
        "evidence": ["2609.24288v1 · II.C：Process variation and clock-phase noise are outside the present model."],
        "novelty": 4,
        "feasibility": 4,
        "impact": 5,
        "experiment": "在 5 个工艺角 × 3 个电压 × 3 个温度下扫描 SA 阈值，加入蒙特卡洛失配，评估 NEAR/FAR 误判率与精度回退。",
    },
    {
        "id": "gap-stochastic-throughput",
        "title": "随机 1-bit 读出的吞吐与能量边界",
        "question": "为了达到 8-bit 等效精度，随机采样需要多少次比较？其吞吐和能量是否仍优于 SAR？",
        "why_gap": "P2 证明了噪声鲁棒性和 ViT-Base 精度，但摘要未报告采样次数、吞吐和能量绝对值。",
        "evidence": ["2609.07059v3 · Abstract：摘要报告精度与 RMSE/SCC 趋势，未给硬件开销。"],
        "novelty": 4,
        "feasibility": 5,
        "impact": 4,
        "experiment": "建立采样数-精度-能量模型，比较 1-bit 随机读出与 8-bit SAR 在相同 ENOB 下的 pJ/conversion 和吞吐。",
    },
    {
        "id": "gap-charge-system",
        "title": "电荷域 CIM 在 Transformer 负载下的系统级能效",
        "question": "电荷再分配统一计算/转换在长序列 Transformer 上是否仍能保持 2.7× 能效优势？",
        "why_gap": "P3 的 91.7%/2.7×/2.0× 是在其评估 setup 下的相对值，摘要未给工艺与硅验证。",
        "evidence": ["2608.11116v1 · Abstract：reduces ADC energy by 91.7% under our evaluation setup。"],
        "novelty": 3,
        "feasibility": 4,
        "impact": 4,
        "experiment": "用开源 Transformer 推理框架，对比电荷域 CIM 与 SAR-based CIM 在 prefill/decode 两阶段的能耗和吞吐。",
    },
    {
        "id": "gap-snn-generalization",
        "title": "非线性树突 IMA 的跨数据集泛化",
        "question": "NLD 模式在 N-MNIST/DVS Gesture 之外的事件视觉任务上是否仍能提升精度？",
        "why_gap": "P4 的实测精度只覆盖两个数据集，K 值与非线性激活的泛化性未验证。",
        "evidence": ["2606.08947v1 · Abstract：N-MNIST 97.2%，DVS Gesture 95.5%。"],
        "novelty": 3,
        "feasibility": 5,
        "impact": 4,
        "experiment": "在 DVS-CIFAR10、N-Caltech101 上复现 NLD/KWN 两种模式，扫描 K 与非线性类型。",
    },
    {
        "id": "gap-imadc-tradeoff",
        "title": "IMADC 的面积-线性度-速度三角",
        "question": "把 IMADC 面积压到 3% 后，线性度和速度的边界在哪里？",
        "why_gap": "P5 摘要报告了 3% 面积、7× 线性度和 3.5× 摆幅改善，但没有给出工艺、硅验证和失配分析。",
        "evidence": ["2605.30814v1 · Abstract：IMADC occupies only 3% area overhead ... linearity by 7x。"],
        "novelty": 3,
        "feasibility": 4,
        "impact": 4,
        "experiment": "建立 IMADC 行为级模型，扫描电容失配、比较器噪声和转换速率，画出面积-ENOB-速度 Pareto 前沿。",
    },
]
