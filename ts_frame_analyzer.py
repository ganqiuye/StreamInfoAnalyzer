# -*- coding: utf-8 -*-
"""
ts_frame_analyzer.py — TS 片源视频帧分析工具
解析 MPEG-TS 中指定 PID 的 H.264 视频流, 逐帧提取:
  - 帧序号 / PTS(秒)
  - 帧类型 (I/P/B, 来自 slice_type; IDR 单独标记)
  - 分辨率 (来自 SPS)
  - 大小(bytes), 是否携带 SPS
自动标记: 分辨率切换帧、IDR 帧、SPS 出现帧。
输出: pid_0xXXX_frames.csv / _frames.json / _report.html
  HTML 含时间轴总览(可点击定位)、切换点列表、逐帧明细(可滚动、按类型过滤)。

用法:
  python ts_frame_analyzer.py input.ts [video_pid_hex] [outdir]
  例: python ts_frame_analyzer.py sample.ts 0x12d ./out

中流一致性检查 (mid-stream check):
  ffmpeg 的 mpegts demuxer 只按首个 PMT 建流, 同一 PID 中途换编码
  (PMT stream_type 变化)时它不会重新协商, 整段仍按旧编码报告。
  本工具直接解析 TS 包跟踪 PMT 的 stream_type/PID 映射变化,
  并对视频 PID 的 ES 做 NAL 特征嗅探交叉验证:
    - ts_pmt_changes:  PMT 中 stream_type/PID 映射变化点(包号+新类型)
    - codec_switches:  ES NAL 层编码切换点(h264/hevc/mpeg2/mpeg4)
    - ts_consistent:   两者均无变化才为 True
"""
import sys, os, json, csv, datetime, re

# ------------------------------------------------- TS mid-stream consistency
# ffmpeg 的 mpegts demuxer 只按首个 PMT 建流, 同一 PID 中途换编码
# (PMT stream_type 变化)时不会重新协商, 整段按旧编码报告。
# 这里直接解析 TS 包: 跟踪 PMT 的 stream_type/PID 映射变化,
# 并对视频 PID 的 ES 做 NAL 特征嗅探交叉验证。
#
# HEVC NAL 首字节(type=(b>>1)&0x3F): 32=VPS 33=SPS 34=PPS 19/20=IDR
# H.264 NAL 首字节(type=b&0x1F):    7=SPS 8=PPS 5=IDR 9=AUD
# MPEG2: 序列头 00 00 01 B3;  MPEG4: VOS 00 00 01 B0

TS_PKT_SIZE = 188

STREAM_TYPE_NAMES = {
    0x01: "MPEG1", 0x02: "MPEG2", 0x03: "MP3", 0x04: "MPEG2", 0x0F: "AAC",
    0x10: "MPEG4", 0x11: "AAC", 0x1B: "H.264", 0x24: "H.265", 0x42: "AVS2",
    0x80: "MPEG2/PCM", 0x81: "AC3", 0x82: "DTS", 0x83: "TrueHD",
}
VIDEO_STREAM_TYPES = (0x01, 0x02, 0x10, 0x1B, 0x24, 0x42, 0x20)

# NAL 特征签名: 在视频 PES 负载里搜索, 用于编码嗅探(与 PMT 交叉验证)
_CODEC_SIGS = [
    ("H.264",  re.compile(rb"\x00\x00\x01(?:\x09[\xF0-\xF7]|[\x67\x68\x41\x01\x61])")),
    ("H.265",  re.compile(rb"\x00\x00\x01[\x40\x42\x44\x46\x26\x28][\x01\x02]")),
    ("MPEG2",  re.compile(rb"\x00\x00\x01[\xB3\xB5\xB8\x00]")),
    ("MPEG4",  re.compile(rb"\x00\x00\x01[\xB0\xB5\x20\x21]")),
]


def _ts_payload(pkt):
    """取 TS 包有效负载; 无有效负载返回 None"""
    if len(pkt) < TS_PKT_SIZE or pkt[0] != 0x47:
        return None
    afc = (pkt[3] >> 4) & 3
    off = 4
    if afc & 2:
        off += 1 + pkt[4]
    if not (afc & 1) or off >= 188:
        return None
    return pkt[off:188]


def _parse_pmt_streams(sec):
    """PMT section -> [(stream_type, es_pid), ...]; 非 PMT 或解析失败返回 None"""
    if len(sec) < 12 or sec[0] != 0x02:
        return None
    section_len = ((sec[1] & 0x0F) << 8) | sec[2]
    prog_info_len = ((sec[10] & 0x0F) << 8) | sec[11]
    i = 12 + prog_info_len
    streams = []
    while i < 3 + section_len - 4:
        if i + 5 > len(sec):
            break
        st = sec[i]
        epid = ((sec[i + 1] & 0x1F) << 8) | sec[i + 2]
        es_len = (sec[i + 3] << 8) | sec[i + 4]
        streams.append((st, epid))
        i += 5 + es_len
    return streams


def _iter_psi_sections(path, max_packets=None):
    """逐包扫描, 产出 (packet_index, pid, section_bytes)。只跟踪 PAT/PMT。"""
    buffers = {}
    pmt_pids = set()
    n = 0
    with open(path, 'rb') as f:
        while True:
            pkt = f.read(TS_PKT_SIZE)
            if len(pkt) < TS_PKT_SIZE:
                break
            n += 1
            if max_packets and n > max_packets:
                break
            if pkt[0] != 0x47:
                f.seek(-(TS_PKT_SIZE - 1), 1)  # 失步重同步
                continue
            pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
            if pid != 0 and pid not in pmt_pids:
                continue
            payload = _ts_payload(pkt)
            if payload is None:
                continue
            if (pkt[1] >> 6) & 1:  # PUSI: pointer_field
                ptr = payload[0]
                buf = buffers.setdefault(pid, bytearray())
                buf.clear()
                buf += payload[1 + ptr:]
                while len(buf) >= 3 and buf[0] != 0xFF:
                    table_id = buf[0]
                    sec_len = ((buf[1] & 0x0F) << 8) | buf[2]
                    if len(buf) < 3 + sec_len:
                        break
                    sec = bytes(buf[:3 + sec_len])
                    del buf[:3 + sec_len]
                    yield n, pid, sec
                    if pid == 0 and table_id == 0x00:
                        # PAT: 注册 program_map_PID
                        body = sec[8:3 + sec_len - 4]
                        for j in range(0, len(body) - 3, 4):
                            prog = (body[j] << 8) | body[j + 1]
                            pmt = ((body[j + 2] & 0x1F) << 8) | body[j + 3]
                            if prog != 0:
                                pmt_pids.add(pmt)
            else:
                buffers.setdefault(pid, bytearray()).extend(payload)


