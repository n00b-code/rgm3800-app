"""Generate the app icon (.icns) from a drawn 1024px master.

Run:  python scripts/make_icon.py
Produces rgm3800app/assets/icon.png (preview) and icon.icns (for py2app).
Requires Pillow and macOS `iconutil`.
"""
import os, subprocess
from PIL import Image, ImageDraw

S = 1024
BLUE = (43, 108, 212, 255)
BLUE2 = (30, 84, 174, 255)
WHITE = (255, 255, 255, 255)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# Squircle-ish rounded background with a soft vertical gradient.
radius = 232
grad = Image.new("RGBA", (1, S))
for y in range(S):
    t = y / S
    r = int(BLUE[0] + (BLUE2[0]-BLUE[0])*t)
    g = int(BLUE[1] + (BLUE2[1]-BLUE[1])*t)
    b = int(BLUE[2] + (BLUE2[2]-BLUE[2])*t)
    grad.putpixel((0, y), (r, g, b, 255))
grad = grad.resize((S, S))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S-1, S-1], radius=radius, fill=255)
img.paste(grad, (0, 0), mask)

# GPS track: a smooth white poly-line with rounded joints + waypoint dots.
pts = [(232, 742), (388, 560), (548, 648), (676, 452), (812, 300)]
d.line(pts, fill=WHITE, width=72, joint="curve")
for x, y in pts:
    r = 36
    d.ellipse([x-r, y-r, x+r, y+r], fill=WHITE)

# Start dot: white ring.
sx, sy = pts[0]
d.ellipse([sx-58, sy-58, sx+58, sy+58], fill=WHITE)
d.ellipse([sx-26, sy-26, sx+26, sy+26], fill=BLUE)

# End: map marker (white disc with hole).
ex, ey = pts[-1]
R = 104
d.ellipse([ex-R, ey-R, ex+R, ey+R], fill=WHITE)
d.ellipse([ex-46, ey-46, ex+46, ey+46], fill=BLUE)

png = "rgm3800app/assets/icon.png"
img.save(png)
print("wrote", png)

# Build .iconset with all required sizes, then icns via iconutil.
iconset = "rgm3800app/assets/icon.iconset"
os.makedirs(iconset, exist_ok=True)
specs = [(16,1),(16,2),(32,1),(32,2),(128,1),(128,2),(256,1),(256,2),(512,1),(512,2)]
for base, scale in specs:
    px = base * scale
    name = f"icon_{base}x{base}{'@2x' if scale==2 else ''}.png"
    img.resize((px, px), Image.LANCZOS).save(os.path.join(iconset, name))
subprocess.run(["iconutil", "-c", "icns", iconset, "-o",
                "rgm3800app/assets/icon.icns"], check=True)
print("wrote rgm3800app/assets/icon.icns")
