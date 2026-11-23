# ExportAllToolLibraries

A Fusion script that exports **every** tool library in your account in one run, one `.json`
file per library: your Local, Cloud and Hub libraries. Autodesk's own sample libraries
("Fusion Library") are skipped. Fusion's Tool Library window can only export one library at
a time, which is slow when you have many.

**Read-only:** it reads your libraries and writes files; it changes nothing in Fusion or your account.

## Install
1. Copy this whole `ExportAllToolLibraries` folder into Fusion's Scripts folder:
   - macOS: `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/Scripts/`
   - Windows: `%APPDATA%\Autodesk\Autodesk Fusion 360\API\Scripts\`

   (or, in Fusion: Shift+S, the **+** at the top of the list, and pick this folder).
2. In Fusion: **Shift+S**, find **ExportAllToolLibraries** in the Scripts list, **Run**.
3. Pick a folder. The export goes into a `Fusion tool libraries` folder inside it.

## Output
Files go in `Local/`, `Cloud/` and `Hub/` subfolders (Fusion's own folder structure is kept),
named after the libraries, plus `export-log.txt` listing anything that could not be exported.
Two libraries with the same name get `(2)`, `(3)` ... added.

To also export Autodesk's built-in **Fusion Library** samples (they are not downloadable
anywhere else), set `INCLUDE_FUSION_SAMPLES = True` at the top of the script; they land in
`Fusion/`. Keep them out of public repos - they are Autodesk's.

The `.json` files are Fusion's own tool-library format: Fusion's Tool Library can import them
again.

## Convert them all for CamCraft

```sh
python3 -m fusitool2craftool ~/"Documents/Fusion tool libraries"
```

writes one `.camtools` per library, next to each `.json`. Use `-o DIR` to put them in a
separate folder instead (same subfolders). See the main [README](../../README.md).

Tested on Fusion with tool library v2.18.0 (macOS): 14 libraries, 0 failed.
