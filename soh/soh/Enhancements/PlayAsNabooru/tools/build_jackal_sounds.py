"""Builds the jackals' voice for the Play as Nabooru mod: howl, barks and a dying whine, cut from field recordings of
black-backed jackals on Wikimedia Commons, into a mod archive for SoH's `mods` folder.

  python build_jackal_sounds.py [--out x64/Debug/mods/jackal_sounds.o2r] [--cache DIR] [--preview DIR]

Needs ffmpeg on the PATH. The recordings are downloaded once into --cache (default: ~/oot-fast64/jackal/sounds/src).
--preview writes a spectrogram of every clip, for checking where a clip starts and ends without ears.

The recordings are licensed CC BY-SA 4.0, so the clips are too: the archive carries a CREDITS.txt, and the recordings
and clips stay out of this repository. Without the archive the jackals borrow the Wolfos' voice (EnJackal.cpp).

The game plays the clips with ModSound.cpp: any .wav works (mono or stereo, any rate), these are 16-bit mono at the
game's own 32 kHz.
"""
import argparse
import struct
import subprocess
import urllib.parse
import urllib.request
import wave
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
FOLDER = "sounds/jackal"  # where the game looks for the clips
RATE = 32000
COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/"

# recording -> (file on Wikimedia Commons, author, licence)
RECORDINGS = {
    "dusk": ("Lupulella mesomelas, roep met skemer, Skeerpoort, 2022-01-01, a.mp3", "JMK", "CC BY-SA 4.0"),
    "howling": ("Lupulella mesomelas & Corvus albus, 2022-09-24 18h25, Dinokeng, a.mp3", "JMK", "CC BY-SA 4.0"),
}

# clip -> (recording, start, end in seconds, what it is). The yelps of "dusk" are four calls of one animal with
# nothing over them; "howling" has several animals, of which the first two howls stand alone.
CLIPS = {
    "howl": ("howling", 4.22, 6.70, "a long howl: a jackal that has noticed someone"),
    "bark1": ("dusk", 5.52, 6.02, "yelps: threatening, attacking and (played higher) hurt"),
    "bark2": ("dusk", 6.33, 6.66, ""),
    "bark3": ("dusk", 6.81, 7.20, ""),
    "bark4": ("dusk", 7.37, 7.78, ""),
    "whine": ("howling", 7.52, 8.92, "a short falling howl: dying"),
}
# what is kept of a recording: the calls lie between these frequencies, wind and insects mostly outside
BAND = (450, 5000)
FADE_IN, FADE_OUT = 0.012, 0.07
PEAK = 0.85  # of full scale, for every clip


def fetch(name, cache):
    """-> the recording's file, downloaded if it is not there yet"""
    title = RECORDINGS[name][0]
    path = cache / (name + Path(title).suffix)
    if not path.exists() or path.stat().st_size < 10000:
        cache.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(COMMONS + urllib.parse.quote(title),
                                         headers={"User-Agent": "jackal-sounds/1.0"})
        with urllib.request.urlopen(request) as response:
            path.write_bytes(response.read())
        print(f"downloaded {title}")
    return path


def cut(source, start, end):
    """-> the part of a recording as 16-bit mono samples at the game's rate, filtered, faded and brought to PEAK"""
    length = end - start
    filters = (f"highpass=f={BAND[0]}:poles=2,highpass=f={BAND[0]}:poles=2,lowpass=f={BAND[1]},"
               f"afade=t=in:st=0:d={FADE_IN},afade=t=out:st={length - FADE_OUT:.3f}:d={FADE_OUT}")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", f"{length:.3f}", "-i", str(source),
                          "-ac", "1", "-ar", str(RATE), "-af", filters, "-f", "s16le", "-"],
                         check=True, capture_output=True).stdout
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    gain = PEAK * 32767 / max(1, max(abs(s) for s in samples))
    return [max(-32767, min(32767, round(s * gain))) for s in samples]


def wav_bytes(samples, path):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path.read_bytes()


def credits():
    lines = ["The jackals' voice (Play as Nabooru mod)", "",
             "Clips cut from recordings of black-backed jackals (Lupulella mesomelas) on Wikimedia Commons;",
             "filtered, faded and normalised. The clips are licensed like the recordings.", ""]
    for name, (title, author, licence) in RECORDINGS.items():
        clips = ", ".join(clip + ".wav" for clip, (recording, *_rest) in CLIPS.items() if recording == name)
        lines += [f"{clips}:", f"  \"{title}\" by {author}, {licence}",
                  "  https://commons.wikimedia.org/wiki/File:" + urllib.parse.quote(title.replace(" ", "_")), ""]
    lines += ["CC BY-SA 4.0: https://creativecommons.org/licenses/by-sa/4.0/", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=REPO / "x64" / "Debug" / "mods" / "jackal_sounds.o2r")
    ap.add_argument("--cache", type=Path, default=Path.home() / "oot-fast64" / "jackal" / "sounds" / "src")
    ap.add_argument("--preview", type=Path, help="write the clips and a spectrogram of each into this folder")
    args = ap.parse_args()

    work = args.preview or args.cache / "clips"
    work.mkdir(parents=True, exist_ok=True)
    files = {f"{FOLDER}/CREDITS.txt": credits().encode()}
    for name, (recording, start, end, _what) in CLIPS.items():
        samples = cut(fetch(recording, args.cache), start, end)
        path = work / (name + ".wav")
        files[f"{FOLDER}/{name}.wav"] = wav_bytes(samples, path)
        print(f"{name}: {len(samples) / RATE:.2f} s")
        if args.preview:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-lavfi",
                            "showspectrumpic=s=700x300:legend=1:scale=log:stop=6000", str(work / (name + ".png"))],
                           check=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(files.items()):
            archive.writestr(path, data)
    print(f"{args.out}: {len(files)} files")


main()
