# to_gif.py
import os, subprocess
from PIL import Image
from rlottie_python import LottieAnimation
import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

VF = "split[a][b];[a]palettegen=reserve_transparent=1[p];[b][p]paletteuse"

for root, _, files in os.walk("stickers"):
    for f in files:
        src = os.path.join(root, f)
        name, ext = os.path.splitext(src)
        dst = name + ".gif"
        if os.path.exists(dst):
            continue
        try:
            if ext == ".webm":
                subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-c:v", "libvpx-vp9",
                "-i", src, "-vf", VF, dst], check=True)
            elif ext == ".tgs":
                LottieAnimation.from_tgs(src).save_animation(dst)
            elif ext == ".webp":
                Image.open(src).save(dst)
            else:
                continue
            print("OK ", src)
        except Exception as e:
            print("ERR", src, e)