#!/usr/bin/env python3
"""Sinh sơ đồ class 3 cột (Model / View / Template) từ một file spec JSON.

Bố cục giống mẫu 認証_クラス図.png:
  cột trái  : エンティティ(データ) Model     — class entity (thuộc tính / phương thức)
  cột giữa  : コントローラ View              — mỗi view: tên / url / GET, POST
  cột phải  : バウンダリ(画面) Template      — tên file template, ngang hàng với view
  góc phải  : ■メモ
  mũi tên   : view ..> template (nét đứt), view -> view「リダイレクト」(nét liền),
              view ..> entity (nét đứt, nhãn = tên phương thức entity được dùng)

Cách dùng:
  python gen_class_diagram.py spec.json -o out/認証_クラス図
      -> out/認証_クラス図.drawio  (mở bằng draw.io / diagrams.net)
      -> out/認証_クラス図.png     (ảnh xem trước, cần Pillow + font CJK)
  thêm --no-png để bỏ ảnh xem trước

Spec JSON (xem references/samples/認証_クラス図.json):
{
  "title": "認証",
  "entities": [
    {"id": "user", "name": "ユーザーアカウント",
     "attributes": ["利用者ID(PK)", "ログインID"],
     "methods": ["登録する(): void", "ログインIDで検索する(): ユーザーアカウント"]}
  ],
  "views": [
    {"id": "register_view", "url": "/register",
     "get": "新規登録画面を表示する", "post": "入力を検証してアカウントを登録する",
     "template": "register.html",
     "redirects": ["register_complete_view"],
     "uses": [{"entity": "user", "label": "ログインIDで検索 登録"}]}
  ],
  "memo": ["シーケンス図でビューから伸びている線は、…"]
}
  - "name" của view mặc định = "id". "get"/"post" bỏ trống -> in "GET:" / "POST:".
  - "extra": ["DELETE: …"] thêm dòng phương thức khác nếu cần.
  - Thứ tự "views" = thứ tự từ trên xuống; đặt view đích của リダイレクト ngay dưới view nguồn.
"""
import argparse
import html
import json
import math
import os
import subprocess
import sys
import unicodedata
from xml.sax.saxutils import quoteattr

# ---- kích thước bố cục -------------------------------------------------------
FS = 11            # cỡ chữ các dòng
FS_HEAD = 12       # cỡ chữ tiêu đề class
HEAD = 26          # chiều cao phần tên class
ROW = 20           # chiều cao một dòng
SEP = 8            # chiều cao đường kẻ ngăn
PAD = 14           # lề chữ hai bên khi ước lượng độ rộng
COL_PAD = 30       # lề trong của cột
COL_TITLE = 34     # chiều cao vùng tiêu đề cột
GAP_V = 40         # khoảng cách dọc giữa các view
TPL_H = 30
TOP = 10
LEFT = 10
COL_TITLES = {
    "entity": "エンティティ(データ) Model",
    "view": "コントローラ View",
    "template": "バウンダリ(画面) Template",
}


def text_w(s, fs=FS):
    w = 0.0
    for ch in s:
        w += fs if unicodedata.east_asian_width(ch) in "WFA" else fs * 0.6
    return w


def member(s):
    s = s.strip()
    return s if s[:1] in "+-#~" else "+ " + s