def sniff_ts_codec_changes(path, max_packets=None):
    """全流扫描 PMT, 返回 stream_type/PID 映射变化点列表。
    [{packet, pmt_pid, old:[...], new:[...]}]"""
    states = {}   # pmt_pid -> sorted tuple of (stream_type, es_pid)
    changes = []
    for n, pid, sec in _iter_psi_sections(path, max_packets):
        if pid == 0 or sec[0] != 0x02:
            continue
        streams = _parse_pmt_streams(sec)
        if not streams:
            continue
        key = tuple(sorted(streams))
        if key and pid in states and states[pid] != key:
            changes.append({
                "packet": n,
                "pmt_pid": pid,
                "old": states[pid],
                "new": key,
            })
        if key:
            states[pid] = key
    return changes


def fmt_stream_types(streams):
    return ", ".join("0x%04x=%s(0x%02x)" % (epid, STREAM_TYPE_NAMES.get(st, "?"), st)
                     for st, epid in streams)


def sniff_video_es_codec_switch(path, video_pids):
    """对视频 PID 的 PES 负载做 NAL 编码嗅探, 返回切换点列表。
    每个 PUSI PES 的首包负载按签名归类, 编码变化即记录。"""
    cur = None
    changes = []
    with open(path, 'rb') as f:
        n = 0
        while True:
            pkt = f.read(TS_PKT_SIZE)
            if len(pkt) < TS_PKT_SIZE:
                break
            n += 1
            if pkt[0] != 0x47:
                continue
            pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
            if pid not in video_pids or not ((pkt[1] >> 6) & 1):
                continue
            payload = _ts_payload(pkt)
            if payload is None:
                continue
            for codec, rx in _CODEC_SIGS:
                if rx.search(payload):
                    if cur is not None and codec != cur:
                        changes.append({"packet": n, "from": cur, "to": codec})
                    cur = codec
                    break
    return changes


def check_midstream_change(path, max_packets=None):
    """完整中流检查。返回 dict:
      ts_pmt_changes / codec_switches / ts_consistent / video_pids
    对非 TS 容器返回 None。"""
    if not str(path).lower().endswith((".ts", ".m2ts", ".mts", ".trp")):
        return None
    pmt_changes = sniff_ts_codec_changes(path, max_packets)
    video_pids = set()
    for _, _, sec in _iter_psi_sections(path, max_packets=200_000):
        streams = _parse_pmt_streams(sec)
        if streams:
            for st, epid in streams:
                if st in VIDEO_STREAM_TYPES:
                    video_pids.add(epid)
    codec_switches = sniff_video_es_codec_switch(path, video_pids) if video_pids else []
    return {
        "ts_pmt_changes": pmt_changes,
        "codec_switches": codec_switches,
        "video_pids": sorted(video_pids),
        "ts_consistent": not pmt_changes and not codec_switches,
    }

# ------------------------------------------------- TS demux
def ts_demux_video(path, pid):
    """返回 [(pts_or_None, dts_or_None, es_bytes), ...] 每个元素一个 PES(access unit)"""
    aus = []
    pes_buf = bytearray()
    cur_pts = None
    cur_dts = None
    in_pes = False
    with open(path, 'rb') as f:
        while True:
            pkt = f.read(188)
            if not pkt:
                break
            if len(pkt) < 188 or pkt[0] != 0x47:
                continue
            pusi = (pkt[1] >> 6) & 1
            cur_pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
            afc = (pkt[3] >> 4) & 3
            off = 4
            if afc & 2:
                off += 1 + pkt[4]
            if off >= 188:
                continue
            payload = pkt[off:] if (afc & 1) else b''
            if cur_pid != pid:
                continue
            if pusi:
                if pes_buf:
                    aus.append((cur_pts, cur_dts, bytes(pes_buf)))
                    pes_buf = bytearray()
                cur_dts = None
                if len(payload) >= 9 and payload[0] == 0 and payload[1] == 0 and payload[2] == 1:
                    sid = payload[3]
                    hdr = 9
                    if sid not in (0xBC, 0xBE, 0xBF, 0xF0, 0xF1, 0xFF, 0xF2, 0xF8):
                        dlen = payload[8]
                        hdr = 9 + dlen
                        cur_pts = None
                        flags = (payload[7] >> 6) & 3
                        if dlen >= 5 and flags in (2, 3):
                            # PTS: 5 bytes at payload[10..14] ('0011'/'0010' 前缀)
                            b = payload[9:14]
                            if len(b) == 5 and (b[0] >> 4) in (2, 3):
                                p = (((b[0] >> 1) & 7) << 30) | (b[1] << 22) \
                                    | ((b[2] >> 1) << 15) | (b[3] << 7) | (b[4] >> 1)
                                cur_pts = p / 90000.0
                            if flags == 3 and dlen >= 10:
                                bd = payload[14:19]
                                if len(bd) == 5 and (bd[0] >> 4) == 3:
                                    q = (((bd[0] >> 1) & 7) << 30) | (bd[1] << 22) \
                                        | ((bd[2] >> 1) << 15) | (bd[3] << 7) | (bd[4] >> 1)
                                    cur_dts = q / 90000.0
                    pes_buf += payload[hdr:]
                    in_pes = True
            elif in_pes:
                pes_buf += payload
    if pes_buf:
        aus.append((cur_pts, cur_dts, bytes(pes_buf)))
    return aus

