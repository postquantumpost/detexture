# Rannirune: conversion and formats

## Usage

Install Python 3 and the dependency with `python -m pip install -r requirements.txt`.
An Oodle library compatible with the input's KRAK stream is also required; it is
not distributed here. Use a library you are entitled to use. For the supplied
file, `oo2core_6_win64.dll` was successfully used on 64-bit Windows:

```powershell
python rannirune.py 00_solo-tpfbdt\MENU_Knowledge_00001.tpf.dcx --oodle C:\path\to\oo2core_6_win64.dll
```

By default the PNG is written next to the input, replacing `.tpf.dcx` with
`.png`. Pass a directory to process its files in name order; unsupported or
failed files are reported and processing continues. For directory input, use
`-o C:\path\to\output-folder` to choose the output directory. For a single
file, use `-o C:\path\to\output.png` to choose the destination. Existing
outputs are never overwritten and are reported as skipped. `--oodle` can be
omitted if `OODLE_LIBRARY` names the library. Otherwise the default is
`%ProgramFiles(x86)%\Steam\steamapps\common\ELDEN RING\Game\oo2core_6_win64.dll`
(with `C:\Program Files (x86)` used if `ProgramFiles(x86)` is unset). Use
`--oodle` or `OODLE_LIBRARY` for a different Steam library location. The
library must match the Python process architecture. Errors are reported on
stderr with a nonzero exit status.

## Observed format stack

These notes describe the supplied file and the subset this converter handles,
not a complete specification of any of the formats. DCX and TPF are container
formats; DDS is a texture format. Multi-texture TPFs, console texture layouts,
and other DCX compression methods are not supported.

### DCX

The outer container starts with `DCX\0`. Its integer fields are big-endian.
The supplied header has `DCS\0` at offset `0x18`, followed by the uncompressed
and compressed byte counts at `0x1c` and `0x20`. `DCP\0KRAK` at `0x24`
identifies Oodle KRAK compression. `DCA\0` occurs at `0x44`; the compressed
stream starts at `0x4c` and runs to the end of the file. The converter checks
this header layout and both sizes, then calls `OodleLZ_Decompress`. The
uncompressed size is capped at 512 MiB.

### TPF

The decompressed file begins with `TPF\0`. The observed PC header and entry use
little-endian integers: the texture count is at `0x08`, platform byte at
`0x0c` (zero for PC), and the first entry at `0x10` begins with the data offset
and byte size. The observed entry also contains format byte `0x66`, one mip
level, and a UTF-16LE texture name. For the supplied file the entry points to
one embedded DDS at `0x4e`. The converter requires one PC texture and uses the
entry's offset and size, rather than searching for a magic string.

### DDS and PNG

The embedded data begins with `DDS ` and has a standard DDS header with a
`DX10` extension. The supplied texture is 1024 by 1024 pixels, with DXGI format
98 (`BC7_UNORM`) and one mip level. Pillow decodes the top-level DDS image,
including BC7, and writes an RGBA PNG. Other embedded DDS variants depend on
Pillow's decoder support; this program does not interpret TPF format metadata
or convert console-specific data.
