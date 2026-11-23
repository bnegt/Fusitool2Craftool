# Test libraries

Real Fusion 360 tool libraries to test the converter against. They are
third-party files and many have no licence, so they are **not** in this repo:
download them into `private/test-libraries/` (git-ignored). The tests then
convert and verify every one of them (`PrivateLibraryTests` in
`tests/test_convert.py`).

Last checked 2026-11-23: 21 libraries, 6,125 tools; 20 libraries convert,
7,435 tools written, all verified and accepted by CamCraft 0.25.0
(with and without `--all-presets`).

## Sources

| Folder | Library | What's in it |
|---|---|---|
| `robbjack/` | [RobbJack Fusion 360 library](https://www.robbjack.com/downloads/fusion-360) ([direct `.tools`](https://robbjack.com/data/cam/robbjack-fusion360.tools)) | 6,746 end mills and slitting saws, inch and metric, 12,310 presets (current format v36) |
| `carlbass/` | [carlbass/fusion_tool_libraries](https://github.com/carlbass/fusion_tool_libraries) | Haas ST30SSY (lathe), Haas VM3, Matsuura MX330, Thermwood, OMAX waterjet: drills, taps, dovetail/lollipop/slot mills, turning tools |
| `ezpeople/` | [ezpeople/FUSION360](https://github.com/ezpeople/FUSION360/tree/main/libraries/Local) | Snapmaker, SpeTool router bits (roundover, tapered), personal libraries |
| `carbide3d/` | [michaelgrilo/carbide3d-tool-library](https://github.com/michaelgrilo/carbide3d-tool-library) | Carbide 3D cutters, old format v1 (`start-values: {"*": ...}`) |
| `techcnc/` | [TechCNC/Template-Tool-Library](https://github.com/TechCNC/Template-Tool-Library) | AP4060 router; a plain-JSON `.tools` file with garbled (double-encoded) names |
| `ps1/` | [evezor/Pumping-Station-One-Standard-Library](https://github.com/evezor/Pumping-Station-One-Standard-Library) | Tormach shop library, format v1, probe |
| `helsinki/` | [HelsinkiHacklab/lottacnc](https://github.com/HelsinkiHacklab/lottacnc) | Hackerspace library, format v1, reamers |
| `enstab/` | [ThomasLeMezo/enstabretagne-inventor-cam](https://github.com/ThomasLeMezo/enstabretagne-inventor-cam) | Haas VF2 mold tools |
| `tandem/` | [machineagency/tandem](https://github.com/machineagency/tandem) | ShopBot |
| `timm052/` | [timm052/cnc-tool-converter](https://github.com/timm052/cnc-tool-converter) | Example file with many tool types (circle segment, counterbore, taps) |
| `ouplan/` | [X3msnake/Ouplan-CNC-Fusion-360-Post-Processor](https://github.com/X3msnake/Ouplan-CNC-Fusion-360-Post-Processor) | Small router library, format v1 |
| `hackland/` | [hackland-shared/CNC](https://github.com/hackland-shared/CNC) | Hackerspace library, format v1, laser |
| `orenbee/` | [OrenBee/FiveBarRobot](https://github.com/OrenBee/FiveBarRobot) | Tormach library with drills, reamers, slitting saw |

Not downloadable as a file, but worth having:

- **Autodesk's own "Fusion Library" samples** (every Fusion tool type): set
  `INCLUDE_FUSION_SAMPLES = True` in
  [`fusion_script/ExportAllToolLibraries`](fusion_script/ExportAllToolLibraries/README.md)
  and run it in Fusion.
- **Vendor libraries that need Fusion or an add-in:** Harvey Tool / Helical
  ([Autodesk's CAM tool library site](https://www.harveyperformance.com/autodesk-fusion-integrations/)),
  [Kennametal](https://www.kennametal.com/se/en/resources/software/fusion.html),
  Sandvik CoroPlus, Amana, Whiteside.
- More on GitHub: `gh search code '"start-values" "post-process"' --extension json`.

## Download them all

Needs `curl` and an authenticated `gh`. Run from the repo root:

```sh
D=private/test-libraries
mkdir -p "$D/robbjack"
curl -sSfL -o "$D/robbjack/robbjack-fusion360.tools" https://robbjack.com/data/cam/robbjack-fusion360.tools
# sha256 75f115baa41cdd909fb73f6f51e56e91a5be2e1b0083c45eda67d76293fc7031 (2026-11-23; changes when RobbJack updates it)

get() {  # repo, path in repo, folder
  mkdir -p "$D/$3"
  gh api -H 'Accept: application/vnd.github.raw' \
    "repos/$1/contents/$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$2")" \
    > "$D/$3/$(basename "$2")" && echo "ok $3/$(basename "$2")"
}
for f in "Haas ST30SSY.json" "Haas VM3.json" "Matsuura MX330.json" "OMAX.json" "Thermwood Model 90.json" "banana holes.json"; do
  get carlbass/fusion_tool_libraries "$f" carlbass
done
for f in "Snapmaker 2.0 CNC Tool Library.json" "_CNCFraises_.json" "_SpeTool_.json" "__ez__.json"; do
  get ezpeople/FUSION360 "libraries/Local/$f" ezpeople
done
get michaelgrilo/carbide3d-tool-library carbide3d-tool-library.json carbide3d
get TechCNC/Template-Tool-Library "CNC Machines/AP4060/Fusion 360/Tool Library/AP4060_new.tools" techcnc
get evezor/Pumping-Station-One-Standard-Library "Tormach/PS1 Standard Tormach Tool Library v0.3.json" ps1
get HelsinkiHacklab/lottacnc tools/fusion360/helsinkihacklab.json helsinki
get ThomasLeMezo/enstabretagne-inventor-cam tools/VF2_mold.json enstab
get machineagency/tandem fusion360/toolLibrary/shopbotCamTool.json tandem
get timm052/cnc-tool-converter "Example Files/Tool Libs/Fusion 360/Fusion Tool Libary.json" timm052
get X3msnake/Ouplan-CNC-Fusion-360-Post-Processor tools/MAD.json ouplan
get hackland-shared/CNC "cad_cam_post_processors/fusion360/fusion360 tools.json" hackland
get OrenBee/FiveBarRobot "CAM_Profiles/RPF Tormach Tool Library_new.json" orenbee
```

Then:

```sh
python3 -m unittest tests.test_convert.PrivateLibraryTests        # convert + verify all
python3 -m fusitool2craftool private/test-libraries -o /tmp/out   # see every skip reason
```
