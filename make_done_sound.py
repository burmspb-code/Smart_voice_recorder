import math
import struct
import wave
from pathlib import Path

out = Path(__file__).parent / "src" / "assets" / "done.wav"
out.parent.mkdir(parents=True, exist_ok=True)

sr = 22050
duration = 0.28
freq = 880.0  # A5, мягкий
amp = 0.25

frames = []
n = int(sr * duration)
for i in range(n):
    t = i / sr
    # затухание экспоненциальное
    env = math.exp(-t * 9.0)
    # мягкий синус + небольшая гармоника для теплее
    s = math.sin(2 * math.pi * freq * t) + 0.3 * math.sin(4 * math.pi * freq * t)
    s = s * env * amp
    # 16-bit
    val = int(max(-1.0, min(1.0, s)) * 32767)
    frames.append(struct.pack('<h', val))

with wave.open(str(out), 'wb') as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(b''.join(frames))

print('written', out, len(frames), 'frames')
