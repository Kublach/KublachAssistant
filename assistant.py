r"""
Kublach Assistant - lik koji se javi na desktopu svakih X minuta.

Struktura mapa (pokraj skripte ili .exe datoteke):
    characters/
        tray.png            (opcionalno) ikona za system tray
        bubu/
            voda.gif        slika/animacija scene
            voda.txt        tekst koji ide uz tu sliku   (opcionalno)
            voda.mp3        zvuk koji ide uz tu sliku    (opcionalno, .wav/.mp3/.ogg)
            zadano.mp3      zvuk za scene bez vlastitog zvuka (opcionalno)
            poruke.txt      poruke za scene bez vlastitog .txt (jedna po retku)
            poruke-hr.txt   poruke na hrvatskom  -> u izboru jezika se pojavi HR
            poruke-en.txt   poruke na engleskom  -> EN  (bilo koji kod od 2-3 slova)
            voda-en.txt     tekst scene na određenom jeziku (opcionalno)
        dudu/
            ... isto ...
        pancake/            svaka nova mapa = novi lik u dropdownu

Scena = datoteke s ISTIM imenom (voda.gif + voda.txt + voda.mp3).

Mapa characters/ i app.ico ugrađeni su u .exe. Pri svakom pokretanju kopiraju se
u %APPDATA%\KublachAssistant\ - datoteke istog imena se pregaze, ostale ostaju.
Aplikacija koristi samo tu kopiju, pa tamo možeš dodavati likove bez novog builda.

Potrebno: pip install pillow pystray pygame
"""
import json
import shutil
import os
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path

from PIL import Image, ImageDraw, ImageSequence, ImageTk

try:
    import pystray
except ImportError:
    pystray = None

try:
    import pygame
    pygame.mixer.init()
    HAS_PYGAME = True
except Exception:
    HAS_PYGAME = False

try:
    import winsound
except ImportError:
    winsound = None

# RES_DIR: ugrađeni resursi (u .exe-u se raspakiraju u privremenu mapu _MEIPASS)
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
# DATA_DIR: korisnički podaci i postavke
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "KublachAssistant"
BUNDLED_CHARS = RES_DIR / "characters"
USER_CHARS = DATA_DIR / "characters"
CHAR_ROOTS = [USER_CHARS]
CONFIG_FILE = DATA_DIR / "config.json"
ICON_FILE = DATA_DIR / "app.ico"