# ---- tính bố cục -------------------------------------------------------------
def layout(spec):
    ents = spec.get("entities", [])
    views = spec.get("views", [])
    vid = {v["id"]: v for v in views}
    eid = {e["id"]: e for e in ents}
    for v in views:
        v.setdefault("name", v["id"])
        rows = ["GET: " + (v.get("get") or ""), "POST: " + (v.get("post") or "")]
        rows += v.get("extra", [])
        v["_rows"] = rows
        v["_h"] = HEAD + ROW + SEP + ROW * len(rows)
        for r in v.get("redirects", []):
            if r not in vid:
                sys.exit(f"[lỗi] view '{v['id']}' redirect tới '{r}' không có trong views")
        for u in v.get("uses", []):
            if u["entity"] not in eid:
                sys.exit(f"[lỗi] view '{v['id']}' dùng entity '{u['entity']}' không có trong entities")
    for e in ents:
        e["_attrs"] = [member(a) for a in e.get("attributes", [])]
        e["_meths"] = [member(m) for m in e.get("methods", [])]
        e["_h"] = HEAD + ROW * max(1, len(e["_attrs"])) + SEP + ROW * max(1, len(e["_meths"]))

    view_w = max([170] + [text_w(v["name"], FS_HEAD) + 2 * PAD for v in views]
                 + [text_w(r) + 2 * PAD for v in views for r in v["_rows"] + ["url: " + v.get("url", "")]])
    tpl_w = max([140] + [text_w(v["template"]) + 2 * PAD for v in views if v.get("template")])
    ent_w = max([200] + [text_w(e["name"], FS_HEAD) + 2 * PAD for e in ents]
                + [text_w(r) + 2 * PAD for e in ents for r in e["_attrs"] + e["_meths"]])
    view_w, tpl_w, ent_w = (int(math.ceil(x / 10.0) * 10) for x in (view_w, tpl_w, ent_w))

    col_e_x = LEFT
    col_e_w = ent_w + 2 * COL_PAD
    col_v_x = col_e_x + col_e_w + 80
    col_v_w = view_w + 2 * COL_PAD
    col_t_x = col_v_x + col_v_w + 70
    col_t_w = tpl_w + 2 * COL_PAD
    memo_x = col_t_x + col_t_w + 40
    memo_w = 270

    # view: xếp dọc theo thứ tự
    y = TOP + COL_TITLE + 10
    for v in views:
        v["_x"], v["_y"], v["_w"] = col_v_x + COL_PAD, y, view_w
        y += v["_h"] + GAP_V
        if v.get("template"):
            cy = v["_y"] + HEAD + ROW / 2
            v["_tpl"] = (col_t_x + COL_PAD, cy - TPL_H / 2, tpl_w, TPL_H)

    # entity: đặt gần trung bình vị trí các view dùng nó, rồi đẩy xuống nếu chồng nhau
    for e in ents:
        ys = [v["_y"] + HEAD + ROW / 2 for v in views
              for u in v.get("uses", []) if u["entity"] == e["id"]]
        want = (sum(ys) / len(ys)) if ys else TOP + COL_TITLE + 80
        e["_want"] = want - e["_h"] / 2
        e["_x"], e["_w"] = col_e_x + COL_PAD, ent_w
    cur = TOP + COL_TITLE + 10
    for e in sorted(ents, key=lambda e: e["_want"]):
        e["_y"] = max(cur, e["_want"])
        cur = e["_y"] + e["_h"] + 50

    bottom = max([v["_y"] + v["_h"] for v in views] + [e["_y"] + e["_h"] for e in ents] + [TOP + 200])
    col_h = bottom + 30 - TOP

    memo = spec.get("memo", [])
    memo_lines = 1
    for line in memo:
        memo_lines += max(1, math.ceil(text_w("・" + line) / (memo_w - 16)))
    memo_h = int(memo_lines * 15 + 20)

    return {
        "views": views, "ents": ents, "vid": vid, "eid": eid,
        "cols": [
            ("col_entity", COL_TITLES["entity"], col_e_x, TOP, col_e_w, col_h),
            ("col_view", COL_TITLES["view"], col_v_x, TOP, col_v_w, col_h),
            ("col_template", COL_TITLES["template"], col_t_x, TOP, col_t_w, col_h),
        ],
        "memo": (memo_x, TOP + 10, memo_w, memo_h, memo),
        "width": memo_x + memo_w + 20,
        "height": TOP + col_h + 20,
    }


