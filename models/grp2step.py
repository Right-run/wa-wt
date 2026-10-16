#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, os, argparse, io

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 默认输出：<项目根>\output\models（本脚本位于 <项目根>\tools\models\）
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.dirname(SCRIPT_DIR)), "output", "models")


def find_dae():
    env = os.environ.get("DAE_ROOT")
    cands = []
    if env:
        cands += [os.path.join(env, "src", "dae"), env]
    for root in (SCRIPT_DIR, os.path.dirname(SCRIPT_DIR),
                 os.path.join(os.path.dirname(SCRIPT_DIR), "research")):
        cands += [os.path.join(root, "daelib", "src", "dae"), os.path.join(root, "daelib")]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            cands += [os.path.join(meipass, "daelib", "src", "dae"),
                      os.path.join(meipass, "daelib")]
    for c in cands:
        if os.path.isdir(os.path.join(c, "parse")) and os.path.isdir(os.path.join(c, "util")):
            return c
    raise SystemExit("找不到 daelib 解析库：请把 daelib 放到脚本旁，或用环境变量 DAE_ROOT 指向其所在目录")


DAE = find_dae()
sys.path.insert(0, DAE)

from parse.gameres import GameResourcePack  # noqa: E402

CLS_SKELETON = 0x56F81B6D
CLS_DYNMODEL = 0xB4B7D9C4
CLS_RENDINST = 0x77F8232F
CLS_COLLISION = 0xACE50000
CLS_FASTPHYS = 0x855A1BE6
CLS_PHYSOBJ = 0xD543E771
CLS_FX = 0x88B7A117

CLASS_NAMES = {
    CLS_SKELETON: "Skeleton(骨架)", CLS_DYNMODEL: "DynModel(网格模型)",
    CLS_RENDINST: "RendInst(静态网格)", CLS_COLLISION: "CollisionGeom(碰撞)",
    CLS_FASTPHYS: "FastPhys(物理代理)", CLS_PHYSOBJ: "PhysObj(物理体)",
    CLS_FX: "FX(特效)",
}


def first_stream_vert_count(dyn, lod):
    """返回主(第一个被追加的) MatVData 顶点流的顶点数。
    模型里通常只有一个主顶点流（各刚性部件按元素范围共享它）；
    少数部件会引用第 2 条流（如 a6m3 的 prop02/03 里 4 个三角面），
    这些“二级流”的顶点是按【模型根空间】直接摆放的（如尾部的贴片/标记），
    不能跟着部件骨骼一起变换，否则会被甩到机身外面。"""
    mvd = getattr(dyn, 'mvd', None)
    if mvd is None:
        return None
    try:
        if lod is None or getattr(lod, 'shaderMesh', None) is None:
            return None
        mvd.computeData()
        seen = []
        for shaderMesh in lod.shaderMesh:
            for e in shaderMesh.elems:
                if e.vData not in seen:
                    seen.append(e.vData)
        if not seen:
            return None
        vd0 = mvd.getVertexData(seen[0])
        verts, _ = vd0.getVertices(), vd0.getUVs()
        return len(verts) if verts is not None else None
    except Exception:
        return None


def raw_first_for(dyn, lod_idx):
    """按模型类型尽量取出 LOD 数据并求主流顶点数；取不到时返回 None（等价旧行为）。"""
    try:
        lods = None
        for name in ('_DynModel__lods', '_RendInst__lods', '__lods', '_SkinnedMeshResource__lods'):
            lods = getattr(dyn, name, None)
            if lods is not None:
                break
        if lods is None or lod_idx is None or lod_idx < 0 or lod_idx >= len(lods):
            lod = None
        else:
            lod = lods[lod_idx]
        return first_stream_vert_count(dyn, lod)
    except Exception:
        return None


