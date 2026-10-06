"""Export the approved minimal SVG with existing Pillow, without network/dependencies.

This deliberately accepts only the current M/L/l/H/h/Z paths, rounded rectangle and
two-stop object-bounding-box linear gradient; unsupported artwork fails closed.
"""
from pathlib import Path
import hashlib, json, re, xml.etree.ElementTree as ET
from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent
SOURCE = BASE / "app-icon.svg"
root = ET.parse(SOURCE).getroot()
assert root.attrib["viewBox"] == "0 0 256 256"
NS = "{http://www.w3.org/2000/svg}"
SCALE = 4

def color(value):
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", value)
    return tuple(bytes.fromhex(value[1:]))

def polygon(data):
    tokens = re.findall(r"[MmLlHhZz]|-?\d+(?:\.\d+)?", data)
    assert re.sub(r"[MmLlHhZz\s,]|-?\d+(?:\.\d+)?", "", data) == ""
    points, cursor, command, i = [], (0., 0.), None, 0
    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]; i += 1
            if command in "Zz": break
        assert command in ("M", "m", "L", "l", "H", "h")
        if command in "Hh":
            x, y = float(tokens[i]), cursor[1]; i += 1
            if command == "h": x += cursor[0]
        else:
            x, y = float(tokens[i]), float(tokens[i+1]); i += 2
            if command.islower(): x, y = x+cursor[0], y+cursor[1]
        cursor = (x, y); points.append(cursor)
        if command == "M": command = "L"
        elif command == "m": command = "l"
    return points

image = Image.new("RGBA", (256*SCALE, 256*SCALE))
rect = root.find(NS+"rect")
assert rect.attrib == {"x":"0", "y":"0", "width":"256", "height":"256", "rx":"55", "fill":"#191e26"}
ImageDraw.Draw(image).rounded_rectangle((0, 0, 256*SCALE-1, 256*SCALE-1), radius=55*SCALE, fill=color(rect.attrib["fill"])+(255,))
for path in root.findall(NS+"path"):
    points = polygon(path.attrib["d"])
    mask = Image.new("L", image.size)
    opacity = round(float(path.attrib.get("opacity", "1"))*255)
    ImageDraw.Draw(mask).polygon([(round(x*SCALE),round(y*SCALE)) for x,y in points], fill=opacity)
    fill = path.attrib["fill"]
    if fill == "url(#v)":
        stops = root.find(NS+"defs").find(NS+"linearGradient").findall(NS+"stop")
        first, last = color(stops[0].attrib["stop-color"]), color(stops[1].attrib["stop-color"])
        x0,x1=min(x for x,y in points),max(x for x,y in points)
        y0,y1=min(y for x,y in points),max(y for x,y in points)
        layer = Image.new("RGBA", image.size); pixels=layer.load()
        for y in range(image.height):
            for x in range(image.width):
                t=max(0.,min(1.,((x/SCALE-x0)/(x1-x0)+(y/SCALE-y0)/(y1-y0))/2))
                pixels[x,y]=tuple(round(a+(b-a)*t) for a,b in zip(first,last))+(255,)
    else: layer=Image.new("RGBA", image.size,color(fill)+(255,))
    layer.putalpha(mask); image=Image.alpha_composite(image,layer)
image=image.resize((256,256),Image.Resampling.LANCZOS)
image.save(BASE/"app-icon.png")
sizes=[16,24,32,48,64,128,256]
image.save(BASE/"app-icon.ico",sizes=[(s,s) for s in sizes])
report={"source":"app-icon.svg","source_sha256":hashlib.sha256(SOURCE.read_bytes()).hexdigest(),"sizes":sizes,"method":"4x supersampled SVG geometry export; Pillow " + __import__('PIL').__version__,"outputs":{f:hashlib.sha256((BASE/f).read_bytes()).hexdigest() for f in ["app-icon.png","app-icon.ico"]}}
(BASE/"icon-export.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps(report))
