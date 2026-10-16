#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, os, argparse, time, json, re
from queue import Empty

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 默认输出：<项目根>\output\models（本脚本位于 <项目根>\tools\models\）
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DEFAULT_OUT  = os.path.join(PROJECT_ROOT, "output", "models")
DEFAULT_WLOG = os.path.join(PROJECT_ROOT, "tools", "logs")

# ---------- 定位 grp2step.py + daelib 所在目录（默认与本脚本同目录） ----------
def find_grp_toolkit():
    r"""返回同时含 grp2step.py 与 daelib\ 的目录。
    查找顺序：环境变量 GRP_TOOLKIT -> 本脚本目录 -> 上一级 -> 上两级。"""
    env = os.environ.get("GRP_TOOLKIT")
    cands = []
    if env:
        cands.append(env)
    d = SCRIPT_DIR
    cands.append(d)
    for _ in range(2):
        d = os.path.dirname(d)
        cands.append(d)
    for c in cands:
        c = os.path.abspath(c)
        if os.path.isfile(os.path.join(c, "grp2step.py")) and os.path.isdir(os.path.join(c, "daelib")):
            return c
    raise SystemExit("找不到 grp2step.py + daelib（应与本脚本同目录）；也可设环境变量 GRP_TOOLKIT 指向其目录。")


GT = find_grp_toolkit()
sys.path.insert(0, GT)
import grp2step as g                       # noqa: E402
from parse.gameres import GameResourcePack  # noqa: E402

CLS_DYNMODEL = g.CLS_DYNMODEL
CLS_RENDINST = g.CLS_RENDINST
CLS_SKELETON = g.CLS_SKELETON

TYPES = ("tanks", "aircrafts", "ships", "cockpits", "base")

# 默认排除的场景/建筑/植被词表（按包名分词精确匹配，避免 rocket 被 rock 命中）。
SCENERY_KEYWORDS = (
    "vegetation", "tree", "trees", "bush", "bushes", "plant", "plants", "forest", "palm",
    "grass", "flower", "flowers", "cliff", "cliffs", "rock", "rocks", "stone", "stones",
    "location", "locations", "terrain", "building", "buildings", "house", "houses",
    "factory", "mall", "castle", "church", "tower", "wall", "walls", "pillar", "column",
    "fortification", "fortifications", "bridge", "bridges", "fence", "fences",
    "statue", "statues", "photogrammetry", "iceberg", "billboard", "billboards",
    "airfield", "airfields", "hangar", "hangars", "gpu", "abandoned", "decal", "decals",
    "destructible", "ruins", "debris", "road", "roads", "rail", "railway", "street",
    "ground", "snow", "sand", "chunk", "chunks", "figures", "graveyard", "cemetery",
)


def is_scenery(pack_path, keywords):
    """按包名分词精确匹配判断场景/建筑/植被（不做子串匹配）。"""
    if not keywords:
        return False
    stem = os.path.splitext(os.path.basename(pack_path))[0].lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", stem) if t]
    kws = set(keywords)
    return any(t in kws for t in tokens)

# 每 worker 的大包缓存上限（并行时每进程各一份，故取小值）
PACK_CACHE_LIMIT = 96 * 1024 * 1024


def type_of_grp(grp_path, res_root):
    """根据 .grp 文件所在相对 res_root 的目录推断类型。"""
    rel = os.path.relpath(os.path.dirname(grp_path), res_root).replace("\\", "/")
    if rel == ".":
        return "base"
    top = rel.split("/")[0]
    if top in ("tanks", "ships", "aircrafts"):
        return top
    return "base"


def collect_packs(game_dir):
    """收集全部 (路径, 类型)。game_dir 假定为 游戏安装根目录。"""
    packs = []
    base_res = os.path.join(game_dir, "content", "base", "res")
    if os.path.isdir(base_res):
        for root, _dirs, files in os.walk(base_res):
            for fn in files:
                if fn.endswith(".grp"):
                    p = os.path.join(root, fn)
                    packs.append((p, type_of_grp(p, base_res)))
    cockpits = os.path.join(game_dir, "content.hq", "pkg_cockpits", "res")
    if os.path.isdir(cockpits):
        for root, _dirs, files in os.walk(cockpits):
            for fn in files:
                if fn.endswith(".grp"):
                    packs.append((os.path.join(root, fn), "cockpits"))
    # 去重（同一路径只处理一次）
    seen = set()
    out = []
    for p, t in packs:
        key = os.path.normcase(os.path.realpath(p))
        if key not in seen:
            seen.add(key)
            out.append((p, t))
    return out


