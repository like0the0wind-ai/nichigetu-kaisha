import numpy as np
from PIL import Image
from moviepy import VideoClip
from moviepy.video.fx import FadeIn, FadeOut

FPS = 30
DURATION = 4.0
W, H = 1080, 1920

DARK  = (80, 40, 10)
LIGHT = (210, 140, 60)

def make_frame(t):
    scale = 1.0 + 0.05 * (t / DURATION)
    frame = np.zeros((H, W, 3), dtype=np.float32)
    for c in range(3):
        frame[:, :, c] = np.linspace(DARK[c], LIGHT[c], H)[:, None]

    cx, cy = W / 2, H / 2
    xv, yv = np.meshgrid(np.linspace(-1,1,W), np.linspace(-1,1,H))
    vignette = 1 - np.clip(xv**2 + yv**2, 0, 1) * 0.55
    for c in range(3):
        frame[:, :, c] *= vignette

    flicker = 1.0 + 0.025 * np.sin(t * 2.8)
    frame = np.clip(frame * flicker, 0, 255).astype(np.uint8)

    cw = int(W / scale); ch = int(H / scale)
    ox = (W - cw) // 2;  oy = (H - ch) // 2
    zoomed = np.array(Image.fromarray(frame[oy:oy+ch, ox:ox+cw]).resize((W, H), Image.LANCZOS))
    return zoomed

clip = VideoClip(make_frame, duration=DURATION)
clip = clip.with_effects([FadeIn(0.5), FadeOut(0.5)])
clip.write_videofile("bakery_4sec.mp4", fps=FPS, codec="libx264", audio=False, logger=None)
print("Done → bakery_4sec.mp4")