# ------------------------------------------------------------ NAL parsing
def find_start_codes(buf):
    pos = []
    i = 0
    n = len(buf)
    while i < n - 3:
        if buf[i] == 0 and buf[i+1] == 0:
            if buf[i+2] == 1:
                pos.append((i, 3)); i += 3; continue
            if buf[i+2] == 0 and i + 3 < n and buf[i+3] == 1:
                pos.append((i, 4)); i += 4; continue
        i += 1
    return pos

class BitReader:
    __slots__ = ('data', 'pos')
    def __init__(self, data):
        self.data = data
        self.pos = 0
    def u(self, n):
        v = 0
        for _ in range(n):
            idx = self.pos >> 3
            byte = self.data[idx] if idx < len(self.data) else 0
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v
    def ue(self):
        lz = 0
        while self.u(1) == 0:
            lz += 1
            if lz > 32: raise ValueError('bad ue')
        if lz == 0: return 0
        return (1 << lz) - 1 + self.u(lz)
    def se(self):
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)

def strip_ep(data):
    """去掉 emulation prevention: 00 00 03 -> 00 00"""
    out = bytearray()
    i = 0
    n = len(data)
    zeros = 0
    while i < n:
        b = data[i]
        if zeros >= 2 and b == 3 and i + 1 < n and data[i+1] <= 3:
            zeros = 0; i += 1; continue
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
        i += 1
    return bytes(out)

def skip_scaling_list(br, size):
    last, default = 8, 8
    for _ in range(size):
        if br.u(1):
            delta = br.se()
            last = (last + delta + 256) % 256
            if last: default = last
        else:
            last = default

HIGH_PROFILES = (100,110,122,244,44,83,86,118,128,138,139,134,135)

def parse_sps(rbsp_raw):
    r = parse_sps_ext(rbsp_raw)
    return (r[0], r[1])

def parse_sps_ext(rbsp_raw):
    br = BitReader(strip_ep(rbsp_raw))
    profile_idc = br.u(8)
    br.u(8); br.u(8)
    br.ue()  # seq_parameter_set_id
    if profile_idc in HIGH_PROFILES:
        cf = br.ue()
        if cf == 3: br.u(1)
        br.ue(); br.ue()  # bit depth luma/chroma
        br.u(1)  # qpprime_y_zero_transform_bypass_flag
        if br.u(1):  # seq_scaling_matrix_present_flag
            cnt = 8 if cf != 3 else 12
            for k in range(cnt):
                if br.u(1):
                    skip_scaling_list(br, 16 if k < 6 else 64)
    lmfn = br.ue() + 4  # log2_max_frame_num
    pomt = br.ue()
    if pomt == 1:
        br.u(1); br.se(); br.se(); br.se(); br.se()
        br.se(); br.se(); br.u(1)
    br.ue()  # log2_max_poc_lsb
    br.ue()  # max_num_ref
    br.u(1)  # gaps
    w_mbs = br.ue() + 1
    h_map = br.ue() + 1
    fmof = br.u(1)
    if not fmof: br.u(1)  # mb_adaptive_frame_field_flag
    br.u(1)  # direct_8x8_inference_flag
    h_map *= 2 - fmof
    width = w_mbs * 16
    height = h_map * 16
    if br.u(1):
        cl = br.ue(); cr = br.ue(); ct = br.ue(); cb = br.ue()
        width -= (cl + cr) * 2
        height -= (ct + cb) * (2 if fmof else 4)
    return width, height, lmfn, fmof

SLICE_TYPE = {0:'P',1:'B',2:'I',3:'SP',4:'SI',5:'I',6:'P',7:'B',8:'SP',9:'SI'}

# 简化版: 直接按位读取, 由调用方传入 fmof/log2_mfn
def slice_info(nal_type, payload, fmof, log2_mfn):
    """返回 (slice_type, field_pic_flag, frame_num)。fmof/log2_mfn 来自当前 SPS。"""
    if nal_type not in (1, 5):
        return (None, None, None)
    br = BitReader(strip_ep(payload[:24]))
    try:
        br.ue()  # first_mb_in_slice
        st = br.ue()
        styp = SLICE_TYPE.get(st, '?')
        br.ue()  # pic_parameter_set_id
        fnum = br.u(log2_mfn) if log2_mfn else None
        fpf = None
        if fmof == 0 and nal_type != 5:
            fpf = br.u(1)
        return (styp, fpf, fnum)
    except Exception:
        return ('?', None, None)