def safe_name(name):
    """资源名 -> 文件名片段（与历史版本逐字符一致，不得随意更改）。"""
    safe = name.replace("\\", "_").replace("/", "_").replace(":", "_")
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in safe)


def list_pack(pack_path):
    """打开包，返回 (grp, 目标资源名列表, 骨架名集合)。资源名顺序与包内顺序一致。"""
    grp = GameResourcePack(pack_path)
    entries = [grp.getRealResEntry(i) for i in range(len(grp.getPackedFiles()))]
    cls = lambda e: getattr(e, "_RealResEntry__classId", None)
    skel_names = {e.getName() for e in entries if cls(e) == CLS_SKELETON}
    names = [e.getName() for e in entries if cls(e) in (CLS_DYNMODEL, CLS_RENDINST)]
    return grp, names, skel_names


def export_names(grp, names, skel_names, sub_out, stem, lod, no_skip):
    """导出一批资源，返回 (ok, fail, skip, lines)。lines 为逐条结果文本（供 worker 落盘）。"""
    ok = fail = skip = 0
    lines = []
    for name in names:
        out_path = os.path.join(sub_out, "%s__%s_LOD%d.obj" % (stem, safe_name(name), lod))
        if os.path.exists(out_path) and not no_skip:
            skip += 1
            continue
        try:
            dyn = grp.getResourceByName(name)
            dyn.computeData()
            skel = None
            cand = name + "_skeleton"
            if cand in skel_names:
                try:
                    skel = grp.getResourceByName(cand)
                except Exception:
                    skel = None
            if skel is not None and hasattr(dyn, "setGeomNodeTree"):
                dyn.setGeomNodeTree(skel)
            mdl = dyn.getModel(lod)
            verts, objs = g.model_geometry(mdl)          # 每部件携带自身骨骼 wtm，写出时各自变换
            g.write_obj(out_path, verts, objs)
            nf = sum(len(o["faces"]) for o in objs)
            lines.append("OK   %-55s %8d verts %8d faces" % (
                os.path.basename(out_path), len(verts), nf))
            ok += 1
        except Exception as ex:
            lines.append("FAIL %s : %s" % (name, ex))
            fail += 1
    return ok, fail, skip, lines


def export_pack(pack_path, pack_type, out_dir, game_dir, lod, match, no_skip, want_types):
    """串行路径：导出一个 .grp 包（保持旧行为与旧日志顺序）。"""
    stem = os.path.splitext(os.path.basename(pack_path))[0]
    rel_dir = os.path.relpath(os.path.dirname(pack_path), game_dir)
    sub_out = os.path.join(out_dir, rel_dir) if rel_dir != "." else out_dir
    os.makedirs(sub_out, exist_ok=True)
    if pack_type not in want_types:
        return 0, 0, 0
    try:
        grp, names, skel_names = list_pack(pack_path)
    except Exception as ex:
        print("[跳过] 无法打开 %s : %s" % (pack_path, ex))
        return 0, 1, 0
    if match:
        names = [n for n in names if match.lower() in n.lower()]
    print("-- [%s] %s" % (pack_type, pack_path))
    ok, fail, skip, lines = export_names(grp, names, skel_names, sub_out, stem, lod, no_skip)
    for ln in lines:
        print(ln)
    return ok, fail, skip


# ===========================================================================
#  并行路径：常驻 worker + 资源级动态队列 + 大包切块
# ===========================================================================
def _now():
    return time.strftime("%H:%M:%S")


def _emit(logq, wid, pack_type, pack_path, a, b, c, lines):
    """任务结果回流父进程写主日志（daelib 的 [W] 告警不走这里，避免主日志膨胀）。"""
    head = "-- [%s] %s  (%d 个资源: 导出%d 失败%d 跳过%d)" % (
        pack_type, pack_path, a + b + c, a, b, c)
    payload = [head] + list(lines)
    step = 200
    for i in range(0, len(payload), step):
        try:
            logq.put((wid, payload[i:i + step]))
        except Exception:
            pass


