#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TS 帧分析 GUI — customtkinter 暗色界面 + 拖拽 + 后台线程分析"""
from __future__ import annotations
import json, sys, threading, time, traceback, os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, StringVar, BooleanVar

import customtkinter as ctk
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND = True
except ImportError:
    _DND = False

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ts_frame_analyzer as T

SETTINGS_PATH = Path.home() / ".tsframe-gui.json"
VERSION = "1.0"


def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(d: dict) -> None:
    try:
        SETTINGS_PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("TS 帧分析工具 v%s" % VERSION)
        self.geometry("700x620")
        self.minsize(620, 540)

        self._busy = False
        self._timer_job = None
        self._stage = "就绪"
        self._started = 0.0
        self._last_report = None
        saved = _load_settings()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 8))
        ctk.CTkLabel(header, text="TS 帧分析工具", font=ctk.CTkFont(size=24, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text="拖拽 TS 片源，逐帧分析并生成可视化 HTML 报告",
                     font=ctk.CTkFont(size=13), text_color=("gray40", "gray65")).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # 拖拽区
        self._drop_host = tk.Frame(self, bg="#1c1c1c", highlightthickness=2,
                                   highlightbackground="#4a4a4a", height=88)
        self._drop_host.grid(row=1, column=0, sticky="ew", padx=24, pady=8)
        self._drop_host.grid_propagate(False)
        self._drop_host.grid_columnconfigure(0, weight=1)
        self._drop_label = tk.Label(self._drop_host, text="将 .ts / .mts / .trp 文件拖放到此处\n或点击选择片源",
                                    bg="#1c1c1c", fg="#9a9a9a", font=("Segoe UI", 13), justify="center", cursor="hand2")
        self._drop_label.grid(row=0, column=0, pady=26)
        self._drop_host.bind("<Button-1>", lambda _e: self._browse_input())
        self._drop_label.bind("<Button-1>", lambda _e: self._browse_input())

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 8))
        body.grid_columnconfigure(1, weight=1)

        r = 0
        ctk.CTkLabel(body, text="片源", anchor="w").grid(row=r, column=0, sticky="w", pady=6)
        self._input_var = StringVar(value=saved.get("input", ""))
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.grid(row=r, column=1, sticky="ew", pady=6)
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(row, textvariable=self._input_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(row, text="浏览", width=72, command=self._browse_input).grid(row=0, column=1)

        r += 1
        ctk.CTkLabel(body, text="输出目录", anchor="w").grid(row=r, column=0, sticky="w", pady=6)
        self._outdir_var = StringVar(value=saved.get("outdir", ""))
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.grid(row=r, column=1, sticky="ew", pady=6)
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(row, textvariable=self._outdir_var,
                     placeholder_text="留空则与片源同目录").grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(row, text="浏览", width=72, command=self._browse_outdir).grid(row=0, column=1)

        r += 1
        opts = ctk.CTkFrame(body, fg_color="transparent")
        opts.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        opts.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(opts, text="视频 PID").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._pid_var = StringVar(value=saved.get("pid", "0x12d"))
        self._pid_menu = ctk.CTkOptionMenu(opts, variable=self._pid_var,
                                           values=["0x12d"], width=150,
                                           command=lambda _v: None)
        self._pid_menu.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(opts, text="选择片源后自动检测; 也可手输后回车", text_color=("gray40", "gray65")).grid(
            row=0, column=2, sticky="w", padx=(14, 14))
        self._pid_entry = ctk.CTkEntry(opts, width=90, placeholder_text="0x12d")
        self._pid_entry.grid(row=0, column=3, sticky="w")
        self._pid_entry.bind("<Return>", self._on_manual_pid)
        ctk.CTkButton(opts, text="重新检测", width=90, command=self._detect_pids).grid(row=0, column=3, padx=(100, 0))

        r += 1
        flags = ctk.CTkFrame(body, fg_color="transparent")
        flags.grid(row=r, column=0, columnspan=2, sticky="ew", pady=8)
        self._open_var = BooleanVar(value=saved.get("open_report", True))
        ctk.CTkCheckBox(flags, text="完成后打开 HTML 报告", variable=self._open_var).grid(row=0, column=0)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=24, pady=(8, 20))
        footer.grid_columnconfigure(0, weight=1)

        self._progress = ctk.CTkProgressBar(footer, mode="indeterminate")
        self._progress.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._progress.grid_remove()
        self._status_var = StringVar(value="就绪")
        ctk.CTkLabel(footer, textvariable=self._status_var, anchor="w",
                     text_color=("gray35", "gray65")).grid(row=1, column=0, sticky="ew")
        btn_row = ctk.CTkFrame(footer, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="e", pady=(12, 0))
        self._open_btn = ctk.CTkButton(btn_row, text="打开报告", width=100, command=self._open_report, state="disabled")
        self._open_btn.grid(row=0, column=0, padx=(0, 8))
        self._analyze_btn = ctk.CTkButton(btn_row, text="开始分析", width=120, command=self._start)
        self._analyze_btn.grid(row=0, column=1)

        if _DND:
            self.after(0, self._init_dnd)

    # ---------- 拖拽 ----------
    def _init_dnd(self):
        try:
            TkinterDnD._require(self)
            for w in (self._drop_host, self._drop_label):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
                w.dnd_bind("<<DragEnter>>", lambda _e: self._drop_host.configure(highlightbackground="#1f6aa5"))
                w.dnd_bind("<<DragLeave>>", lambda _e: self._drop_host.configure(highlightbackground="#4a4a4a"))
        except RuntimeError:
            self._drop_label.configure(text=self._drop_label.cget("text") + "\n(拖拽不可用，请用浏览按钮)")

    def _on_drop(self, event):
        if self._busy:
            return
        for raw in self.tk.splitlist(event.data):
            p = Path(str(raw).strip().strip("{}"))
            if p.is_file():
                self._set_input(p)
                break
        self._drop_host.configure(highlightbackground="#4a4a4a")

    # ---------- 路径 ----------
    def _set_input(self, p: Path):
        self._input_var.set(str(p))
        self._drop_label.configure(text="已选择\n%s" % p.name, fg="#e0e0e0", font=("Segoe UI", 14, "bold"))
        # 输出目录默认跟随片源所在目录
        self._outdir_var.set(str(p.parent))
        self.after(0, self._detect_pids)  # 拉取码流后自动检测视频 PID

    def _browse_input(self):
        p = filedialog.askopenfilename(title="选择 TS 片源",
            filetypes=[("TS 流", "*.ts *.mts *.trp *.m2ts"), ("All files", "*.*")])
        if p:
            self._set_input(Path(p))

    def _browse_outdir(self):
        p = filedialog.askdirectory(title="选择输出目录")
        if p:
            self._outdir_var.set(p)

    # ---------- PID 检测 ----------
    def _on_manual_pid(self, _evt=None):
        v = self._pid_entry.get().strip()
        if v:
            try:
                int(v, 0)
                self._pid_var.set(v)
                self._pid_menu.configure(values=[v])
            except ValueError:
                messagebox.showerror("参数错误", "PID 格式错误，请用 0x12d 这类格式")

    def _detect_pids(self):
        path = self._input_var.get().strip()
        if not path:
            return
        self._pid_menu.configure(values=["扫描中…"])
        self._pid_var.set("扫描中…")

        def worker():
            try:
                pids = self._scan_video_pids(path)
            except Exception:
                pids = []
            self._pid_result = pids  # 线程安全: 主线程 _tick 轮询

        self._pid_result = None
        threading.Thread(target=worker, daemon=True).start()
        self.after(300, self._poll_pids)

    def _poll_pids(self):
        pr = getattr(self, "_pid_result", None)
        if pr is not None:
            self._pid_result = None
            if not pr:
                self._pid_menu.configure(values=["未检出"])
                self._pid_var.set("未检出")
            else:
                self._pid_menu.configure(values=pr)
                self._pid_var.set(pr[0].split("(")[0])
        elif "扫描中" in self._pid_var.get():
            self.after(300, self._poll_pids)

    @staticmethod
    def _scan_video_pids(path: str):
        """扫 PMT (table_id=2) 找视频流 PID: stream_type 0x1b=H.264, 0x24=H.265"""
        found = {}
        with open(path, "rb") as fh:
            data = fh.read(188 * 6000)
        for off in range(0, len(data) - 187, 188):
            if data[off] != 0x47:
                continue
            pid = ((data[off+1] & 0x1F) << 8) | data[off+2]
            afc = (data[off+3] >> 4) & 3
            if not (afc & 1):
                continue
            payload_off = off + 4
            if afc & 2:
                payload_off += 1 + data[off+4]
            p = data[payload_off:off+188]
            if len(p) < 12 or p[0] != 0x02 or (p[1] & 0xB0) != 0xB0:
                continue
            sl = ((p[1] & 0x0F) << 8) | p[2]
            pil = ((p[10] & 0x0F) << 8) | p[11]
            i = 12 + pil
            end = min(3 + sl, len(p))
            while i + 5 <= end:
                st = p[i]
                epid = ((p[i+1] & 0x1F) << 8) | p[i+2]
                esil = ((p[i+3] & 0x0F) << 8) | p[i+4]
                if st in (0x1B, 0x24, 0x20):
                    found[epid] = st
                i += 5 + esil
        order = {0x1B: "H.264", 0x24: "H.265", 0x20: "MVC"}
        if found:
            return ["0x%03x(%s)" % (pid, order.get(st, "?")) for pid, st in sorted(found.items())]
        # 无 PMT (如 -map 截取的样本): 按 PES stream_id 0xE0 + H.264/265 NAL 经验检测
        return _scan_video_pids_heuristic(path)


    # ---------- 分析 ----------
    def _start(self):
        if self._busy:
            return
        path = self._input_var.get().strip()
        if not path:
            messagebox.showerror("参数错误", "请先选择片源文件")
            return
        try:
            pid = int(self._pid_var.get().split("(")[0].strip(), 0)
        except ValueError:
            messagebox.showerror("参数错误", "PID 无效，请重新检测或手动输入")
            return
        inp = Path(path)
        if not inp.is_file():
            messagebox.showerror("参数错误", "片源文件不存在:\n%s" % inp)
            return
        outdir = self._outdir_var.get().strip() or str(inp.parent)
        _save_settings({"input": str(inp), "outdir": outdir, "pid": self._pid_var.get().strip(),
                        "open_report": self._open_var.get()})
        self._busy = True
        self._analyze_btn.configure(state="disabled")
        self._open_btn.configure(state="disabled")
        self._progress.grid(); self._progress.start()
        self._started = time.time()
        self._stage = "解析中…"
        self._tick()

        def progress(i, n):
            # 线程安全: 只写共享变量, 由主线程 _tick 轮询刷新
            self._progress_i = i

        def worker():
            try:
                frames = T.analyze(str(inp), pid, progress)
                res_dist, type_dist, switches = T.summarize(frames)
                T.detect_anomalies(frames)
                os.makedirs(outdir, exist_ok=True)
                base = os_path_join(outdir, "pid_0x%x" % pid)
                html_path = base + "_report.html"
                T.build_html(frames, res_dist, type_dist, switches, html_path, pid, inp.name)
                self._write_csv_json(T, frames, base)
                nj = sum(1 for f in frames if f.get("pts_jump") is not None)
                nd = sum(1 for f in frames if f.get("dur_change") is not None)
                nf = sum(1 for f in frames if f.get("field_change"))
                msg = "完成 — %d 帧 | PTS跳变 %d | duration变化边界 %d | 场/帧切换 %d" % (len(frames), nj, nd, nf)
                self._result = ("ok", html_path, msg)
            except Exception as exc:
                tb = traceback.format_exc()
                self._result = ("err", "%s\n\n%s" % (exc, tb[-800:]))

        threading.Thread(target=worker, daemon=True, name="tsframe-analyze").start()

    @staticmethod
    def _write_csv_json(T, frames, base):
        import csv as csvmod, json as jsonmod
        with open(base + "_frames.csv", "w", newline="", encoding="utf-8") as fh:
            w = csvmod.writer(fh)
            w.writerow(['disp_idx','decode_idx','pts_sec','pts_hms','type','pic_mode','width','height',
                        'res_change','idr','has_sps','size','local_dur','dur_change','pts_jump','anomalies'])
            for fr in frames:
                r = fr['res']
                w.writerow([fr.get('disp_idx', fr['idx']), fr['idx'],
                            '%.4f' % fr['pts'] if fr['pts'] is not None else '',
                            T.fmt_ts(fr['pts']) if fr['pts'] is not None else '', fr['type'],
                            fr.get('pic_mode') or '',
                            r[0] if r else '', r[1] if r else '',
                            int(fr['res_change']), int(fr['idr']), int(fr['has_sps']), fr['size'],
                            fr.get('local_dur') or '',
                            ('%.0fms' % (fr['dur_change']*1000)) if fr.get('dur_change') is not None else '',
                            fr.get('pts_jump') or '',
                            ';'.join(fr.get('anomalies') or [])])
        with open(base + '_frames.json', 'w', encoding='utf-8') as fh:
            jsonmod.dump(frames, fh, ensure_ascii=False)

    def _tick(self):
        pr = getattr(self, "_pid_result", None)
        if pr is not None:
            self._pid_result = None
            if not pr:
                self._pid_menu.configure(values=["未检出"])
                self._pid_var.set("未检出")
            else:
                self._pid_menu.configure(values=pr)
                self._pid_var.set(pr[0].split("(")[0])
        res = getattr(self, "_result", None)
        if res is not None:
            self._result = None
            if res[0] == "ok":
                self._done(res[1])
                self._status_var.set("%s（%ds）" % (res[2], int(time.time() - self._started)))
            else:
                self._fail(res[1])
            return
        if not self._busy:
            return
        i = getattr(self, "_progress_i", 0)
        if i:
            self._status_var.set("解析中 %d 帧（%ds）" % (i, int(time.time() - self._started)))
        else:
            self._status_var.set("%s（%ds）" % (self._stage, int(time.time() - self._started)))
        self._timer_job = self.after(500, self._tick)

    def _done(self, html_path):
        self._busy = False
        self._progress.stop(); self._progress.grid_remove()
        if self._timer_job:
            self.after_cancel(self._timer_job); self._timer_job = None
        self._analyze_btn.configure(state="normal")
        self._open_btn.configure(state="normal")
        self._last_report = html_path
        self._status_var.set("完成 — %s（%ds）" % (html_path, int(time.time() - self._started)))
        if self._open_var.get():
            self._open_report()

    def _fail(self, msg):
        self._busy = False
        self._progress.stop(); self._progress.grid_remove()
        if self._timer_job:
            self.after_cancel(self._timer_job); self._timer_job = None
        self._analyze_btn.configure(state="normal")
        self._status_var.set("分析失败")
        messagebox.showerror("分析失败", msg)

    def _open_report(self):
        if self._last_report and Path(self._last_report).is_file():
            os_startfile(self._last_report)