# ------------------------------------------------------------ main analyze
def analyze(path, pid, progress=None):
    aus = ts_demux_video(path, pid)
    frames = []
    cur_res = None
    sps_cache = {}
    cur_sps_ext = (None, 6, 1)  # (res, log2_mfn, fmof)
    for i, (pts, dts, es) in enumerate(aus):
        codes = find_start_codes(es)
        ftype = None
        res = None
        has_sps = has_idr = False
        field_pic = None
        frame_num = None
        for idx, (off, scl) in enumerate(codes):
            start = off + scl
            end = codes[idx+1][0] if idx+1 < len(codes) else len(es)
            nal = es[start] & 0x1F
            payload = es[start+1:end]
            if nal == 7:
                has_sps = True
                key = bytes(payload[:24])
                if key in sps_cache:
                    res, cur_sps_ext = sps_cache[key], sps_cache[key + b'_ext']
                else:
                    try:
                        ext = parse_sps_ext(payload)
                        res = (ext[0], ext[1])
                        cur_sps_ext = (res, ext[2], ext[3])
                        sps_cache[key] = res
                        sps_cache[key + b'_ext'] = cur_sps_ext
                    except Exception:
                        res = None
            elif nal == 5:
                has_idr = True
            if nal in (1, 5):
                info = slice_info(nal, payload, cur_sps_ext[2], cur_sps_ext[1])
                if ftype is None:
                    ftype = info[0]
                if info[1] is not None:
                    field_pic = info[1]
                if info[2] is not None:
                    frame_num = info[2]
        if ftype is None:
            ftype = 'non-VCL'
        res_change = bool(res and cur_res and res != cur_res)
        if res:
            cur_res = res
        corrupted = False
        try:
            corrupted = (b'\x00\x00\x01' not in es) or len(es) < 8
        except Exception:
            corrupted = True
        frames.append({'idx': i, 'pts': pts, 'dts': dts, 'type': ftype, 'res': res,
                       'res_change': res_change, 'has_sps': has_sps,
                       'idr': has_idr, 'size': len(es), 'pts_jump': None,
                       'corrupted': corrupted, 'field_pic': field_pic,
                       'frame_num': frame_num})
        if progress and i % 500 == 0:
            progress(i, len(aus))
    # 报告以显示顺序为主序: 按 PTS 排序, 保留解码序号
    frames.sort(key=lambda f: (f['pts'] is None, f['pts'] if f['pts'] is not None else 0))
    for di, f in enumerate(frames):
        f['disp_idx'] = di
    return frames

def detect_anomalies(frames):
    """在【显示顺序】帧序列上做异常检测。

    核心思路: 把相邻帧 PTS 间隔(delta) 切成"等值段"(run)。
    - 同一段内 duration 一致 → 完全不是异常。
    - 段与段交界处 → duration 变化, 只标记交界前后两帧。
    - 只有个别 delta 偏离其所在段/邻近段的 duration → 才是真正的 PTS 跳变/丢帧。
    """
    import statistics
    ptsed = [f for f in frames if f['pts'] is not None]
    n = len(ptsed)
    for f in frames:
        f['pts_jump'] = None
        f['dur_change'] = None
        f['anomalies'] = []
        f['local_dur'] = None
    if n < 3:
        return frames

    # ---- 计算相邻显示帧间隔 ----
    deltas = [ptsed[i]['pts'] - ptsed[i-1]['pts'] for i in range(1, n)]  # len n-1, delta[i] 对应 ptsed[i+1]

    # ---- 切成等值段 (容差 1ms) ----
    tol = 0.001
    runs = []  # (start_delta_idx, end_delta_idx_exclusive, value)
    s = 0
    for k in range(1, len(deltas) + 1):
        if k == len(deltas) or abs(deltas[k] - deltas[s]) > tol:
            # 段值取中位数更稳
            seg = deltas[s:k]
            pos = [d for d in seg if d > tol]
            val = statistics.median(pos) if pos else None
            runs.append([s, k, val])
            s = k

    # ---- 段内正常, 段交界=duration 变化; 跳变 = 与前后段值都显著不同的孤立 delta ----
    # 建立 delta -> 所在段值 映射
    def run_val(di):
        for r in runs:
            if r[0] <= di < r[1]:
                return r[2]
        return None

    # 合并过碎的段 (单帧噪声段并入邻近段再判断): 对每个 delta, 找包含它的段值; 若段太短(<3),
    # 用邻近长段值作为基准
    long_vals = [r[2] for r in runs if r[1]-r[0] >= 3 and r[2]]
    ref_vals = sorted(set(round(v, 3) for v in long_vals)) if long_vals else []

    def nearest_ref(d):
        if not ref_vals: return None
        rr = [round(v, 3) for v in ref_vals]
        return min(rr, key=lambda v: abs(v - round(d, 3)))

    # duration 变化边界: 相邻长段值不同 → 标记段首 delta 对应的帧和前一帧
    prev_rv = None
    for r in runs:
        if r[2] is None: continue
        rv = round(r[2], 3)
        if r[1]-r[0] < 3:  # 碎段不触发 duration 变化
            continue
        if prev_rv is not None and abs(rv - prev_rv[1]) > 0.002:
            fi = r[0] + 1  # 该 delta 连接的帧 ptsed[fi]
            if 0 < fi < n:
                ptsed[fi-1]['dur_change'] = prev_rv[1]
                ptsed[fi]['dur_change'] = rv
        prev_rv = (r[0], rv)

    # PTS 跳变: delta 为负 → 回跳; delta 与 前后各帧局部基准都不同 → 跳变
    # 局部基准: 取该 delta 前后最近的同值长段值
    def baseline(di):
        # 向前后扩展找最近的 >=3 长段值
        for r in reversed(runs[: [x[0] for x in runs].index(next((x[0] for x in runs if x[0] <= di < x[1]), None)) + 1] if False else []):
            pass
        # 简化: 找 di 所在段, 若长段用其值; 否则向前/后找最近长段
        for r in runs:
            if r[0] <= di < r[1]:
                if r[1]-r[0] >= 3 and r[2]:
                    return r[2]
                # 向前找
                for r2 in reversed(runs[:runs.index(r)]):
                    if r2[2] and r2[1]-r2[0] >= 3: return r2[2]
                for r2 in runs[runs.index(r)+1:]:
                    if r2[2] and r2[1]-r2[0] >= 3: return r2[2]
                return r[2]
        return None

    for k, d in enumerate(deltas):
        fi = k + 1
        f, pf = ptsed[fi], ptsed[fi-1]
        base = baseline(k)
        if base is None: continue
        f['local_dur'] = round(base, 4)
        if d < -0.001:
            f['pts_jump'] = round(d, 4)
            continue
        if base is None: continue
        # 属于任何已知正常 duration (自身段值/邻近长段值) → 正常
        d3 = round(d, 3)
        if d3 == round(base, 3):
            continue
        # 与前后 delta 的段值比较: 若前后段值与 d 相同则正常(过渡帧)
        ok = False
        for r in runs:
            if r[2] and abs(r[2] - d) <= tol and (r[0] <= k <= r[1]):
                ok = True; break
        if ok: continue
        # 检查邻近: 若该 delta 等于前一个或后一个 delta (相同值连续出现) 视为段
        if k > 0 and abs(deltas[k-1] - d) <= tol: continue
        if k + 1 < len(deltas) and abs(deltas[k+1] - d) <= tol: continue
        if abs(d - base) > max(0.002, base * 0.25):
            f['pts_jump'] = round(d, 4)

    