def _worker_main(q, cfg, wid, pending, logq):
    """包装 worker：未捕获异常写自身日志并回报父进程，避免静默死亡。"""
    try:
        _worker_body(q, cfg, wid, pending, logq)
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            with open(os.path.join(cfg["wlog"], "w%02d.log" % wid), "a", encoding="utf-8") as f:
                f.write("[worker 未捕获异常]\n%s\n" % tb)
        except Exception:
            pass
        try:
            logq.put((wid, ["[worker %02d 异常退出] %s" % (
                wid, tb.strip().splitlines()[-1] if tb.strip() else "?")]))
        except Exception:
            pass
        raise


def _worker_body(q, cfg, wid, pending, logq):
    """退出条件：队列取空且未完成计数为 0。不能用哨兵——切块任务回队后哨兵会先被取走。"""
    log_path = os.path.join(cfg["wlog"], "w%02d.log" % wid)
    stat_path = os.path.join(cfg["wlog"], "w%02d.stat" % wid)
    logf = open(log_path, "w", encoding="utf-8", buffering=1)
    # 把子进程的 stdout/stderr 重定向到自己的日志：daelib 的告警是直接 print 的，
    # 若不重定向，16 个 worker 会同时写入父进程继承的同一个流（既交错又形成写竞争）。
    _saved = (sys.stdout, sys.stderr)
    sys.stdout = logf
    sys.stderr = logf
    cache = {"path": None, "data": None}
    ok = fail = skip = packs = 0

    def dump_stat(final=False):
        tmp = stat_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"wid": wid, "packs": packs, "ok": ok, "fail": fail,
                       "skip": skip, "done": final}, f)
        os.replace(tmp, stat_path)

    dump_stat()
    while True:
        try:
            item = q.get(timeout=0.5)
        except Empty:
            with pending.get_lock():
                if pending.value <= 0:
                    break
            continue
        if item is None:                      # 兼容显式哨兵
            break
        pack_path, pack_type, lo, hi = item
        try:
            if cache["path"] == pack_path and cache["data"] is not None:
                grp, names, skel_names = cache["data"]
            else:
                grp, names, skel_names = list_pack(pack_path)
                if os.path.getsize(pack_path) <= PACK_CACHE_LIMIT:
                    cache["path"], cache["data"] = pack_path, (grp, names, skel_names)
                else:
                    cache["path"], cache["data"] = None, None
        except Exception as ex:
            logf.write("[跳过] 无法打开 %s : %s\n" % (pack_path, ex))
            _emit(logq, wid, pack_type, pack_path, 0, 1, 0,
                  ["[跳过] 无法打开 %s : %s" % (pack_path, ex)])
            fail += 1
            packs += 1
            dump_stat()
            continue
        if cfg["match"]:
            names = [n for n in names if cfg["match"].lower() in n.lower()]
        if hi is None:
            # 大包切块回队；必须先加计数再入队，最后才扣减本份，否则计数会提前归零。
            if len(names) > cfg["split"]:
                ch = cfg["chunk"]
                chunks = [(pack_path, pack_type, k, min(k + ch, len(names)))
                          for k in range(ch, len(names), ch)]
                with pending.get_lock():
                    pending.value += len(chunks)
                for c in chunks:
                    q.put(c)
                names = names[:ch]
        else:
            names = names[lo:hi]
        stem = os.path.splitext(os.path.basename(pack_path))[0]
        rel_dir = os.path.relpath(os.path.dirname(pack_path), cfg["game_dir"])
        sub_out = os.path.join(cfg["out"], rel_dir) if rel_dir != "." else cfg["out"]
        os.makedirs(sub_out, exist_ok=True)
        a, b, c, lines = export_names(grp, names, skel_names, sub_out, stem,
                                      cfg["lod"], cfg["no_skip"])
        ok += a; fail += b; skip += c; packs += 1
        logf.write("-- [%s] %s  (%d 个资源: 导出%d 失败%d 跳过%d)\n" % (
            pack_type, pack_path, len(names), a, b, c))
        for ln in lines:
            logf.write(ln + "\n")
        with pending.get_lock():
            pending.value -= 1
        _emit(logq, wid, pack_type, pack_path, a, b, c, lines)
        dump_stat()
    dump_stat(final=True)
    sys.stdout, sys.stderr = _saved
    logf.close()