def edges(L):
    """Trả về danh sách cạnh với toạ độ tuyệt đối (dùng chung cho drawio và PNG)."""
    out = []
    views, vid, eid = L["views"], L["vid"], L["eid"]
    idx = {v["id"]: i for i, v in enumerate(views)}
    k = 0
    for i, v in enumerate(views):
        x, y, w, h = v["_x"], v["_y"], v["_w"], v["_h"]
        ry = HEAD + ROW / 2
        if v.get("template"):
            tx, ty, tw, th = v["_tpl"]
            out.append(dict(kind="template", src=v["id"], dst="t_" + v["id"],
                            pts=[(x + w, y + ry), (tx, y + ry)],
                            exit=(1, ry / h), entry=(0, 0.5), label=""))
        for u in v.get("uses", []):
            e = eid[u["entity"]]
            sx, sy = x, y + ry
            ex = e["_x"] + e["_w"]
            cx, cy = e["_x"] + e["_w"] / 2, e["_y"] + e["_h"] / 2
            t = (ex - sx) / (cx - sx) if cx != sx else 0
            iy = sy + t * (cy - sy)
            iy = min(max(iy, e["_y"] + e["_h"] * 0.05), e["_y"] + e["_h"] * 0.95)
            out.append(dict(kind="uses", src=v["id"], dst="e_" + e["id"],
                            pts=[(sx, sy), (ex, iy)],
                            exit=(0, ry / h), entry=(1, (iy - e["_y"]) / e["_h"]),
                            label=u.get("label", "")))
        for r in v.get("redirects", []):
            t = vid[r]
            if idx[r] == i + 1:
                out.append(dict(kind="redirect", src=v["id"], dst=r,
                                pts=[(x + w / 2, y + h), (t["_x"] + t["_w"] / 2, t["_y"])],
                                exit=(0.5, 1), entry=(0.5, 0), label="リダイレクト", way=[]))
            else:
                rx = x + w + 14 + 8 * k
                k += 1
                sy, tyy = y + h * 0.8, t["_y"] + t["_h"] * 0.2
                out.append(dict(kind="redirect", src=v["id"], dst=r,
                                pts=[(x + w, sy), (rx, sy), (rx, tyy), (t["_x"] + t["_w"], tyy)],
                                exit=(1, 0.8), entry=(1, 0.2), label="リダイレクト",
                                way=[(rx, sy), (rx, tyy)]))
    return out


# ---- draw.io -----------------------------------------------------------------
def esc(s):
    return html.escape(s, quote=False)


def cell(cid, value, style, x, y, w, h, parent="1"):
    return (f'<mxCell id={quoteattr(cid)} value={quoteattr(value)} style={quoteattr(style)} '
            f'vertex="1" parent={quoteattr(parent)}>'
            f'<mxGeometry x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" as="geometry"/></mxCell>')


ST_COL = ("rounded=0;whiteSpace=wrap;html=1;fillColor=none;verticalAlign=top;align=left;"
          "spacingLeft=8;spacingTop=4;fontStyle=1;fontSize=12;")
ST_CLASS = ("swimlane;fontStyle=1;align=center;verticalAlign=top;childLayout=stackLayout;horizontal=1;"
            f"startSize={HEAD};horizontalStack=0;resizeParent=1;resizeParentMax=0;resizeLast=0;"
            f"collapsible=0;marginBottom=0;html=1;whiteSpace=wrap;fillColor=#ffffff;fontSize={FS_HEAD};")
ST_ROW = ("text;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;spacingLeft=4;spacingRight=4;"
          f"overflow=hidden;rotatable=0;points=[[0,0.5],[1,0.5]];portConstraint=eastwest;whiteSpace=wrap;html=1;fontSize={FS};")
ST_LINE = ("line;strokeWidth=1;fillColor=none;align=left;verticalAlign=middle;spacingTop=-1;spacingLeft=3;"
           "spacingRight=3;rotatable=0;labelPosition=right;points=[];portConstraint=eastwest;strokeColor=inherit;")
ST_TPL = f"rounded=0;whiteSpace=wrap;html=1;fillColor=#ffffff;fontSize={FS};"
ST_MEMO = (f"rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=top;spacing=8;fontSize={FS};"
           "fillColor=#ffffff;")
ST_EDGE = {
    "template": "endArrow=open;endFill=0;dashed=1;html=1;rounded=0;",
    "uses": "endArrow=open;endFill=0;dashed=1;html=1;rounded=0;labelBackgroundColor=#ffffff;fontSize=10;",
    "redirect": "endArrow=block;endFill=1;html=1;rounded=0;labelBackgroundColor=#ffffff;fontSize=10;",
}


def class_cells(cid, name, sections, x, y, w):
    """sections: list các list dòng; giữa các section có đường kẻ."""
    h = HEAD + sum(ROW * max(1, len(s)) for s in sections) + SEP * (len(sections) - 1)
    out = [cell(cid, esc(name), ST_CLASS, x, y, w, h)]
    cy = HEAD
    n = 0
    for si, rows in enumerate(sections):
        for r in (rows or [""]):
            out.append(cell(f"{cid}_r{n}", esc(r), ST_ROW, 0, cy, w, ROW, parent=cid))
            cy += ROW
            n += 1
        if si < len(sections) - 1:
            out.append(cell(f"{cid}_s{si}", "", ST_LINE, 0, cy, w, SEP, parent=cid))
            cy += SEP
    return out


