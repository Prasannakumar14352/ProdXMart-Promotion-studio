"""An original eight-bar instrumental cue, synthesized offline for Studio Reels."""
from array import array
from functools import lru_cache
from math import exp, pi, sin, tanh
from random import Random
import sys
import wave

RATE = 24000
BEAT = 60 / 128
SECONDS = 15


@lru_cache(maxsize=96)
def tone(midi, beats, bass=False):
    frequency = 440 * 2 ** ((midi - 69) / 12)
    duration = beats * BEAT
    samples = array('f')
    for i in range(round(duration * RATE)):
        t = i / RATE
        phase = 2 * pi * frequency * t
        envelope = min(1, t / 0.009) * min(1, (duration - t) / 0.08)
        envelope *= exp(-t * (3 if bass else 5))
        samples.append(envelope * (sin(phase) + .26 * sin(2 * phase) + .10 * sin(3 * phase)))
    return samples


def write_music(path, seconds=SECONDS):
    total_seconds = max(1, int(seconds))
    mix = array('f', [0]) * (RATE * total_seconds)

    def add(beat, sound, gain):
        start = round(beat * BEAT * RATE)
        if start >= len(mix):
            return
        for i in range(min(len(sound), len(mix) - start)):
            mix[start + i] += sound[i] * gain

    rng = Random(42)
    kick, snap, hat = array('f'), array('f'), array('f')
    previous = 0
    for i in range(RATE // 4):
        t = i / RATE
        noise = rng.uniform(-1, 1)
        kick.append(sin(2 * pi * (53 * t + 1.7 * (1 - exp(-36 * t)))) * exp(-19 * t))
        snap.append((noise + .24 * sin(2 * pi * 185 * t)) * exp(-39 * t))
        hat.append((noise - previous) * exp(-95 * t))
        previous = noise
    chords = [(50, 66, 69, 73), (45, 64, 69, 71), (47, 62, 66, 69), (43, 62, 67, 71)]
    total_bars = max(8, int((total_seconds / (4 * BEAT)) + 1))
    for bar in range(total_bars):
        root, low, mid, high = chords[bar % 4]
        start = bar * 4
        for beat in (0, 2, 2.75):
            add(start + beat, kick, .34)
        for beat in (1, 3):
            add(start + beat, snap, .16)
        for half in range(8):
            add(start + half / 2, hat, .038 if half % 2 else .017)
        for beat in (0, 1.5, 3):
            add(start + beat, tone(root, .9, True), .22)
        for beat, note in ((0, low), (.5, mid), (1.5, high), (2.5, mid), (3.25, low)):
            add(start + beat, tone(note, 1.5), .21)
            add(start + beat + .375, tone(note, 1), .045)
    pcm = array('h', (round(30000 * tanh(v * 1.6) * min(1, i / 250, (len(mix) - i) / (RATE * .45)))
                      for i, v in enumerate(mix)))
    if sys.byteorder != 'little':
        pcm.byteswap()
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        out.writeframes(pcm.tobytes())