def _drain_log(logq):
    """把 worker 回报的结构化行写到控制台（即 all_unpack.ps1 的 Tee-Object 日志）。"""
    n = 0
    while True:
        try:
            _wid, lines = logq.get_nowait()
        except Empty:
            break
        except Exception:
            break
        for ln in lines:
            print(ln, flush=True)
        n += 1
    return n


def _read_stats(wlog):
    tot = {"packs": 0, "ok": 0, "fail": 0, "skip": 0, "done": 0}
    if not os.path.isdir(wlog):
        return tot
    for fn in os.listdir(wlog):
        if fn.endswith(".stat"):
            try:
                with open(os.path.join(wlog, fn), encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            for k in ("packs", "ok", "fail", "skip"):
                tot[k] += d.get(k, 0)
            tot["done"] += 1 if d.get("done") else 0
    return tot


def run_parallel(packs, out_dir, game_dir, lod, match, no_skip, workers,
                 chunk, split, wlog, quiet=False):
    """并行导出。返回 (ok, fail, skip)。packs 为 [(路径, 类型)]。"""
    import multiprocessing as mp
    os.makedirs(wlog, exist_ok=True)
    for fn in os.listdir(wlog):                    # 清理上一轮状态文件
        if fn.endswith(".stat") or fn.endswith(".stat.tmp"):
            try:
                os.remove(os.path.join(wlog, fn))
            except OSError:
                pass
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    logq = ctx.Queue()          # worker -> 父进程 的结构化日志行
    cfg = {"out": out_dir, "game_dir": game_dir, "lod": lod, "match": match,
           "no_skip": no_skip, "chunk": chunk, "split": split, "wlog": wlog}
    pending = ctx.Value("i", 0)
    with pending.get_lock():
        pending.value = len(packs)
    procs = [ctx.Process(target=_worker_main, args=(q, cfg, i, pending, logq), daemon=False)
             for i in range(workers)]
    for p in procs:
        p.start()
    for pack_path, pack_type in packs:
        q.put((pack_path, pack_type, None, None))

    total = len(packs)
    t0 = time.time()
    last = 0.0
    print("开始导出：%s  包=%d  worker=%d  输出=%s" % (
        _now(), total, workers, out_dir), flush=True)
    while True:
        alive = [p for p in procs if p.is_alive()]
        if not alive:
            break
        _drain_log(logq)
        time.sleep(0.5)
        st = _read_stats(wlog)
        if not quiet and time.time() - last >= 20:
            last = time.time()
            with pending.get_lock():
                left = pending.value
            print("[进度] %s 剩余任务 %d / 初始 %d（含大包切块）导出 %d 失败 %d 跳过 %d 活跃worker %d 用时 %.0fs" % (
                _now(), left, total, st["ok"], st["fail"], st["skip"],
                sum(1 for p in procs if p.is_alive()), time.time() - t0), flush=True)
    for p in procs:
        p.join()
    _drain_log(logq)                      # 收尾排空（worker 退出前会把消息刷出）
    st = _read_stats(wlog)
    bad = [p.exitcode for p in procs if p.exitcode not in (0, None)]
    if bad:
        print("[警告] 有 %d 个 worker 非正常退出，退出码：%s" % (len(bad), bad))
    return st["ok"], st["fail"], st["skip"]


def main():
    ap = argparse.ArgumentParser(description="Dagor .grp -> OBJ 模型导出")
    ap.add_argument("--all", action="store_true", help="导出所有类型")
    ap.add_argument("--type", default="ALL", help="tanks/aircrafts/ships/cockpits/base/ALL")
    ap.add_argument("--game-dir", default=None,
                    help="游戏安装根目录（含 content\\base\\res）；缺省则交互询问")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="OBJ 输出目录（默认 <项目根>\\output\\models）")
    ap.add_argument("--match", default=None, help="只导出名字包含该子串的模型")
    ap.add_argument("--lod", type=int, default=0, help="LOD 层级（默认 0）")
    ap.add_argument("--no-skip", action="store_true", help="强制重新导出已有输出")
    ap.add_argument("--workers", default="auto",
                    help="并行进程数：auto（默认）或 1..64；1 = 串行")
    ap.add_argument("--chunk", type=int, default=64, help="大包切片粒度（默认 64）")
    ap.add_argument("--split", type=int, default=256, help="超过该资源数的包切块分发（默认 256）")
    ap.add_argument("--packs-file", default=None, help="只导出该文件列出的包（每行一个路径）")
    ap.add_argument("--exclude-keywords", default=",".join(SCENERY_KEYWORDS),
                    help="按包名分词排除（默认场景/建筑/植被词表）；空串表示不排除")
    ap.add_argument("--no-exclude", action="store_true", help="关闭默认的场景/建筑/植被排除")
    ap.add_argument("--worker-log-dir", default=None, help="子进程日志目录")
    args = ap.parse_args()

    want = args.type.lower()
    if want == "all":
        want_types = set(TYPES)
    else:
        if want not in TYPES:
            ap.error("未知类型 %r，可选 %s/ALL" % (args.type, "/".join(TYPES)))
        want_types = {want}

    # 游戏目录：命令行缺省时交互询问，避免把某台机器的安装路径写死在脚本里
    game_dir = args.game_dir
    while not game_dir or not os.path.isdir(game_dir):
        if game_dir:
            print("[!] 目录不存在或不是目录：%s" % game_dir)
        try:
            game_dir = input("请输入游戏安装根目录: ").strip().strip('"')
        except EOFError:
            game_dir = ""
        if not game_dir:
            print("[错误] 未提供游戏目录")
            sys.exit(2)
    args.game_dir = os.path.abspath(game_dir)
    args.out = os.path.abspath(args.out)

    # worker 数量
    if str(args.workers).lower() == "auto":
        workers = max(1, min(os.cpu_count() or 1, 16) - 0)
        workers = min(workers, 16)
    else:
        workers = max(1, int(args.workers))

    wlog = args.worker_log_dir or os.path.join(
        DEFAULT_WLOG, "workers_" + time.strftime("%Y%m%d_%H%M%S"))
    wlog = os.path.abspath(wlog)

    os.makedirs(args.out, exist_ok=True)
    print("游戏目录：%s" % args.game_dir)
    print("输出目录：%s" % args.out)
    if args.packs_file:
        with open(args.packs_file, encoding="utf-8") as f:
            listed = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        packs = [(p, type_of_grp(p, os.path.join(args.game_dir, "content", "base", "res")))
                 for p in listed]
        print("按 --packs-file 限定 %d 个包" % len(packs))
    else:
        packs = collect_packs(args.game_dir)
    packs = [(p, t) for p, t in packs if t in want_types]
    kws = [] if args.no_exclude else [k.strip().lower() for k in args.exclude_keywords.split(",") if k.strip()]
    if kws:
        before = len(packs)
        skipped = [p for p, _t in packs if is_scenery(p, kws)]
        packs = [(p, t) for p, t in packs if not is_scenery(p, kws)]
        print("排除场景/建筑/植被：%d 个包（保留 %d 个）；前几个排除项：%s" % (
            before - len(packs), len(packs), ", ".join(os.path.basename(x) for x in skipped[:6])))
    print("找到 %d 个 .grp 资源包，类型过滤：%s" % (len(packs), ",".join(sorted(want_types))))
    print("并行进程数：%d" % workers)

    t0 = time.time()
    if workers == 1:
        total_ok = total_fail = total_skip = 0
        for pack_path, pack_type in packs:
            ok, fail, skip = export_pack(pack_path, pack_type, args.out, args.game_dir,
                                         args.lod, args.match, args.no_skip, want_types)
            total_ok += ok; total_fail += fail; total_skip += skip
        print("完成：导出 %d，失败 %d，跳过 %d" % (total_ok, total_fail, total_skip))
    else:
        print("子进程日志：%s" % wlog)
        total_ok, total_fail, total_skip = run_parallel(
            packs, args.out, args.game_dir, args.lod, args.match, args.no_skip,
            workers, args.chunk, args.split, wlog)
        print("完成：导出 %d，失败 %d，跳过 %d" % (total_ok, total_fail, total_skip))
    print("用时：%.1f s" % (time.time() - t0))
    if args.match:
        print("仅前 8 个命中文件写入各 worker 日志；总计见上。")
    if total_fail:
        print("失败明细见：%s" % wlog)


if __name__ == "__main__":
    main()
