import ast, os, sys
from collections import defaultdict

ROOT = "src"
files = []
for dp, _, fns in os.walk(ROOT):
    for fn in fns:
        if fn.endswith(".py"):
            files.append(os.path.join(dp, fn))

def max_depth(node, d=0):
    deepest = d
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try,
                              ast.AsyncFor, ast.AsyncWith, ast.FunctionDef,
                              ast.AsyncFunctionDef)):
            deepest = max(deepest, max_depth(child, d+1))
        else:
            deepest = max(deepest, max_depth(child, d))
    return deepest

stats = []
for path in files:
    src = open(path, encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    lines = src.splitlines()
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    file_max_depth = 0
    longest_func = (0, "")
    func_lengths = []
    for f in funcs:
        end = getattr(f, "end_lineno", f.lineno)
        flen = end - f.lineno + 1
        func_lengths.append(flen)
        if flen > longest_func[0]:
            longest_func = (flen, f.name)
        file_max_depth = max(file_max_depth, max_depth(f, 0))
    # bare except / broad except
    bare = sum(1 for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.type is None)
    broad = sum(1 for n in ast.walk(tree)
                if isinstance(n, ast.ExceptHandler) and isinstance(n.type, ast.Name) and n.type.id in ("Exception","BaseException"))
    todo = sum(1 for ln in lines if any(k in ln for k in ("TODO","FIXME","XXX","HACK")))
    nfunc = len(funcs)
    avg = sum(func_lengths)/nfunc if nfunc else 0
    stats.append({
        "path": path, "loc": len(lines), "nfunc": nfunc,
        "maxfunc": longest_func, "avgfunc": round(avg,1),
        "depth": file_max_depth, "bare": bare, "broad": broad, "todo": todo,
    })

print("=== 文件级复杂度 (按最大函数长度降序 top25) ===")
for s in sorted(stats, key=lambda x: -x["maxfunc"][0])[:25]:
    print(f"{s['maxfunc'][0]:5d}行  函数数{s['nfunc']:3d}  均长{s['avgfunc']:6.1f}  最深{s['depth']}  bare{s['bare']} broad{s['broad']} todo{s['todo']}  {s['path']}  (最长:{s['maxfunc'][1]})")

print("\n=== 嵌套深度 >= 6 的文件 (潜在难读) ===")
for s in sorted(stats, key=lambda x: -x["depth"]):
    if s["depth"] >= 6:
        print(f"  深度{s['depth']}  {s['path']}")

print("\n=== bare/宽泛 except 信号 ===")
tot_bare = sum(s['bare'] for s in stats); tot_broad = sum(s['broad'] for s in stats)
print(f"  裸 except: {tot_bare}  宽泛 Exception: {tot_broad}")
for s in sorted(stats, key=lambda x: -(x['bare']+x['broad'])):
    if (s['bare']+s['broad']) >= 3:
        print(f"  bare{s['bare']} broad{s['broad']}  {s['path']}")

print("\n=== 整体汇总 ===")
tot_loc = sum(s['loc'] for s in stats); tot_f = sum(s['nfunc'] for s in stats)
print(f"  扫描文件 {len(stats)}  总 LOC {tot_loc}  总函数 {tot_f}  平均函数长 {tot_loc/tot_f:.1f}")
over100 = [s for s in stats if s['maxfunc'][0] > 100]
print(f"  含 >100 行函数的文件: {len(over100)}")
over200 = [s for s in stats if s['maxfunc'][0] > 200]
print(f"  含 >200 行函数的文件: {len(over200)}  -> {[s['path'] for s in over200]}")