def to_drawio(spec, L):
    title = spec.get("title", "クラス図")
    c = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    for cid, t, x, y, w, h in L["cols"]:
        c.append(cell(cid, esc(t), ST_COL, x, y, w, h))
    for e in L["ents"]:
        c += class_cells("e_" + e["id"], e["name"], [e["_attrs"], e["_meths"]], e["_x"], e["_y"], e["_w"])
    for v in L["views"]:
        c += class_cells(v["id"], v["name"], [["url: " + v.get("url", "")], v["_rows"]], v["_x"], v["_y"], v["_w"])
        if v.get("template"):
            tx, ty, tw, th = v["_tpl"]
            c.append(cell("t_" + v["id"], esc(v["template"]), ST_TPL, tx, ty, tw, th))
    mx, my, mw, mh, memo = L["memo"]
    if memo:
        body = "<b>■メモ</b><br>" + "<br>".join("・" + esc(m) for m in memo)
        c.append(cell("memo", body, ST_MEMO, mx, my, mw, mh))
    for n, ed in enumerate(edges(L)):
        st = ST_EDGE[ed["kind"]]
        st += "exitX={};exitY={:.3f};exitDx=0;exitDy=0;entryX={};entryY={:.3f};entryDx=0;entryDy=0;".format(
            ed["exit"][0], ed["exit"][1], ed["entry"][0], ed["entry"][1])
        if ed.get("way"):
            st += "edgeStyle=orthogonalEdgeStyle;"
            pts = "".join(f'<mxPoint x="{px:.0f}" y="{py:.0f}"/>' for px, py in ed["way"])
            geo = f'<mxGeometry relative="1" as="geometry"><Array as="points">{pts}</Array></mxGeometry>'
        else:
            geo = '<mxGeometry relative="1" as="geometry"/>'
        c.append(f'<mxCell id="edge{n}" value={quoteattr(esc(ed["label"]))} style={quoteattr(st)} edge="1" '
                 f'parent="1" source={quoteattr(ed["src"])} target={quoteattr(ed["dst"])}>{geo}</mxCell>')
    return ('<mxfile host="app.diagrams.net" type="device">\n'
            f'  <diagram id="class-diagram" name={quoteattr(title)}>\n'
            f'    <mxGraphModel dx="{L["width"]}" dy="{L["height"]}" grid="1" gridSize="10" guides="1" '
            'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            f'pageWidth="{max(1169, L["width"])}" pageHeight="{max(827, L["height"])}" math="0" shadow="0">\n'
            '      <root>\n        ' + "\n        ".join(c) + '\n      </root>\n'
            '    </mxGraphModel>\n  </diagram>\n</mxfile>\n')


# ---- ảnh xem trước PNG ------------------------------------------------------------
def find_font(bold=False):
    cands = []
    try:
        pat = "Noto Sans CJK JP" + (":bold" if bold else "")
        r = subprocess.run(["fc-match", "-f", "%{file}|%{index}", pat], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and "|" in r.stdout:
            f, i = r.stdout.split("|", 1)
            cands.append((f, int(i or 0)))
    except Exception:
        pass
    for f in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/msgothic.ttc",
              "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc", "/Library/Fonts/Arial Unicode.ttf"]:
        cands.append((f, 0))
    return cands