def sync_resources():
    """Kopiraj ugrađene resurse u AppData: isto ime = pregazi, ostalo ne diraj."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if BUNDLED_CHARS.is_dir():
            shutil.copytree(BUNDLED_CHARS, USER_CHARS, dirs_exist_ok=True)
        if (RES_DIR / "app.ico").exists():
            shutil.copy2(RES_DIR / "app.ico", ICON_FILE)
    except Exception as e:
        print("Greška pri kopiranju resursa:", e)
DEFAULT_CHARACTERS = ["bubu", "dudu"]
IMG_EXT = (".gif", ".png", ".webp")
SND_EXT = (".wav", ".mp3", ".ogg")
TRANSP = "#ff00fe"
TRANSP_RGB = (255, 0, 254, 255)
IMG_SIZE = 180

DEFAULT_MESSAGES = [
    "Popij malo vode!",
    "Vrijeme je za kratku pauzu.",
    "Ustani i protegni se malo!",
    "Odmori oči - pogledaj u daljinu 20 sekundi.",
    "Samo polako, ide ti super!",
    "Ispravi leđa!",
]


# ---------- učitavanje scena ----------

def get_characters():
    """Svaka podmapa u characters/ (ugrađenoj ili u AppData) je jedan lik."""
    names = set()
    for root in CHAR_ROOTS:
        if root.exists():
            names.update(p.name for p in root.iterdir() if p.is_dir())
    return sorted(names)


def char_dirs(character):
    return [r / character for r in CHAR_ROOTS if (r / character).is_dir()]


def find_resource(name):
    """Prva postojeća datoteka među korisničkim pa ugrađenim resursima."""
    for root in CHAR_ROOTS:
        if (root / name).exists():
            return root / name
    return None


def find_sound(base: Path):
    for ext in SND_EXT:
        p = base.with_suffix(ext)
        if p.exists():
            return p
    return None


LANG_RE = re.compile(r"^(?P<stem>.+)-(?P<lang>[A-Za-z]{2,3})$")


def get_languages(character):
    """Jezici = kodovi iz poruke-XX.txt i <scena>-XX.txt datoteka."""
    langs = set()
    for folder in char_dirs(character):
        img_stems = {p.stem for p in folder.iterdir() if p.suffix.lower() in IMG_EXT}
        for f in folder.glob("*.txt"):
            m = LANG_RE.match(f.stem)
            if m and (m["stem"] == "poruke" or m["stem"] in img_stems):
                langs.add(m["lang"].lower())
    return sorted(langs)


def read_text(base: Path, lang):
    """Tekst za <base>-<lang>.txt, a ako ne postoji, <base>.txt."""
    candidates = [base.parent / f"{base.stem}-{lang}.txt"] if lang else []
    candidates.append(base.with_suffix(".txt"))
    for f in candidates:
        if f.exists():
            t = f.read_text(encoding="utf-8").strip()
            if t:
                return t
    return None


def load_scenes(character, lang=None):
    scenes, seen = [], set()
    for folder in char_dirs(character):
        for img in sorted(folder.iterdir()):
            if img.suffix.lower() not in IMG_EXT or img.stem in seen:
                continue
            seen.add(img.stem)   # ista scena u AppData zamjenjuje ugrađenu
            scenes.append({"image": img, "text": read_text(img, lang), "sound": find_sound(img)})
    return scenes


def default_sound(character):
    for folder in char_dirs(character):
        s = find_sound(folder / "zadano")
        if s:
            return s
    return None


def load_messages(character, lang=None):
    names = ([f"poruke-{lang}.txt"] if lang else []) + ["poruke.txt"]
    files = [folder / n for n in names for folder in char_dirs(character)]
    for f in files:
        if not f.exists():
            continue
        lines = [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        if lines:
            return lines
    return DEFAULT_MESSAGES


def load_frames(path: Path):
    img = Image.open(path)
    frames = []
    for frame in ImageSequence.Iterator(img):
        f = frame.convert("RGBA")
        f.thumbnail((IMG_SIZE, IMG_SIZE))
        bg = Image.new("RGBA", f.size, TRANSP_RGB)
        mask = f.split()[3].point(lambda a: 255 if a > 128 else 0)
        bg.paste(f, mask=mask)
        frames.append((ImageTk.PhotoImage(bg.convert("RGB")), frame.info.get("duration", 100) or 100))
    return frames


# ---------- zvuk ----------

_current_sound = None

def play_sound(path):
    global _current_sound
    if not path:
        return
    try:
        if HAS_PYGAME:
            _current_sound = pygame.mixer.Sound(str(path))  # čuvamo referencu da ne prekine
            _current_sound.play()
        elif winsound and path.suffix.lower() == ".wav":
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as e:
        print("Greška sa zvukom:", e)


# ---------- skočni prozor ----------

class Popup(tk.Toplevel):
    def __init__(self, master, character, scene, message, seconds):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=TRANSP)
        try:
            self.attributes("-transparentcolor", TRANSP)
        except tk.TclError:
            pass

        tk.Label(self, text=message, bg="#fff8e7", fg="#333", font=("Segoe UI", 11),
                 wraplength=220, justify="left", padx=12, pady=8, relief="solid", bd=1).pack(pady=(0, 4))

        self.frames = load_frames(scene["image"]) if scene else None
        if self.frames:
            self.img_label = tk.Label(self, bg=TRANSP, bd=0)
            self.img_label.pack()
            self.frame_idx = 0
            self.animate()
        else:
            tk.Label(self, text=f"(dodaj sliku u\ncharacters/{character}/)", bg="#fff8e7",
                     font=("Segoe UI", 9)).pack()

        for w in [self] + list(self.winfo_children()):
            w.bind("<Button-1>", lambda e: self.close())

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.x, self.target_y, self.y = sw - w - 20, sh - h - 60, sh
        self.geometry(f"+{self.x}+{self.y}")
        self.slide_in()
        self.after(seconds * 1000, self.close)

    def animate(self):
        if not self.winfo_exists():
            return
        img, delay = self.frames[self.frame_idx]
        self.img_label.configure(image=img)
        self.frame_idx = (self.frame_idx + 1) % len(self.frames)
        if len(self.frames) > 1:
            self.after(delay, self.animate)

    def slide_in(self):
        if self.y > self.target_y:
            self.y = max(self.target_y, self.y - 15)
            self.geometry(f"+{self.x}+{self.y}")
            self.after(10, self.slide_in)

    def close(self):
        if self.winfo_exists():
            self.destroy()


# ---------- glavna aplikacija ----------

class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()          # naredbe iz tray threada -> tkinter
        root.title("Kublach Assistant")
        if ICON_FILE.exists():
            try:
                root.iconbitmap(default=str(ICON_FILE))
            except tk.TclError:
                pass
        root.resizable(False, False)

        cfg = self.load_config()
        # "obični" atributi - čita ih i tray thread
        self.char = cfg.get("character", "bubu")
        self.chars = get_characters()
        if self.char not in self.chars and self.chars:
            self.char = self.chars[0]
        self.sound_enabled = cfg.get("sound", True)
        self.lang_pref = cfg.get("language")      # željeni jezik (pamti se i kad ga lik nema)
        self.langs = get_languages(self.char)
        self.random_enabled = cfg.get("random", False)
        self.running = False
        self.last_scene = None
        self.popup = None
        self.job = None
        self.next_time = None

        self.v_char = tk.StringVar(value=self.char)
        self.v_interval = tk.IntVar(value=cfg.get("interval", 30))
        self.v_show = tk.IntVar(value=cfg.get("show_secs", 8))
        self.v_sound = tk.BooleanVar(value=self.sound_enabled)
        self.v_interval_max = tk.IntVar(value=cfg.get("interval_max", 60))
        self.v_random = tk.BooleanVar(value=self.random_enabled)
        self.v_lang = tk.StringVar()

        pad = {"padx": 10, "pady": 4}
        tk.Label(root, text="Character:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", **pad)
        self.combo = ttk.Combobox(root, textvariable=self.v_char, values=self.chars,
                                  state="readonly", width=16, postcommand=self.rescan_chars)
        self.combo.grid(row=0, column=1, columnspan=2, sticky="w", **pad)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.set_char(self.v_char.get()))

        tk.Label(root, text="Language:").grid(row=1, column=0, sticky="w", **pad)
        self.lang_combo = ttk.Combobox(root, textvariable=self.v_lang, state="readonly", width=8)
        self.lang_combo.grid(row=1, column=1, sticky="w", **pad)
        self.lang_combo.bind("<<ComboboxSelected>>", lambda e: self.set_lang(self.v_lang.get().lower()))

        tk.Label(root, text="Show every (min):").grid(row=2, column=0, sticky="w", **pad)
        tk.Spinbox(root, from_=1, to=240, textvariable=self.v_interval, width=5).grid(row=2, column=1, **pad)
        rnd = tk.Frame(root)
        rnd.grid(row=2, column=2, sticky="w", **pad)
        tk.Checkbutton(rnd, text="randomly till", variable=self.v_random,
                       command=lambda: self.set_random(self.v_random.get())).pack(side="left")
        self.max_spin = tk.Spinbox(rnd, from_=1, to=480, textvariable=self.v_interval_max, width=5)
        self.max_spin.pack(side="left")

        tk.Label(root, text="Show (sec):").grid(row=3, column=0, sticky="w", **pad)
        tk.Spinbox(root, from_=3, to=60, textvariable=self.v_show, width=5).grid(row=3, column=1, **pad)
        tk.Checkbutton(root, text="Sound", variable=self.v_sound,
                       command=lambda: self.set_sound(self.v_sound.get())).grid(row=3, column=2, sticky="w", **pad)

        tk.Button(root, text="Start", width=10, command=self.start).grid(row=4, column=0, **pad)
        tk.Button(root, text="Pause", width=10, command=self.stop).grid(row=4, column=1, **pad)
        tk.Button(root, text="Show now", width=10, command=self.show).grid(row=4, column=2, **pad)
        self.status = tk.Label(root, text="", fg="#666")
        self.status.grid(row=5, column=0, columnspan=3, **pad)
        tk.Label(root, text="Kublach, 2026.").grid(row=6, column=0, sticky="e", **pad)
        tk.Button(root, text="Exit", width=10, command=self.quit).grid(row=6, column=2, sticky="e", **pad)

        self.update_lang_combo()
        self.update_random_ui()

        # X sakrije prozor u tray (ako tray postoji), inače minimizira
        root.protocol("WM_DELETE_WINDOW", self.hide_window)

        self.icon = None
        self.setup_tray()
        self.start()
        self.update_status()
        self.poll_queue()
        self.auto_rescan()

    # ----- tray -----
    def setup_tray(self):
        if not pystray:
            return
        def cmd(fn):                 # tray callback -> stavi u red za tkinter
            return lambda icon, item: self.q.put(fn)

        menu = pystray.Menu(
            pystray.MenuItem("Show now", cmd(self.show), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Character", pystray.Menu(lambda: [
                pystray.MenuItem(c, cmd(lambda c=c: self.set_char(c)),
                                 checked=lambda item, c=c: self.char == c, radio=True)
                for c in self.chars])),
            pystray.MenuItem("Language", pystray.Menu(lambda: [
                pystray.MenuItem(l.upper(), cmd(lambda l=l: self.set_lang(l)),
                                 checked=lambda item, l=l: self.lang == l, radio=True)
                for l in self.langs] or [pystray.MenuItem("(nema)", lambda: None, enabled=False)])),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Randomly", cmd(lambda: self.set_random(not self.random_enabled)),
                             checked=lambda item: self.random_enabled),
            pystray.MenuItem("Sound", cmd(lambda: self.set_sound(not self.sound_enabled)),
                             checked=lambda item: self.sound_enabled),
            pystray.MenuItem(lambda item: "Pause" if self.running else "Continue",
                             cmd(lambda: self.stop() if self.running else self.start())),
            pystray.MenuItem("Settings", cmd(self.show_window)),
            pystray.MenuItem("Exit", cmd(self.quit)),
        )
        self.icon = pystray.Icon("kublach_assistant", self.tray_image(), "Kublach Assistant", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def tray_image(self):
        p = find_resource("tray.png")
        if p:
            return Image.open(p)
        if ICON_FILE.exists():
            return Image.open(ICON_FILE)
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill=(245, 190, 130, 255))
        d.ellipse((20, 24, 27, 31), fill=(40, 40, 40, 255))
        d.ellipse((37, 24, 44, 31), fill=(40, 40, 40, 255))
        d.arc((22, 30, 42, 46), 20, 160, fill=(40, 40, 40, 255), width=3)
        return img

    def refresh_tray(self):
        if self.icon:
            self.icon.update_menu()

    def poll_queue(self):
        while not self.q.empty():
            try:
                self.q.get_nowait()()
            except Exception as e:
                print("Greška:", e)
        self.root.after(100, self.poll_queue)

    # ----- postavke -----
    def load_config(self):
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_config(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps({
                "character": self.char, "interval": self.v_interval.get(),
                "show_secs": self.v_show.get(), "sound": self.sound_enabled,
                "language": self.lang_pref, "random": self.random_enabled,
                "interval_max": self.v_interval_max.get()}), encoding="utf-8")
        except Exception:
            pass

    def rescan_chars(self):
        """Ponovno pročitaj mape likova (nova mapa = novi lik)."""
        chars = get_characters()
        if chars != self.chars:
            self.chars = chars
            self.combo["values"] = chars
            if self.char not in chars and chars:
                self.set_char(chars[0])
            self.refresh_tray()
        langs = get_languages(self.char)           # novi poruke-XX.txt = novi jezik
        if langs != self.langs:
            self.langs = langs
            self.update_lang_combo()
            self.refresh_tray()

    def auto_rescan(self):
        self.rescan_chars()
        self.root.after(15000, self.auto_rescan)

    @property
    def lang(self):
        """Stvarni jezik: željeni ako ga lik ima, inače prvi dostupni."""
        if self.lang_pref in self.langs:
            return self.lang_pref
        return self.langs[0] if self.langs else None

    def update_lang_combo(self):
        self.lang_combo["values"] = [l.upper() for l in self.langs]
        if self.langs:
            self.lang_combo.config(state="readonly")
            self.v_lang.set(self.lang.upper())
        else:
            self.v_lang.set("-")
            self.lang_combo.config(state="disabled")

    def set_lang(self, l):
        self.lang_pref = l
        self.update_lang_combo()
        self.save_config()
        self.refresh_tray()

    def update_random_ui(self):
        self.max_spin.config(state="normal" if self.random_enabled else "disabled")

    def set_random(self, on):
        self.random_enabled = bool(on)
        self.v_random.set(self.random_enabled)
        self.update_random_ui()
        if self.running:
            self.start()          # odmah primijeni novi način
        else:
            self.save_config()
        self.refresh_tray()

    def set_char(self, c):
        self.char = c
        self.v_char.set(c)
        self.last_scene = None
        self.langs = get_languages(c)
        self.update_lang_combo()
        self.save_config()
        self.refresh_tray()

    def set_sound(self, on):
        self.sound_enabled = bool(on)
        self.v_sound.set(self.sound_enabled)
        self.save_config()
        self.refresh_tray()

    # ----- tajmer -----
    def start(self):
        self.stop()
        self.save_config()
        a = max(1, self.v_interval.get())
        if self.random_enabled:
            b = max(1, self.v_interval_max.get())
            lo, hi = min(a, b), max(a, b)
            ms = random.randint(lo * 60, hi * 60) * 1000
        else:
            ms = a * 60 * 1000
        self.next_time = time.time() + ms / 1000
        self.job = self.root.after(ms, self.tick)
        self.running = True
        self.refresh_tray()

    def stop(self):
        if self.job:
            self.root.after_cancel(self.job)
        self.job, self.next_time, self.running = None, None, False
        self.refresh_tray()

    def tick(self):
        self.show()
        self.start()

    # ----- prikaz scene -----
    def pick_scene(self):
        scenes = load_scenes(self.char, self.lang)
        if not scenes:
            return None
        choices = [s for s in scenes if s["image"] != self.last_scene] or scenes
        scene = random.choice(choices)
        self.last_scene = scene["image"]
        return scene

    def show(self):
        if self.popup and self.popup.winfo_exists():
            self.popup.close()
        scene = self.pick_scene()
        message = (scene and scene["text"]) or random.choice(load_messages(self.char, self.lang))
        self.popup = Popup(self.root, self.char, scene, message, max(3, self.v_show.get()))
        if self.sound_enabled:
            sound = (scene and scene["sound"]) or default_sound(self.char)
            play_sound(sound)

    # ----- prozor -----
    def update_status(self):
        if self.next_time and self.random_enabled:
            a, b = self.v_interval.get(), self.v_interval_max.get()
            self.status.config(text=f"{self.char.capitalize()} will show randomly "
                                    f"({min(a, b)}-{max(a, b)} min) - surprise!")
        elif self.next_time:
            left = max(0, int(self.next_time - time.time()))
            self.status.config(text=f"{self.char.capitalize()} will show in {left // 60}:{left % 60:02d}")
        else:
            self.status.config(text="Pause")
        self.root.after(1000, self.update_status)

    def hide_window(self):
        self.root.withdraw() if self.icon else self.root.iconify()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()

    def quit(self):
        self.save_config()
        if self.icon:
            self.icon.stop()
        self.root.destroy()


if __name__ == "__main__":
    sync_resources()
    if not get_characters():
        for c in DEFAULT_CHARACTERS:
            (USER_CHARS / c).mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    App(root)
    root.mainloop()
