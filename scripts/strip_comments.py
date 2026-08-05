import io
import pathlib
import tokenize

root = pathlib.Path("src/wb_mcp")
n = 0
for p in sorted(root.rglob("*.py")):
    src = p.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    cmts = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(src).readline):
            if t.type == tokenize.COMMENT:
                cmts.append((t.start[0], t.start[1]))
    except tokenize.TokenError:
        pass
    for row, col in sorted(cmts, reverse=True):
        i = row - 1
        if i >= len(lines):
            continue
        line = lines[i]
        if line[:col].strip() == "":
            del lines[i]
        else:
            lines[i] = line[:col].rstrip() + "\n"
    new = "".join(lines)
    if new != src:
        p.write_text(new, encoding="utf-8", newline="")
        n += 1
print("stripped", n, "files")