def to_png(spec, L, path, scale=1.5):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[bỏ qua PNG] chưa có Pillow (pip install pillow)")
        return False

    def load(size, bold=False):
        for f, i in find_font(bold):
            if os.path.exists(f):
                try:
                    return ImageFont.truetype(f, int(size * scale), index=i)
                except Exception:
                    continue
        return None

    f_row, f_head, f_lbl = load(FS), load(FS_HEAD, True), load(10)
    if not f_row:
        print("[bỏ qua PNG] không tìm thấy font CJK")
        return False
    f_head = f_head or f_row
    s = lambda v: v * scale
    W, H = int(s(L["width"])), int(s(L["height"]))
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    BLK = (0, 0, 0)

    def rect(x, y, w, h, fill=None):
        d.rectangle([s(x), s(y), s(x + w), s(y + h)], outline=BLK, fill=fill, width=1)

    def text(x, y, t, font, anchor="lm"):
        d.text((s(x), s(y)), t, fill=BLK, font=font, anchor=anchor)

    def dline(p, q, dashed):
        if not dashed:
            d.line([s(p[0]), s(p[1]), s(q[0]), s(q[1])], fill=BLK, width=1)
            return
        L_ = math.hypot(q[0] - p[0], q[1] - p[1])
        if L_ == 0:
            return
        ux, uy = (q[0] - p[0]) / L_, (q[1] - p[1]) / L_
        t = 0.0
        while t < L_:
            t2 = min(t + 4, L_)
            d.line([s(p[0] + ux * t), s(p[1] + uy * t), s(p[0] + ux * t2), s(p[1] + uy * t2)], fill=BLK, width=1)
            t += 7

    def arrow(p, q, filled):
        ang = math.atan2(q[1] - p[1], q[0] - p[0])
        a1 = (q[0] - 9 * math.cos(ang - 0.4), q[1] - 9 * math.sin(ang - 0.4))
        a2 = (q[0] - 9 * math.cos(ang + 0.4), q[1] - 9 * math.sin(ang + 0.4))
        if filled:
            d.polygon([(s(q[0]), s(q[1])), (s(a1[0]), s(a1[1])), (s(a2[0]), s(a2[1]))], fill=BLK)
        else:
            d.line([s(a1[0]), s(a1[1]), s(q[0]), s(q[1]), s(a2[0]), s(a2[1])], fill=BLK, width=1)

    for cid, t, x, y, w, h in L["cols"]:
        rect(x, y, w, h)
        text(x + 8, y + 14, t, f_head)

    def klass(name, sections, x, y, w):
        hh = HEAD + sum(ROW * max(1, len(sec)) for sec in sections) + SEP * (len(sections) - 1)
        rect(x, y, w, hh, fill="white")
        text(x + w / 2, y + HEAD / 2, name, f_head, "mm")
        cy = y + HEAD
        d.line([s(x), s(cy), s(x + w), s(cy)], fill=BLK)
        for si, rows in enumerate(sections):
            for r in (rows or [""]):
                text(x + 6, cy + ROW / 2, r, f_row)
                cy += ROW
            if si < len(sections) - 1:
                d.line([s(x), s(cy + SEP / 2), s(x + w), s(cy + SEP / 2)], fill=BLK)
                cy += SEP

    for e in L["ents"]:
        klass(e["name"], [e["_attrs"], e["_meths"]], e["_x"], e["_y"], e["_w"])
    for v in L["views"]:
        klass(v["name"], [["url: " + v.get("url", "")], v["_rows"]], v["_x"], v["_y"], v["_w"])
        if v.get("template"):
            tx, ty, tw, th = v["_tpl"]
            rect(tx, ty, tw, th, fill="white")
            text(tx + tw / 2, ty + th / 2, v["template"], f_row, "mm")
    mx, my, mw, mh, memo = L["memo"]
    if memo:
        rect(mx, my, mw, mh, fill="white")
        yy = my + 14
        text(mx + 8, yy, "■メモ", f_head)
        for m in memo:
            line, first = "", True
            for ch in "・" + m:
                if text_w(line + ch) > mw - 18:
                    yy += 15
                    text(mx + 8, yy, line, f_row)
                    line = ch
                else:
                    line += ch
            yy += 15
            text(mx + 8, yy, line, f_row)
    for ed in edges(L):
        pts = ed["pts"]
        dashed = ed["kind"] != "redirect"
        for a, b in zip(pts, pts[1:]):
            dline(a, b, dashed)
        arrow(pts[-2], pts[-1], filled=not dashed)
        if ed["label"]:
            if len(pts) == 2:
                lx, ly = (pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2
            else:
                lx, ly = pts[1][0], (pts[1][1] + pts[2][1]) / 2
            bb = d.textbbox((s(lx), s(ly)), ed["label"], font=f_lbl, anchor="mm")
            d.rectangle([bb[0] - 2, bb[1] - 1, bb[2] + 2, bb[3] + 1], fill="white")
            d.text((s(lx), s(ly)), ed["label"], fill=BLK, font=f_lbl, anchor="mm")
    img.save(path)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("-o", "--out", help="đường dẫn đầu ra, không có đuôi (mặc định: cạnh file spec)")
    ap.add_argument("--no-png", action="store_true", help="không xuất ảnh xem trước")
    a = ap.parse_args()
    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)
    out = a.out or os.path.splitext(a.spec)[0]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    L = layout(spec)
    with open(out + ".drawio", "w", encoding="utf-8") as f:
        f.write(to_drawio(spec, L))
    print("drawio:", out + ".drawio")
    if not a.no_png and to_png(spec, L, out + ".png"):
        print("png   :", out + ".png")


if __name__ == "__main__":
    main()