# ---- 码率突刺 ----
    sizes = [f['size'] for f in frames]
    for i, f in enumerate(frames):
        lo = max(0, i-50); hi = min(len(frames), i+50)
        med = statistics.median(sizes[lo:hi])
        f['size_spike'] = bool(med > 0 and f['size'] > med * 6 and f['size'] > 200000)

    # ---- 场/帧模式切换 (PAFF, fmof=0 时 field_pic_flag 才有效) ----
    prev_fpf = None
    for i, f in enumerate(ptsed):
        fpf = f.get('field_pic')
        f['pic_mode'] = None if fpf is None else ('场' if fpf == 1 else '帧')
        if fpf is not None and prev_fpf is not None and fpf != prev_fpf:
            f.setdefault('anomalies', []).append('场/帧模式切换')
            f['field_change'] = True
        else:
            f['field_change'] = False
        if fpf is not None:
            prev_fpf = fpf

    # ---- 汇总 anomaly 标签 ----
    global_dur = 0.04
    for f in frames:
        if f.get('res_change'): f['anomalies'].append('分辨率切换')
        j = f.get('pts_jump')
        if j is not None:
            ld = f.get('local_dur') or global_dur or 0.04
            if j < 0:
                f['anomalies'].append('PTS回跳%.3fs' % j)
            elif j > ld * 1.5:
                lost = round(j / ld) - 1
                f['anomalies'].append('PTS跳变(≈丢%d帧, 间隔%.3fs)' % (lost, j) if lost > 0
                                      else 'PTS跳变(间隔%.3fs)' % j)
            else:
                f['anomalies'].append('PTS间隔过小(%.3fs)' % j)
        if f.get('corrupted'): f['anomalies'].append('数据异常')
        if f.get('size_spike'): f['anomalies'].append('码率突刺')
        if f.get('sps_change'): f['anomalies'].append('SPS参数变更')
        if f.get('dur_change') is not None:
            f['anomalies'].append('duration变为%.0fms' % (f['dur_change'] * 1000))
    return [f for f in frames if f.get('pts_jump') is not None]

def summarize(frames):
    res_dist = {}; type_dist = {}; switches = []
    for fr in frames:
        if fr['res']:
            k = '%dx%d' % fr['res']
            res_dist[k] = res_dist.get(k, 0) + 1
        type_dist[fr['type']] = type_dist.get(fr['type'], 0) + 1
        if fr['res_change']:
            switches.append(fr)
    return res_dist, type_dist, switches

def fmt_ts(t):
    if t is None: return '-'
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return '%02d:%02d:%06.3f' % (h, m, s)

# ---------------------------------------------------------------- HTML out
def build_html(frames, res_dist, type_dist, switches, path, pid, src_name):
    """生成异常检测版 HTML 报告"""
    n_ano = sum(1 for f in frames if f.get('anomalies'))
    by_kind = {}
    for f in frames:
        for a in f.get('anomalies', []):
            k = a.split('%')[0].split('+')[0].split('-')[0]
            by_kind[k] = by_kind.get(k, 0) + 1
    kind_badge = ''.join('<span class="badge %s">%s ×%d</span>' % (
        {'分辨率切换':'bk-res','PTS跳变':'bk-pts','PTS回跳':'bk-pts','PTS间隔过小':'bk-oth','数据异常':'bk-cor','码率突刺':'bk-spk','SPS参数变更':'bk-sps','场/帧模式切换':'bk-fld','duration变为':'bk-dur','PTS':'bk-pts','duration':'bk-dur'}.get(k,'bk-oth'),
        k, v) for k, v in sorted(by_kind.items(), key=lambda x:-x[1]))
    type_rows = ' / '.join('<b>%s</b>: %d' % (k, v) for k, v in sorted(type_dist.items()))
    res_rows = ' / '.join('<b>%s</b>: %d 帧' % (k, v) for k, v in sorted(res_dist.items(), key=lambda x:-x[1]))
    nsw = len(switches)

    # 异常列表(全部)
    ano_rows = []
    for f in frames:
        if not f.get('anomalies'): continue
        res_s = '%dx%d' % f['res'] if f['res'] else ''
        ano_rows.append("<tr class='rk-%s'><td>%d</td><td>%d</td><td>%s</td><td class='t%s'>%s</td><td>%s</td><td>%d</td><td>%s</td></tr>"
            % (f['type'], f.get('disp_idx', f['idx']), f['idx'], T_fmt(f['pts']), f['type'], f['type'], res_s, f['size'],
               ' '.join('<span class="tag">%s</span>' % a for a in f['anomalies'])))
    if not ano_rows:
        ano_rows.append("<tr><td colspan='7' style='text-align:center;color:#7a7;padding:16px'>✓ 未检测到异常帧</td></tr>")

    # 切换点
    sw_rows = ''.join("<tr><td>%d</td><td>%s</td><td>%dx%d</td></tr>"
        % (f['idx'], T_fmt(f['pts']), f['res'][0], f['res'][1]) for f in switches) or "<tr><td colspan='3' style='color:#7a7'>无</td></tr>"

    # 逐帧明细(限 HTML 体积: 异常帧全列, 普通帧最多保留前 3000 行; 全量看 CSV/JSON)
    rows = []
    normal_written = 0
    for fr in frames:
        has_ano = bool(fr.get('anomalies'))
        if not has_ano and normal_written >= 3000:
            continue
        res_s = '%dx%d' % fr['res'] if fr['res'] else ''
        cls = 'idr' if fr['idr'] else {'I':'i','P':'p','B':'b'}.get(fr['type'],'')
        if fr.get('anomalies'): cls = 'anomaly ' + cls
        marks = []
        if fr.get('res_change'): marks.append('<b class="mk">⟲分辨率切换</b>')
        if fr.get('pts_jump') is not None: marks.append('<b class="mk mk-pts">⏱PTS%+.2fs</b>' % fr['pts_jump'])
        if fr.get('corrupted'): marks.append('<b class="mk mk-cor">✖损坏</b>')
        if fr.get('size_spike'): marks.append('<b class="mk mk-spk">▲突刺</b>')
        if fr.get('dur_change') is not None: marks.append('<b class="mk mk-dur">⇄duration→%.0fms</b>' % (fr['dur_change']*1000))
        if fr.get('field_change'): marks.append('<b class="mk mk-fld">⇅%s→%s</b>' % (frames[max(0,fr.get("disp_idx",fr["idx"])-1)].get("pic_mode") or "?", fr.get("pic_mode") or "?"))
        elif fr.get('pic_mode'): marks.append('<span class="pm">%s</span>' % fr['pic_mode'])
        if fr['idr']: marks.append('<b class="mk idrm">IDR</b>')
        rows.append("<tr id='r%d' data-k='%s' class='%s'><td>%d</td><td>%d</td><td>%s</td><td class='t%s'>%s</td><td>%s</td><td>%d</td><td>%s</td></tr>"
            % (fr.get('disp_idx', fr['idx']), fr['type'], ('res-switch ' + cls) if fr['res_change'] else cls,
               fr.get('disp_idx', fr['idx']), fr['idx'], T_fmt(fr['pts']), fr['type'], fr['type'], res_s, fr['size'],
               ' '.join(marks)))
        if not has_ano: normal_written += 1

    fj = json.dumps([dict(idx=f.get('disp_idx', f['idx']), didx=f['idx'], type=f['type'], idr=f['idr'],
        res_change=f['res_change'], anomalies=f.get('anomalies', []), pic_mode=f.get('pic_mode'),
        dur_change=(round(f['dur_change'],4) if f.get('dur_change') is not None else None),
        res=list(f['res']) if f['res'] else None,
        pts=round(f['pts'],3) if f['pts'] is not None else None,
        pts_str=T_fmt(f['pts']), size=f['size']) for f in frames])

    html = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>TS 帧分析 — PID 0x%(pid)03x — %(src)s</title>
