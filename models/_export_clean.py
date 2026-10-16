#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import glob
import heapq
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DEFAULT_CONFIG = os.path.join(PROJECT_ROOT, "output", "config")

# 名称兜底规则：命中即视为特效面/外挂物，剔除
NAME_EXCL = re.compile(
    r"prop0|jet_flame|flare"
    r"|pylon|launcher|launch|bomb|missile|adapter|holder|rocket|pod"
    r"|bdz|usk|dbz|pfx|gdj|twin_"
    r"|(^|_)pl[0-9_]|(^|_)pl12|yj_|(^|_)kh[0-9]|(^|_)r_[0-9]|fab|kab"
    r"|(^|_)ch_|su_s_|aku_|apu_|(^|_)fin[0-9]"
)

BLK_HIDE_KEYS = ("HideNodes", "HideDmParts")   # WeaponSlots 下“默认隐藏”的两个字段

# 例外：命中即保留（理由见文件头“例外”一节）
KEEP_EXCEPTIONS = re.compile(r"^rotor")


# --------------------------------------------------------------------------
#  Dagor BLK 解析（只取所需字段，容错优先：解析失败即忽略该文件）
# --------------------------------------------------------------------------
def _tokenize(text):
    i, n, toks = 0, len(text), []
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":      # 行注释
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == '"':
                    break
                buf.append(text[j])
                j += 1
            toks.append(("str", "".join(buf)))
            i = j + 1
            continue
        if c in "{}[]:,":
            toks.append((c, c))
            i += 1
            continue
        j = i
        while j < n and text[j] not in " \t\r\n{}[]:,":
            j += 1
        toks.append(("str", text[i:j]))
        i = j
    return toks


