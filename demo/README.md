# PaperPilot 产品演示视频

**成片：** [`out/paperpilot_demo.mp4`](out/paperpilot_demo.mp4)

- 时长：3 分 8 秒
- 分辨率：1600×900 / 25fps / H.264 + AAC 立体声
- 内容：检索 → 精读 → 翻译 → 对比 → 创新点 → 成果 → 研究空白 → 证据问答 → 研究库 → 批量 PDF 解析 → 选题与写作 → 评测
- 画面：自动操作 + 键盘/鼠标光标 + 中文旁白 + 底部字幕
- 音频：中文旁白 + 轻量配乐

![封面](out/cover.jpg)

## 重新录制

```bash
cd demo
python generate_demo_audio.py   # 生成旁白与时间轴
python mix_demo_audio.py        # 合成音频
python record_demo.py           # Playwright 自动操作并录制
```

依赖：

- Python 3.11+
- `playwright`（`python -m playwright install ffmpeg`）
- 已安装 Edge（Playwright 通过 `channel="msedge"` 启动）
- `ffmpeg` 在 PATH 中

## 文件

- `demo_script.json`：13 段旁白脚本
- `demo_timeline.json`：旁白实测时长与时间轴
- `audio/*.mp3`：分段旁白
- `audio/mix.m4a`：最终混音
- `out/raw/*.webm`：Playwright 原始录制
- `out/paperpilot_demo.mp4`：最终成片
- `record_demo.py`：自动操作与录制
