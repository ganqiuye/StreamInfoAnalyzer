# TS 帧分析工具 (streamInfoTool)

对 MPEG-TS 片源做逐帧分析，检测分辨率切换、I/P/B 帧类型切换、PTS 跳变、duration 变化（20ms↔40ms 场/帧编码切换）、码率突刺、损坏帧等异常，输出可交互 HTML 报告（时间轴可左右滑动、点击定位、异常帧明细）。

## 打开 GUI

**方式一（推荐）**：双击 `ts_frame_gui.pyw`（无控制台窗口）。

**方式二**：命令行运行
```
python ts_frame_gui.py
```

**方式三（无 Python 环境）**：使用已编译的 exe —— `dist\TS帧分析工具\TS帧分析工具.exe`，双击即可（编译方法见下文）。

> GUI 依赖 `customtkinter`、`tkinterdnd2`（拖拽），Python 3.8+ 可用：
> ```
> pip install customtkinter tkinterdnd2
> ```
> 未装拖拽库时 GUI 会提示改用"浏览"按钮，不影响使用。

## GUI 使用说明

1. **选择片源**：把 `.ts` 文件拖到虚线框内，或点击虚线框/片源行"浏览"按钮选择。
2. **输出目录**：默认与片源同目录，可修改（分析结果 HTML/CSV/JSON 都写在这里）。
3. **视频 PID**：
   - 已知 PID 直接填（如 `0x12d` 或十进制 `301`）；
   - 不确定时点 **"检测视频PID"**，工具会列出片源中的视频流（H.264/H.265），点击后自动填入第一个。
4. 勾选"完成后打开 HTML 报告"（默认勾选），点 **"开始分析"**。
5. 分析在后台线程执行，底部状态栏实时显示已解析帧数和耗时；完成后自动打开报告。

### 命令行用法（不需要界面时）

```
python ts_frame_analyzer.py <input.ts> [pid_hex] [outdir]
# 例: python ts_frame_analyzer.py D:\clip.ts 0x12d D:\code\streamInfoTool
```

输出三个文件：
- `pid_0xXXX_report.html` — 可视化报告（推荐浏览器打开）
- `pid_0xXXX_frames.csv` — 逐帧明细（Excel 可开）
- `pid_0xXXX_frames.json` — 逐帧完整数据

## 报告怎么读

- **时间轴总览**：每帧一个色块（黄=I、蓝=P、灰=B、橙=IDR），异常帧按类型覆盖高亮色；横向滚动条左右滑动，悬停显示帧详情，**点击色块跳转到明细行**。
- **异常帧明细**：所有被标记的帧，含显示序号、解码序号、PTS、类型、异常标签。
- **逐帧明细**：可按 I/P/B/切换帧/异常帧 过滤。

### 异常类型说明

| 标记 | 含义 |
|---|---|
| 分辨率切换 | SPS 中分辨率发生变化 |
| 场/帧模式切换 | PAFF 场编码↔帧编码切换（通常伴随 duration 20ms↔40ms） |
| duration变为XXms | 帧间隔（duration）变化，只标记变化前后两帧；大段相同 duration 不算异常 |
| PTS跳变/PTS回跳 | 显示序上帧间隔偏离局部 duration（丢帧/时间戳错误） |
| PTS间隔过小 | 帧间隔为局部 duration 的一半左右（常见于场编码切换过渡） |
| 码率突刺 | 单帧码量超过滑动窗口中位数 6 倍（常见于 IDR） |
| 数据异常 | PES/NAL 解析失败或数据不完整 |
| PMT编码变化/编码切换 | 同一 PID 中途更换视频编码格式（如 H.264→H.265）。ffmpeg 只按首个 PMT 建流，此时仅报首段编码且后续数据会被误判；报告会给出切换点包号，建议分包分析 |

### 中流一致性检查

分析开始前工具会全流扫描 PAT/PMT 并对视频 ES 做 NAL 特征嗅探（PMT 层 + ES 层双重验证）：

- **一致**：输出 `中流检查: 一致`，正常分析。
- **检出变化**：命令行在 stderr 打印 `!! PMT stream_type 变化 @ packet #N` / `!! ES 编码切换 @ packet #N: H.264 -> H.265`；GUI 完成后弹窗提示，状态栏显示"检测到中流编码变化"。
- 不带 PID 参数运行时，若能唯一确定视频 PID 会自动选择（含中途换编码的 PID）。

### 命令行用法（不需要界面时）

## 编译 exe（给没有 Python 环境的用户）

前置：安装 py38 及依赖后执行
```
pip install customtkinter tkinterdnd2 pyinstaller
python build_exe.py
```
产物在 `dist\TS帧分析工具\`，整个文件夹即绿色版，拷给任何 Windows 机器双击 exe 即可运行。
