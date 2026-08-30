# Daily Dub Template

## Output

- file: `C:\Users\runya\Videos\pZ_aeriaL\out\Daily Dub #EPISODENUMBER - EPISODENAME.mp4`
- resolution: 1920x1080
- fps: 60
- encoder: h264_nvenc

## Joins

- join: crossfade
- crossfade: 0.3
- fade: 0.5
- audio overlap: 2
- audio lead: 0
- fade from black: 0.5
- fade to black: 0.5

## Auto Editor

- balance audio: yes
- audio target: -14 LUFS
- trim silence: yes
- silence threshold: -30 dB
- silence padding: 0.5
- silence min length: 1
- silence min segment: 0.5

## Timeline

1. `E:\Content Creation\pZ_aeriaL\Assets\Video Assets\Intro\pZ_aeriaL Video Intro 1.mp4` -- keep silence, balance -9.6 dB
2. `C:\Users\runya\Downloads\2859468736-150376735-0f8d7165-99aa-4a9b-9893-221fb7ce35c5.mp4` -- 1:05:29.724-1:10:09.021, balance +12.6 dB
3. `E:\Content Creation\pZ_aeriaL\Assets\Video Assets\Outro\pZ_aeriaL Video Outro 4.mp4` -- audio overlap 69, keep silence, volume -8 dB, balance +8.1 dB