def xform(wtm, v):
    """行主序 3x4 矩阵仿射变换 + 全局手性修正（Z 取反）。
    Dagor 模型数据整体是左手系（实测：每个 rigid 骨骼 wtm 的 det 均为 -1，且导出件
    歼-15T 加油管/航炮与实机左右相反），而 OBJ/STEP 查看器是右手系；
    输出时对每个部件做 Z 镜像可恢复真实左右手性（det=-1 的骨骼矩阵与镜像相抵，
    面绕序在写出处统一翻转以保持法线朝外）。"""
    return (wtm[0][0]*v[0] + wtm[0][1]*v[1] + wtm[0][2]*v[2] + wtm[0][3],
            wtm[1][0]*v[0] + wtm[1][1]*v[1] + wtm[1][2]*v[2] + wtm[1][3],
            -(wtm[2][0]*v[0] + wtm[2][1]*v[1] + wtm[2][2]*v[2] + wtm[2][3]))


def mirror_raw(v):
    """对未变换顶点（蒙皮件/无骨骼件，引擎空间坐标）施加同一 Z 镜像。"""
    return (v[0], v[1], -v[2])


def model_geometry(mdl, raw_first=None):
    """返回 (原始顶点表, objs[])。每个 obj 携带自己的 wtm（刚性部件骨骼世界矩阵）或 None。
    关键修正：顶点【不再】全局预变换。
    引擎对每个 rigid 节点独立变换：左右对称件常引用同一段顶点流（vData 缓存跨 rigid 共享），
    旧逻辑“顶点归第一个引用它的部件”会把两份部件变换到同一侧/错误位置
    （光电探头偏侧、尾椎过高均源于此）。现在每个部件写出时用自己的骨骼矩阵变换自己那份副本，
    与引擎 per-rigid 渲染一致；蒙皮部件保持原始顶点（蒙皮由引擎渲染时计算）。
    raw_first 参数保留但不再参与判断（历史残留）。注意：不做 Z 轴翻转，避免镜像。"""
    skeleton = getattr(mdl, "skeleton", None)
    verts = [tuple(mdl.getVertex(i)) for i in range(mdl.vertCnt)]
    objs = []
    for o in mdl:
        wtm = None
        if skeleton is not None and not o.skinned:
            node = skeleton.getNodeByName(o.name)
            if node is not None:
                wtm = node.wtm
        objs.append({"name": o.name, "faces": [tuple(f) for f in o.faces], "wtm": wtm})
    return verts, objs


def part_points(verts, o):
    """返回 {全局索引: 变换后坐标}：该部件实际用到的顶点，按部件自身骨骼变换（含 Z 镜像）。"""
    wtm = o.get("wtm")
    used = set()
    for f in o["faces"]:
        used.update(f)
    return {i: (xform(wtm, verts[i]) if wtm is not None else mirror_raw(verts[i])) for i in used}


def write_obj(path, verts, objs):
    """写 OBJ，保证每个零件互相拓扑独立：
    - 每个子部件(g 组)写出【自己的一套顶点】并应用自己的骨骼 wtm 变换；
      共享顶点流的对称部件各自变换到自己应在的一侧/位置。
    - 零件之间不共享顶点索引，导入后不会被焊接成一整个。
    - 兼容旧格式 objs（无 'wtm' 字段时视为已预变换，直接写 verts）。"""
    # 拼接后一次写出；格式串与逐行写出相同，字节结果不变。
    out = ["# exported by grp2step.py (Dagor .grp -> OBJ)\n",
           "# 每零件独立顶点+自身骨骼变换，共享顶点流不再串位\n"]
    seen = {}
    vert_offset = 0  # 已写出的顶点总数（OBJ 里顶点索引是全局递增的）
    for o in objs:
        name = (o.get("name") or "").strip() or "part"
        faces = o["faces"]
        if len(faces) == 0:
            continue
        pts = part_points(verts, o) if "wtm" in o else None
        # 收集该零件实际用到的顶点（按出现顺序去重），旧全局索引 -> 本零件内局部索引
        remap = {}
        local = []
        for face in faces:
            for idx in face:
                if idx not in remap:
                    remap[idx] = len(local)
                    local.append(idx)
        # 组名去重，避免同名零件在查看器里被归类成一个
        if name in seen:
            seen[name] += 1
            name = "%s_%02d" % (name, seen[name])
        else:
            seen[name] = 0
        out.append("g %s\n" % name)
        if pts is None:
            for idx in local:
                v = verts[idx]
                out.append("v %.6f %.6f %.6f\n" % (v[0], v[1], v[2]))
        else:
            for idx in local:
                v = pts[idx]
                out.append("v %.6f %.6f %.6f\n" % (v[0], v[1], v[2]))
        off = vert_offset
        for a, b, c in faces:
            # Z 镜像后翻转绕序，保持法线朝外
            out.append("f %d %d %d\n" % (off + remap[a] + 1,
                                          off + remap[c] + 1,
                                          off + remap[b] + 1))
        vert_offset += len(local)
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(out))
    return path