def parse_blk(text):
    """把 BLK 文本解析成嵌套 dict/list（键值均为字符串）。失败抛异常，由调用方兜底。"""
    toks, pos = _tokenize(text), [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else (None, None)

    def value():
        t, v = peek()
        if t == "{":
            pos[0] += 1
            d = {}
            while True:
                t2, v2 = peek()
                if t2 is None or t2 == "}":
                    pos[0] += 1
                    break
                if t2 == ",":
                    pos[0] += 1
                    continue
                pos[0] += 1
                if peek()[0] == ":":
                    pos[0] += 1
                d[v2] = value()
            return d
        if t == "[":
            pos[0] += 1
            a = []
            while True:
                t2, _ = peek()
                if t2 is None or t2 == "]":
                    pos[0] += 1
                    break
                if t2 == ",":
                    pos[0] += 1
                    continue
                a.append(value())
            return a
        pos[0] += 1
        return v

    return value()


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def flightmodel_dirs(config_root):
    """在 config 解包目录下定位 gamedata\flightmodels（各包解包目录名不固定，逐个候选试探）。"""
    cands = [
        os.path.join(config_root, "aces.vromfs.bin", "aces.vromfs.bin_u", "gamedata", "flightmodels"),
        os.path.join(config_root, "aces.vromfs.bin_u", "gamedata", "flightmodels"),
        os.path.join(config_root, "gamedata", "flightmodels"),
    ]
    try:
        for pkg in os.listdir(config_root):
            pkg_dir = os.path.join(config_root, pkg)
            if not os.path.isdir(pkg_dir):
                continue
            cands.append(os.path.join(pkg_dir, "gamedata", "flightmodels"))
            cands.append(os.path.join(pkg_dir, pkg + "_u", "gamedata", "flightmodels"))
    except OSError:
        pass
    out = []
    for c in cands:
        if os.path.isdir(c) and c not in out:
            out.append(c)
    return out


def build_hide_index(config_root, verbose=False):
    """返回 (hide_index, aircraft_names, dirs)。
    hide_index     {模型名(小写): {默认隐藏节点名}}，用于清理飞机；
    aircraft_names 全部飞行模型名(小写)的集合，用于判定「这个资源是飞机」。"""
    index = {}
    aircraft = set()
    dirs = flightmodel_dirs(config_root)
    if not dirs:
        return index, aircraft, []
    blks = []
    for d in dirs:
        blks += glob.glob(os.path.join(d, "*.blk"))
    for path in blks:
        try:
            with io.open(path, encoding="utf-8", errors="ignore") as f:
                doc = parse_blk(f.read())
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        names = {str(doc.get("model") or stem), stem}          # model 字段优先，文件名兜底
        aircraft.update(n.lower() for n in names)
        hide = set()
        ws = doc.get("WeaponSlots")
        if isinstance(ws, dict):
            for key in BLK_HIDE_KEYS:
                blk = ws.get(key)
                if isinstance(blk, dict):
                    hide.update(str(x) for x in _as_list(blk.get("node")) if isinstance(x, str))
        if not hide:
            continue
        for key in names:
            index.setdefault(key.lower(), set()).update(hide)
    if verbose:
        print("[配置] flightmodels 目录：%s" % "; ".join(dirs))
        print("[配置] 飞行模型 %d 个（其中 %d 个带默认隐藏节点表；BLK %d 个）"
              % (len(aircraft), len(index), len(blks)))
    return index, aircraft, dirs


def model_name_of(obj_path):
    """由 OBJ 文件名反推模型名：<包名>__<资源名>_LOD<n>.obj -> <资源名>。"""
    stem = os.path.basename(obj_path)
    if stem.lower().endswith(".obj"):
        stem = stem[:-4]
    if "__" in stem:
        stem = stem.split("__", 1)[1]
    return re.sub(r"_LOD\d+$", "", stem, flags=re.I).lower()


VARIANT_SUFFIXES = ("_dmg", "_xray", "_dstr", "_decals", "_damaged")

# 配对缓存：{obj 绝对路径: (hide_set|None, key|None)}，避免同一文件被重复读
_RESOLVE_CACHE = {}


def _obj_group_names(path, cap=40000):
    """读取 OBJ 的零件组名集合（只取 'g ' 行）。"""
    gs = set()
    try:
        with io.open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line[:2] == "g ":
                    gs.add(line[2:].strip())
                    if len(gs) >= cap:
                        break
    except OSError:
        pass
    return gs


def resolve_aircraft(obj_path, hide_index):
    """OBJ -> 飞行模型 BLK 的两级配对，返回 (隐藏节点集, 机型键)，不可信则 (None, None)。

    1) 精确名；2) 变体前缀（资源 saab_a32a ↔ BLK saab_a32a_red_adam）+ 组名重叠校验：
    前缀不足以判定机体——吊舱/副油箱共享同一前缀。实测机体重叠 3~149、配件 0~1，故取 >=2。
    """
    if not hide_index:
        return None, None
    key_path = os.path.abspath(obj_path)
    if key_path in _RESOLVE_CACHE:
        return _RESOLVE_CACHE[key_path]
    name = model_name_of(obj_path)
    cands = [name]
    stripped = name
    for suf in VARIANT_SUFFIXES:
        if stripped.endswith(suf):
            stripped = stripped[: -len(suf)]
            if stripped not in cands:
                cands.append(stripped)
    # 1) 精确
    for c in cands:
        if c in hide_index:
            res = (hide_index[c], c)
            _RESOLVE_CACHE[key_path] = res
            return res
    # 2) 前缀 + 重叠验证
    keys = []
    for c in cands:
        if len(c) >= 4:
            keys += [k for k in hide_index if k.startswith(c + "_") or c.startswith(k + "_")]
    keys = sorted(set(keys))
    if not keys:
        _RESOLVE_CACHE[key_path] = (None, None)
        return None, None
    gs = _obj_group_names(obj_path)
    best = None
    for k in keys:
        hid = hide_index[k]
        ov = len(gs & hid)
        if best is None or ov > best[0] or (ov == best[0] and len(k) < len(best[1])):
            best = (ov, k, hid)
    if best and best[0] >= 2:
        res = (best[2], best[1])
    else:
        res = (None, None)
    _RESOLVE_CACHE[key_path] = res
    return res


def is_aircraft(obj_path, aircraft_names, hide_index=None):
    """该 OBJ 是否飞机资源。给 hide_index 时走两级可信配对，否则退回旧的精确名判定。"""
    if hide_index:
        return resolve_aircraft(obj_path, hide_index)[0] is not None
    if not aircraft_names:
        return False
    name = model_name_of(obj_path)
    return name in aircraft_names or re.sub(r"_(dmg|xray)$", "", name) in aircraft_names


def copy_through(src, dst):
    """非飞机资源：原样复制，不删不改。"""
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    shutil.copyfile(src, dst)


# --------------------------------------------------------------------------
#  清理
# --------------------------------------------------------------------------
def clean_one(src, dst, hide_index=None, report=None, hide=None):
    """写出剔除非机体件后的 OBJ；返回 (保留顶点数, 保留面数)。report 为 list 时收集被剔除的组名。"""
    if hide is None:
        hide = (hide_index or {}).get(model_name_of(src), frozenset())
    old = 0
    old2new = {}
    result_v = []
    result_f = []          # (group, "f ...")
    g = None
    keep = True
    for line in io.open(src, encoding="utf-8", errors="ignore"):
        s = line[:2]
        if line[:2] == "g ":
            g = line[2:].strip()
            if KEEP_EXCEPTIONS.search(g):
                keep = True
            else:
                keep = not (g in hide or NAME_EXCL.search(g))
                if not keep and report is not None:
                    report.append((g, "游戏默认隐藏" if g in hide else "名称规则"))
        elif s == "v ":
            if keep:
                old2new[old] = len(result_v) + 1
                result_v.append(line)
            old += 1
        elif s == "f ":
            if keep:
                idx = [int(t.split("/")[0]) - 1 for t in line.split()[1:]]
                if all(i in old2new for i in idx):
                    ni = [old2new[i] for i in idx]
                    for k in range(1, len(ni) - 1):
                        result_f.append((g, "f %d %d %d\n" % (ni[0], ni[k], ni[k + 1])))
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with io.open(dst, "w", encoding="utf-8") as f:
        f.write("# clean airframe (default-hidden parts + FX/stores removed) by _export_clean.py\n")
        for vl in result_v:
            f.write(vl)
        prev = object()
        for gname, fl in result_f:
            if gname != prev:
                f.write("g %s\n" % gname)
                prev = gname
            f.write(fl)
    return len(result_v), len(result_f)


# 批处理并行：复制属 I/O 密集、清理属 CPU 密集，按文件大小贪心分箱后并行。
def _dump_index(path, hide_index, aircraft_names):
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump({"hide": {k: sorted(v) for k, v in hide_index.items()},
                   "aircraft": sorted(aircraft_names)}, f, ensure_ascii=False)


def _load_index(path):
    with io.open(path, encoding="utf-8") as f:
        d = json.load(f)
    return {k: set(v) for k, v in d["hide"].items()}, set(d["aircraft"])


def _write_stat(path, wid, n, n_cleaned, n_passed, n_by_data, n_by_name, n_groups, final=False):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump({"wid": wid, "n": n, "cleaned": n_cleaned, "passed": n_passed,
                   "by_data": n_by_data, "by_name": n_by_name, "groups": n_groups,
                   "done": final}, f)
    os.replace(tmp, path)


def _batch_worker_part(files, srcd, dstd, index_path, stat_path, log_path, wid):
    """子进程：非机体原样复制，机体按隐藏表 + 名称规则清理。"""
    hide_index, aircraft_names = _load_index(index_path)
    logf = io.open(log_path, "w", encoding="utf-8") if log_path else None
    n = n_cleaned = n_passed = n_by_data = n_by_name = n_groups = 0
    for p in files:
        o = os.path.join(dstd, os.path.relpath(p, srcd))
        n += 1
        hide, _key = resolve_aircraft(p, hide_index)
        if hide is None:
            copy_through(p, o)
            n_passed += 1
        else:
            n_cleaned += 1
            report = []
            clean_one(p, o, hide_index, report, hide=hide)
            if report:
                g_data = sum(1 for _, why in report if why == "游戏默认隐藏")
                n_groups += len(report)
                n_by_data += g_data
                n_by_name += len(report) - g_data
                if logf:
                    logf.write("  %s -> 剔除 %d 组：%s\n" % (
                        os.path.relpath(p, srcd), len(report),
                        ", ".join(sorted(set(g for g, _ in report)))))
        if n % 20 == 0:
            _write_stat(stat_path, wid, n, n_cleaned, n_passed, n_by_data, n_by_name, n_groups)
    _write_stat(stat_path, wid, n, n_cleaned, n_passed, n_by_data, n_by_name, n_groups, final=True)
    if logf:
        logf.close()


def _read_batch_stats(tmpdir):
    tot = dict(n=0, cleaned=0, passed=0, by_data=0, by_name=0, groups=0, done=0)
    for fn in os.listdir(tmpdir):
        if fn.endswith(".stat"):
            try:
                d = json.load(io.open(os.path.join(tmpdir, fn), encoding="utf-8"))
            except Exception:
                continue
            for k in ("n", "cleaned", "passed", "by_data", "by_name", "groups"):
                tot[k] += d.get(k, 0)
            tot["done"] += 1 if d.get("done") else 0
    return tot


def batch_parallel(files, srcd, dstd, hide_index, aircraft_names, workers, verbose):
    """按文件大小贪心分箱并并行处理；返回 (n, n_cleaned, n_passed, n_by_data, n_by_name, n_groups)。"""
    import multiprocessing as mp
    tmpdir = tempfile.mkdtemp(prefix="wt_clean_")
    index_path = os.path.join(tmpdir, "index.json")
    _dump_index(index_path, hide_index, aircraft_names)
    order = sorted(files, key=lambda p: -os.path.getsize(p))
    heap = [(0, i) for i in range(workers)]
    heapq.heapify(heap)
    bins = [[] for _ in range(workers)]
    for p in order:
        load, i = heapq.heappop(heap)
        bins[i].append(p)
        heapq.heappush(heap, (load + os.path.getsize(p), i))
    ctx = mp.get_context("spawn")
    procs = []
    for i in range(workers):
        procs.append(ctx.Process(target=_batch_worker_part, args=(
            bins[i], srcd, dstd, index_path,
            os.path.join(tmpdir, "w%02d.stat" % i),
            os.path.join(tmpdir, "w%02d.log" % i) if verbose else None, i)))
    for p in procs:
        p.start()
    print("并行清理：%d 个进程，共 %d 个文件，索引 %d 个模型" % (workers, len(files), len(hide_index)))
    t0 = time.time()
    last = 0.0
    while True:
        if not any(p.is_alive() for p in procs):
            break
        time.sleep(2.0)
        if time.time() - last >= 20:
            last = time.time()
            st = _read_batch_stats(tmpdir)
            print("[清理进度] %d/%d 文件（飞机 %d / 非飞机 %d，活跃进程 %d）用时 %.0fs" % (
                st["n"], len(files), st["cleaned"], st["passed"],
                sum(1 for p in procs if p.is_alive()), time.time() - t0), flush=True)
    for p in procs:
        p.join()
    bad = [p.exitcode for p in procs if p.exitcode not in (0, None)]
    if bad:
        print("[警告] 有 %d 个清理进程非正常退出：%s" % (len(bad), bad))
    st = _read_batch_stats(tmpdir)
    if verbose:
        print("逐文件剔除明细见：%s" % tmpdir)
    else:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return st["n"], st["cleaned"], st["passed"], st["by_data"], st["by_name"], st["groups"]


def main():
    ap = argparse.ArgumentParser(description="纯机体清理：剔除非机体件（游戏默认隐藏节点 + 特效面/外挂物）")
    ap.add_argument("--batch", nargs=2, metavar=("SRC_DIR", "DST_DIR"),
                    help="批量清理目录（递归），保留相对目录结构")
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help="config 解包目录（含 aces.vromfs.bin\\...\\gamedata\\flightmodels），"
                         "默认 <项目根>\\output\\config")
    ap.add_argument("--verbose", action="store_true", help="打印每个文件被剔除的组名")
    ap.add_argument("--workers", default="auto",
                    help="批处理并行进程数：auto（默认，min(CPU,8)）或 1..32；1 = 旧的串行行为")
    ap.add_argument("src", nargs="?", help="单个源 OBJ")
    ap.add_argument("dst", nargs="?", help="单个目标 OBJ")
    args = ap.parse_args()

    hide_index, aircraft_names, dirs = build_hide_index(args.config, verbose=True)
    if not aircraft_names:
        print("[警告] 未找到飞行模型 BLK（%s）：无法判定哪些资源是飞机，"
              "本次不做任何清理，全部原样复制。" % args.config)
        print("        解决办法：先执行一次「完整输出」解包配置，"
              "或用 --config 指向已解包的 config 目录。")

    if args.batch:
        srcd, dstd = args.batch
        files = glob.glob(os.path.join(srcd, "**", "*.obj"), recursive=True)   # 递归，适配 export_models 的嵌套镜像输出
        if str(args.workers).lower() == "auto":
            workers = max(1, min(os.cpu_count() or 1, 8))
        else:
            workers = max(1, int(args.workers))
        if workers > 1 and len(files) > 1:
            n, n_cleaned, n_passed, n_by_data, n_by_name, n_groups = batch_parallel(
                files, srcd, dstd, hide_index, aircraft_names, workers, args.verbose)
            print("batch done: %d files -> %s" % (n, dstd))
            print("  飞机清理 %d 个（剔除组：游戏默认隐藏 %d + 名称规则 %d，合计 %d）；"
                  "非飞机原样复制 %d 个" % (n_cleaned, n_by_data, n_by_name, n_groups, n_passed))
            return
        n = 0
        n_cleaned = n_passed = 0
        n_by_data = n_by_name = n_groups = 0
        for p in files:
            o = os.path.join(dstd, os.path.relpath(p, srcd))                   # 保留相对目录结构
            n += 1
            hide, _key = resolve_aircraft(p, hide_index)
            if hide is None:                                                   # 非机体：原样复制
                copy_through(p, o)
                n_passed += 1
                continue
            n_cleaned += 1
            report = []
            clean_one(p, o, hide_index, report, hide=hide)
            if report:
                g_data = sum(1 for _, why in report if why == "游戏默认隐藏")
                n_groups += len(report)
                n_by_data += g_data
                n_by_name += len(report) - g_data
                if args.verbose:
                    print("  %s -> 剔除 %d 组：%s" % (
                        os.path.relpath(p, srcd), len(report),
                        ", ".join(sorted(set(g for g, _ in report)))))
            if n % 50 == 0:
                print("...%d/%d" % (n, len(files)))
        print("batch done: %d files -> %s" % (n, dstd))
        print("  飞机清理 %d 个（剔除组：游戏默认隐藏 %d + 名称规则 %d，合计 %d）；"
              "非飞机原样复制 %d 个" % (n_cleaned, n_by_data, n_by_name, n_groups, n_passed))
    else:
        if not args.src or not args.dst:
            ap.error("单文件用法：_export_clean.py <src.obj> <dst.obj>；批量用法：--batch <src目录> <dst目录>")
        hide, key = resolve_aircraft(args.src, hide_index)
        if hide is None:
            copy_through(args.src, args.dst)
            print("非机体资源：原样复制（未做任何删改）-> %s" % args.dst)
            return
        print("配对机型：%s（隐藏节点 %d 个）" % (key, len(hide)))
        report = []
        v, fc = clean_one(args.src, args.dst, hide_index, report, hide=hide)
        print("kept v=%d f=%d -> %s" % (v, fc, args.dst))
        if report:
            print("剔除 %d 组：%s" % (len(report), ", ".join(sorted(set(g for g, _ in report)))))
        else:
            print("剔除 0 组")


if __name__ == "__main__":
    main()