<style>
:root{--bg:#0f1115;--card:#1a1d24;--line:#2a2e38;--fg:#d7dae0;--mut:#8a90a0;
--red:#ff4d6d;--org:#ff9f43;--yel:#ffd24a;--blu:#4cc9f0;--grn:#2dc653}
*{box-sizing:border-box}
body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;margin:0;background:var(--bg);color:var(--fg);padding:24px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{font-size:12px;color:var(--mut)}.card .v{font-size:22px;font-weight:600;margin-top:4px}
.card .v small{font-size:12px;color:var(--mut)}
.card.warn .v{color:var(--red)}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;margin:2px 4px 2px 0}
.bk-res{background:#5a1030;color:#ff9ecd}.bk-pts{background:#3a2e10;color:#ffd24a}
.bk-cor{background:#40151a;color:#ff8a9a}.bk-spk{background:#123a3a;color:#6ee7d8}
.bk-sps{background:#2a2040;color:#c0a6ff}.bk-fld{background:#143a22;color:#7bd88f}
.bk-dur{background:#241a3a;color:#c0a6ff}.bk-oth{background:#262a33;color:#aab}
h2{font-size:15px;margin:26px 0 10px;display:flex;align-items:center;gap:8px}
h2 .cnt{font-size:11px;background:#2d6a4f;color:#fff;border-radius:10px;padding:1px 8px}
table{border-collapse:collapse;width:100%%;font-size:12px}
th{position:sticky;top:0;background:#242832;color:#cdd2dc;padding:6px 10px;text-align:left;z-index:2;border-bottom:1px solid var(--line)}
td{border-bottom:1px solid #22262e;padding:3px 10px}
tr.i td{background:#2a2410}.tI{color:var(--yel);font-weight:600}
tr.p td{background:#10222e}.tP{color:var(--blu)}
tr.b td{background:transparent}.tB{color:#6a7080}
tr.idr td{background:#33220c}
tr.res-switch td{background:#4d0f2a !important;color:#ffb3d1;font-weight:600}
tr.anomaly td{background:#2d1114 !important}
tr.anomaly td:first-child{border-left:3px solid var(--red)}
.mk{margin-left:6px;font-size:11px}.idrm{color:var(--org)}.mk-pts{color:var(--yel)}.mk-cor{color:var(--red)}.mk-spk{color:#6ee7d8}.mk-dur{color:#c0a6ff}.mk-fld{color:#7bd88f}.pm{color:#5a6272;font-size:10px;margin-left:4px}
.tag{display:inline-block;background:#402028;color:#ff9aae;border-radius:8px;padding:0 8px;font-size:11px;margin-right:4px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px 0;max-height:320px;overflow:auto;margin:8px 0}
#scroll{max-height:60vh;overflow:auto;border:1px solid var(--line);border-radius:10px}
.filter{margin:10px 0;display:flex;gap:6px;flex-wrap:wrap}
.filter button{padding:5px 16px;background:#262a33;color:var(--mut);border:1px solid var(--line);border-radius:16px;cursor:pointer;font-size:12px}
.filter button.on{background:#2d6a4f;color:#fff;border-color:#2d6a4f}
canvas{background:#161a20;border:1px solid var(--line);border-radius:8px;cursor:crosshair;display:block}
#tip{position:fixed;pointer-events:none;background:#000e;color:#fff;padding:8px 12px;border-radius:6px;font-size:12px;display:none;z-index:9;white-space:nowrap;border:1px solid #444}
.legend{font-size:12px;color:var(--mut);margin:6px 0 0}.dot{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 12px;vertical-align:-1px}
footer{margin-top:24px;color:var(--mut);font-size:12px}
</style></head><body>
<h1>TS 流帧分析报告</h1>
<div class="sub">%(src)s — PID 0x%(pid)03x — 生成于 %(gen)s</div>
<div class="cards cards-top"></div>
<div class="cards">
  <div class="card"><div class="k">总帧数</div><div class="v">%(nframes)d</div></div>
  <div class="card"><div class="k">分辨率</div><div class="v">%(res_main)s</div></div>
  <div class="card"><div class="k">I / P / B</div><div class="v">%(cnt_i)s <small>/</small> %(cnt_p)s <small>/</small> %(cnt_b)s</div></div>
  <div class="card"><div class="k">IDR 帧</div><div class="v">%(nidr)d</div></div>
  <div class="card warn"><div class="k">分辨率切换</div><div class="v">%(nsw)d</div></div>
  <div class="card warn"><div class="k">异常帧</div><div class="v">%(n_ano)d</div></div>
</div>
<h2>异常概览 <span style="font-weight:normal;font-size:12px;color:var(--mut)">检测项: 分辨率切换 / PTS 跳变 / 损坏帧 / 码率突刺 / SPS 变更</span></h2>
<div>%(kind_badge)s</div>
<h2>时间轴总览 <span class="cnt">点击定位 · 左右滚动查看</span></h2>
<div id="cvwrap" style="overflow-x:auto;overflow-y:hidden;border:1px solid var(--line);border-radius:8px">
<canvas id="cv" height="86"></canvas>
</div>
<div class="legend">
  <span class="dot" style="background:#ff2a6d"></span>异常帧(悬停看类型)
  <span class="dot" style="background:#ff9f1c"></span>IDR
  <span class="dot" style="background:#ffd24a"></span>I
  <span class="dot" style="background:#4cc9f0"></span>P
  <span class="dot" style="background:#3a3f4a"></span>B
</div>
<div id="tip"></div>
<h2>异常帧明细 <span class="cnt">%(n_ano)s 帧</span></h2>
<div class="panel"><table><tr><th>#(显示)</th><th>解码#</th><th>PTS</th><th>类型</th><th>分辨率</th><th>大小</th><th>异常</th></tr>
%(ano_rows)s
</table></div>
<h2>分辨率切换点 <span class="cnt">%(nsw)s 处</span></h2>
<table style="width:auto;min-width:340px"><tr><th>帧号</th><th>PTS</th><th>新分辨率</th></tr>
%(sw_rows)s
</table>
<h2>逐帧明细 <span style="font-weight:normal;font-size:12px;color:var(--mut)">异常帧全量, 普通帧仅前 3000 行; 全量见 CSV/JSON</span></h2>
<div class="filter">
  <button data-f="all" class="on">全部</button><button data-f="I">I</button>
  <button data-f="P">P</button><button data-f="B">B</button>
  <button data-f="sw">切换帧</button><button data-f="ano">异常帧</button>
</div>
<div id="scroll"><table id="ft"><tr><th>#(显示)</th><th>解码#</th><th>PTS</th><th>类型</th><th>分辨率</th><th>大小</th><th>标记</th></tr>
%(rows)s
</table></div>
<footer>ts_frame_analyzer.py — 帧按显示顺序(PTS递增)排列; #列为显示序号, 明细含解码序号; PTS 为显示时间戳</footer>
<script>
var FILT='all';
document.querySelectorAll('.filter button').forEach(function(b){
 b.onclick=function(){FILT=b.dataset.f;
  document.querySelectorAll('.filter button').forEach(function(x){x.classList.remove('on')});
  b.classList.add('on');applyF();};});
function hasAno(r){return r.className.indexOf('anomaly')>=0;}
function applyF(){document.querySelectorAll('#ft tr[data-k]').forEach(function(r){
 var show;
 if(FILT==='all')show=true;
 else if(FILT==='sw')show=r.className.indexOf('res-switch')>=0;
 else if(FILT==='ano')show=hasAno(r);
 else show=(r.dataset.k===FILT);
 r.style.display=show?'':'none';});}
var FRAMES=%(frames_json)s;
var N=FRAMES.length,cv=document.getElementById('cv'),ctx=cv.getContext('2d');
var PXF=3; // 每帧像素宽, 总宽=N*PXF, 超宽时压缩
function fit(){cv.style.width='';var ideal=N*PXF;cv.width=Math.min(ideal,32000);draw();}
function draw(){var W=cv.width,H=86;ctx.clearRect(0,0,W,H);
 for(var i=0;i<N;i++){var f=FRAMES[i];
  var c='#3a3f4a';                       // B
  if(f.type==='I')c='#ffd24a';           // I
  else if(f.type==='P')c='#4cc9f0';      // P
  if(f.idr)c='#ff9f1c';                  // IDR 覆盖 I
  if(f.anomalies&&f.anomalies.length)c='#ff2a6d';  // 异常帧统一红色
  ctx.fillStyle=c;ctx.fillRect(i/N*W,8,Math.max(1,W/N),H-16);}}
fit();
cv.onmousemove=function(e){var r=cv.getBoundingClientRect(),i=Math.floor((e.clientX-r.left)/r.width*N);
 if(i>=0&&i<N){var f=FRAMES[i],t=document.getElementById('tip');
 t.style.display='block';t.style.left=Math.min(e.clientX+14,window.innerWidth-220)+'px';t.style.top=(e.clientY+14)+'px';
 t.innerHTML='#'+i+' <b>'+f.type+'</b>'+(f.idr?' (IDR)':'')+(f.pic_mode?' ['+f.pic_mode+']':'')+(f.res?' · '+f.res[0]+'x'+f.res[1]:'')
  +'<br>'+f.pts_str+' · '+f.size+' B'
  +(f.anomalies&&f.anomalies.length?'<br><span style="color:#ff8a9a">'+f.anomalies.join(' / ')+'</span>':'');}};
cv.onmouseleave=function(){document.getElementById('tip').style.display='none';};
cv.onclick=function(e){var r=cv.getBoundingClientRect(),i=Math.floor((e.clientX-r.left)/r.width*N);
 if(i<0||i>=N)return;
 var el=document.getElementById('r'+i);
 if(el){el.scrollIntoView({block:'center'});el.style.outline='2px solid var(--red)';
 setTimeout(function(){el.style.outline='';},1600);}
 else{ // 普通帧被截断, 提示
  document.getElementById('tip').style.display='block';
  document.getElementById('tip').style.left=(e.clientX+14)+'px';
  document.getElementById('tip').style.top=(e.clientY+14)+'px';
  setTimeout(function(){document.getElementById('tip').style.display='none';},1200);}};
</script></body></html>""" % dict(
        pid=pid, src=src_name, gen=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        nframes=len(frames), type_rows=type_rows, res_main=(list(res_dist.keys())[0] if res_dist else '-'),
        cnt_i=type_dist.get('I',0), cnt_p=type_dist.get('P',0), cnt_b=type_dist.get('B',0),
        nidr=sum(1 for f in frames if f['idr']), nsw=nsw, n_ano=n_ano,
        kind_badge=kind_badge, sw_rows=sw_rows, ano_rows=''.join(ano_rows), rows=''.join(rows), frames_json=fj,
        res_rows=res_rows)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(html)

def T_fmt(t):
    return fmt_ts(t)

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    ts_path = sys.argv[1]
    outdir = sys.argv[3] if len(sys.argv) > 3 else os.path.dirname(os.path.abspath(ts_path))
    if outdir and not os.path.exists(outdir):
        os.makedirs(outdir)

    # ---- 中流一致性检查: PMT stream_type 变化 + ES NAL 编码嗅探 ----
    print('中流一致性检查 ...', file=sys.stderr)
    mid = check_midstream_change(ts_path)
    if mid is not None:
        if mid['ts_pmt_changes']:
            for c in mid['ts_pmt_changes']:
                print('!! PMT stream_type 变化 @ packet #%d:' % c['packet'], file=sys.stderr)
                print('   旧: %s' % fmt_stream_types(c['old']), file=sys.stderr)
                print('   新: %s' % fmt_stream_types(c['new']), file=sys.stderr)
        if mid['codec_switches']:
            for c in mid['codec_switches']:
                print('!! ES 编码切换 @ packet #%d: %s -> %s (PID 见 video_pids)'
                      % (c['packet'], c['from'], c['to']), file=sys.stderr)
        if not mid['ts_consistent']:
            print('>> 该流同一 PID 中途换编码, ffmpeg 单次探测只会报首段编码;'
                  ' 建议按切换点分包分析', file=sys.stderr)
        else:
            print('中流检查: 一致 (无 PMT/编码变化)', file=sys.stderr)

    # ---- 未指定 PID 时: 有且仅有一个视频 PID 则直接用之 ----
    if len(sys.argv) > 2:
        pid = int(sys.argv[2], 0)
    elif mid and len(mid['video_pids']) == 1:
        pid = mid['video_pids'][0]
        print('自动选择视频 PID: 0x%x' % pid, file=sys.stderr)
    else:
        pid = 0x12d

    def prog(i, n):
        if i % 5000 == 0:
            print('  已解析 %d 帧...' % i, file=sys.stderr)
    print('解析 %s (pid=0x%x) ...' % (ts_path, pid), file=sys.stderr)
    frames = analyze(ts_path, pid, prog)
    res_dist, type_dist, switches = summarize(frames)
    detect_anomalies(frames)
    base = os.path.join(outdir, 'pid_0x%x' % pid)
    with open(base + '_frames.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['disp_idx','decode_idx','pts_sec','pts_hms','type','pic_mode','width','height',
                    'res_change','idr','has_sps','size','local_dur','dur_change','pts_jump','anomalies'])
        for fr in frames:
            r = fr['res']
            w.writerow([fr.get('disp_idx', fr['idx']), fr['idx'],
                        '%.4f' % fr['pts'] if fr['pts'] is not None else '',
                        fmt_ts(fr['pts']) if fr['pts'] is not None else '', fr['type'],
                        fr.get('pic_mode') or '',
                        r[0] if r else '', r[1] if r else '',
                        int(fr['res_change']), int(fr['idr']), int(fr['has_sps']), fr['size'],
                        fr.get('local_dur') or '',
                        ('%.0fms' % (fr['dur_change']*1000)) if fr.get('dur_change') is not None else '',
                        fr.get('pts_jump') or '',
                        ';'.join(fr.get('anomalies') or [])])
    with open(base + '_frames.json', 'w', encoding='utf-8') as fh:
        json.dump(frames, fh, ensure_ascii=False)
    html_path = base + '_report.html'
    build_html(frames, res_dist, type_dist, switches, html_path, pid, os.path.basename(ts_path))
    print('帧数: %d' % len(frames))
    print('帧类型: %s' % type_dist)
    print('分辨率: %s' % res_dist)
    print('分辨率切换点: %d' % len(switches))
    for f in switches[:100]:
        print('  帧 #%d  %s  -> %dx%d' % (f['idx'], fmt_ts(f['pts']), f['res'][0], f['res'][1]))
    nj = sum(1 for f in frames if f.get('pts_jump') is not None)
    nd = sum(1 for f in frames if f.get('dur_change') is not None)
    nf = sum(1 for f in frames if f.get('field_change'))
    print('PTS跳变: %d, duration变化边界帧: %d, 场/帧切换: %d' % (nj, nd, sum(1 for f in frames if f.get('field_change'))))
    print('输出: %s' % html_path)

if __name__ == '__main__':
    main()