def _scan_video_pids_heuristic(path: str):
    """无 PMT 时按 PES 特征探测视频 PID: stream_id=0xE0 且负载含 H.264/H.265 NAL"""
    votes = {}
    with open(path, "rb") as fh:
        data = fh.read(188 * 12000)
    for off in range(0, len(data) - 187, 188):
        if data[off] != 0x47:
            continue
        pid = ((data[off+1] & 0x1F) << 8) | data[off+2]
        if not ((data[off+1] >> 6) & 1):
            continue
        afc = (data[off+3] >> 4) & 3
        po = off + 4
        if afc & 2:
            po += 1 + data[off+4]
        p = data[po:off+188]
        if len(p) < 20 or p[0:3] != b"\x00\x00\x01" or (p[3] & 0xF0) != 0xE0:
            continue
        # 负载起始即 PES 头, 找 ES 里第一个 00 00 01 NAL
        hdr_len = p[8]
        es = p[9 + hdr_len: 9 + hdr_len + 8]
        nal = None
        for j in range(len(es) - 4):
            if es[j] == 0 and es[j+1] == 0 and es[j+2] == 1:
                nal = es[j+3] & 0x1F
                break
        if nal in (1, 5, 6, 7, 8, 9, 19, 20):
            v = votes.setdefault(pid, [set(), 0])
            v[0].add(nal); v[1] += 1
    cands = [(pid, cnt) for pid, (nals, cnt) in votes.items() if cnt >= 8 and (nals & {1, 5, 7, 9})]
    return ["0x%03x(H.264?)" % pid for pid, cnt in sorted(cands, key=lambda x: -x[1])]


def os_path_join(*parts):
    return os.path.join(*parts)

import os
def os_startfile(p):
    os.startfile(p)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
