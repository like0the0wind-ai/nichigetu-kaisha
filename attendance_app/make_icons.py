from PIL import Image, ImageDraw, ImageFont
import os

out = os.path.join(os.path.dirname(__file__), "static")

for size in [192, 512]:
    img = Image.new("RGB", (size, size), "#c87941")
    d = ImageDraw.Draw(img)
    margin = size // 8
    d.ellipse([margin, margin, size-margin, size-margin], fill="#f5ede0")
    font_size = size // 4
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msgothic.ttc", font_size)
    except Exception:
        font = ImageFont.load_default(font_size)
    text = "打"
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
    d.text(((size-tw)//2, (size-th)//2), text, fill="#c87941", font=font)
    path = os.path.join(out, f"icon-{size}.png")
    img.save(path)
    print(f"Saved {path}")