def main():
    ap = argparse.ArgumentParser(description="Dagor .grp 资源包提取/转换")
    ap.add_argument("grp", help="输入 .grp (GRP2) 文件")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="输出目录（默认 <项目根>\\output\\models）")
    ap.add_argument("--list", action="store_true", help="仅列出内部资源后退出")
    ap.add_argument("--obj", action="store_true", help="导出 OBJ（默认 LOD0 满精度）")
    ap.add_argument("--lod", type=int, default=None, help="OBJ 使用的 LOD（默认 0）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    g = GameResourcePack(args.grp)
    entries = [g.getRealResEntry(i) for i in range(len(g.getPackedFiles()))]

    print("== %s  (%d 个内部资源)" % (os.path.basename(args.grp), len(entries)))
    for i, e in enumerate(entries):
        cid = e._RealResEntry__classId
        print("  [%d] %-40s class=0x%08X  %s" % (i, e.getName(), cid, CLASS_NAMES.get(cid, "未知")))

    if args.list:
        return

    dyn_entry = next((e for e in entries if e._RealResEntry__classId == CLS_DYNMODEL), None) \
        or next((e for e in entries if e._RealResEntry__classId == CLS_RENDINST), None)
    if dyn_entry is None:
        print("警告：未找到 DynModel/RendInst 网格资源")
        return

    # 骨架按“模型名 + _skeleton”配对；多模型包里绝不能用包的第一个骨架套到别的模型上
    def skel_for(model_name):
        name = model_name + "_skeleton"
        for e in entries:
            if e._RealResEntry__classId == CLS_SKELETON and e.getName() == name:
                return e
        return None

    skel_entry = skel_for(dyn_entry.getName())
    if skel_entry:
        skel = g.getResourceByName(skel_entry.getName())   # 骸架仅供 OBJ 部件世界变换使用

    dyn = g.getResourceByName(dyn_entry.getName())
    # 注意顺序：computeData() 内部会用 AssetCacher 查骨架并覆盖 setGeomNodeTree，
    # 缓存里没有时骨架会变成 None，导致部件不做骨骼变换、叠成一团；
    # 因此先 computeData()，再手动挂骨架（DynModel 才有；RendInst 没有此方法）。
    dyn.computeData()
    if skel_entry and hasattr(dyn, "setGeomNodeTree"):
        dyn.setGeomNodeTree(g.getResourceByName(skel_entry.getName()))
    print("== 模型 %s  LOD 数: %d" % (dyn_entry.getName(), dyn.lodCount))

    if args.obj:
        lod = args.lod if args.lod is not None else 0
        mdl = dyn.getModel(lod)
        verts, objs = model_geometry(mdl)
        p = write_obj(os.path.join(args.out, dyn_entry.getName() + "_LOD%d.obj" % lod), verts, objs)
        print("OBJ  :", p, "(%d 顶点, %d 面)" % (len(verts), sum(len(o['faces']) for o in objs)))


if __name__ == "__main__":
    main()
