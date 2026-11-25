# Test libraries

Real Fusion 360 tool libraries to test the converter against. They are
third-party files and many have no licence, so they are **not** in this repo:
download them into `private/test-libraries/` (git-ignored). The tests then
convert and verify every one of them (`PrivateLibraryTests` in
`tests/test_convert.py`).

Last checked 2026-11-25 (with default speeds): 64 libraries convert, 133,306
tools written, all verified and accepted by CamCraft 0.26.0 (with and without
`--all-presets`): 24 of the sources below with 7,683 tools, and 40 of
[Autodesk's vendor libraries](#autodesks-vendor-libraries) with 125,623. The
others convert to nothing: `OMAX.json` (one waterjet), three holder libraries
and two probe libraries.

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
| `enstab/` | [ThomasLeMezo/enstabretagne-inventor-cam](https://github.com/ThomasLeMezo/enstabretagne-inventor-cam) | Haas VF2 mold tools, Haas TL1 lathe (24 turning tools, threading without a pitch) |
| `rr1706/` | [rr1706/RRFusionLibrary](https://github.com/rr1706/RRFusionLibrary) | Tormach 8L lathe |
| `tarcos/` | [TarcosS/Auto-Tool-Selector](https://github.com/TarcosS/Auto-Tool-Selector) | Fusion's metric turning samples (internal grooving, round blade, threading) |
| `tandem/` | [machineagency/tandem](https://github.com/machineagency/tandem) | ShopBot |
| `timm052/` | [timm052/cnc-tool-converter](https://github.com/timm052/cnc-tool-converter) | Example file with many tool types (circle segment, counterbore, taps) |
| `ouplan/` | [X3msnake/Ouplan-CNC-Fusion-360-Post-Processor](https://github.com/X3msnake/Ouplan-CNC-Fusion-360-Post-Processor) | Small router library, format v1 |
| `hackland/` | [hackland-shared/CNC](https://github.com/hackland-shared/CNC) | Hackerspace library, format v1, laser |
| `openspindle/` | [openspindle/openspindle](https://github.com/openspindle/openspindle/tree/main/public/tool-libraries) | Dreanique catalog: 23 single-form thread mills (no `LCF`), no presets (needs `--default-*`) |
| `orenbee/` | [OrenBee/FiveBarRobot](https://github.com/OrenBee/FiveBarRobot) | Tormach library with drills, reamers, slitting saw |

Not downloadable as a file, but worth having:

- **Autodesk's own "Fusion Library" samples** (every Fusion tool type): set
  `INCLUDE_FUSION_SAMPLES = True` in
  [`fusion_script/ExportAllToolLibraries`](fusion_script/ExportAllToolLibraries/README.md),
  run it in Fusion and put its `Fusion/` folder in
  `private/test-libraries/fusion-samples/`
  ([#19](https://github.com/bnegt/Fusitool2Craftool/issues/19)).
- **Vendor libraries that need Fusion or an add-in:**
  [Kennametal](https://www.kennametal.com/se/en/resources/software/fusion.html)
  (milling), Sandvik CoroPlus, Amana, Whiteside.
- More on GitHub: `gh search code '"start-values" "post-process"' --extension json`.

## Autodesk's vendor libraries

[Tools for Autodesk Fusion](https://cam.autodesk.com/hsmtools) lists 52 vendor
libraries to download without an account. Use them whenever a change could
touch real tools: they hold 17 times as many as the other libraries together,
with most vendors' habits in them (catalogs without speeds, old format
versions, turning tools with a full holder, stand-alone holders, probes).

```sh
python3 tests/get_autodesk_libraries.py               # about 530 MB into private/test-libraries/autodesk/
python3 tests/get_autodesk_libraries.py --only harvey  # just some
python3 tests/get_autodesk_libraries.py --update       # again, after a vendor's update
```

The site had no file for 7 of them (it answers "Failed to download file").
Counts on 2026-11-25; "Convert" is without defaults (a catalog without speeds,
or with only placeholder speeds, gives none), "With defaults" uses the tests' default speeds:

| Library (catalogue name) | Entries | Convert | With defaults | Mostly |
|---|---|---|---|---|
| Harvey Tool/End Mills | 11,937 | 0 | 11,449 | flat end mill, ball end mill, bull nose end mill |
| Harvey Tool/Holemaking and Threading | 3,959 | 0 | 2,194 | drill, form mill, spot drill |
| Harvey Tool/Specialty Profiles | 7,866 | 0 | 5,484 | slot mill, chamfer mill, form mill |
| Helical Solutions/End Mills | 6,007 | 0 | 6,007 | bull nose end mill, flat end mill, ball end mill |
| Helical Solutions/Specialty Profiles | 190 | 0 | 190 | chamfer mill, spot drill |
| BitsBits/BitsBits Cutting Tools | 62 | 50 | 50 | flat end mill, tapered mill, ball end mill |
| IMCO/Solid End Mills Metric | 1,635 | 1,631 | 1,635 | bull nose end mill, flat end mill, ball end mill |
| IMCO/Solid End Mills Imperial | 3,243 | 3,243 | 3,243 | bull nose end mill, flat end mill, ball end mill |
| SGS Kyocera/SGS Carbide Tools Metric | 4,906 | 0 | 4,893 | drill, flat end mill, bull nose end mill |
| SGS Kyocera/SGS Carbide Tools Imperial | 7,049 | 0 | 7,022 | bull nose end mill, flat end mill, ball end mill |
| Haas Tooling/Haas Tooling | 3,013 | 158 | 2,972 | drill, bull nose end mill, tap right hand |
| Haas Tooling/Haas Holders | 404 | 0 | 0 | holder |
| Haimer/Haimer Holders | 557 | 0 | 0 | holder |
| SwiftCARB/End Mills | 23,574 | 0 | 23,571 | bull nose end mill, flat end mill, ball end mill |
| DATRON/DATRON Cutting Tools | 1,003 | 938 | 938 | flat end mill, drill, chamfer mill |
| Delta Tools/HPC Endmill Solid Carbide | 154 | 154 | 154 | flat end mill, bull nose end mill |
| Destiny Tool/Destiny Tool | 1,791 | 0 | 1,791 | flat end mill, bull nose end mill, ball end mill |
| Renishaw/Renishaw - OMP Probes | 16 | 0 | 0 | probe |
| Blum/Blum Probes | 15 | 0 | 0 | holder, probe |
| Tormach/Tormach - 15L Slant-PRO - Starter | 15 | 15 | 15 | turning general, turning grooving, turning threading |
| Tormach/Tormach - PCNC 440, PCNC 770 - Starter | 20 | 16 | 16 | flat end mill, holder, spot drill |
| Tormach/Tormach - PCNC 1100 - Starter | 20 | 16 | 16 | flat end mill, holder, spot drill |
| Sandvik Coromant/Solid End Mills | 2,876 | 0 | 2,844 | bull nose end mill, flat end mill, ball end mill |
| Sandvik Coromant/Solid Hole Making | 4,315 | 0 | 4,085 | tap right hand, drill, reamer |
| Guhring/Solid Hole Making | 20,121 | 0 | 18,808 | drill, center drill, spot drill |
| NexGen Tooling/NexGen Tooling | 3,981 | 0 | 3,981 | bull nose end mill, flat end mill, ball end mill |
| Garr Tool/Garr Tool | 11,092 | 0 | 10,497 | drill, bull nose end mill, flat end mill |
| Valor Holemaking/Valor Holemaking | 2,251 | 0 | 2,001 | drill, form mill, chamfer mill |
| OSG/End Mills | 259 | 259 | 259 | flat end mill |
| Dormer Pramet/Dormer Pramet | - | - | - | not available |
| Kennametal/Kennametal | - | - | - | not available |
| Iscar/Iscar Hole Making Imperial | 289 | 0 | 289 | drill |
| Iscar/Iscar Hole Making Metric | 1,156 | 0 | 1,156 | drill |
| Iscar/Iscar Solid End Mills Imperial | 3,249 | 3,249 | 3,249 | bull nose end mill, flat end mill, ball end mill |
| Iscar/Iscar Solid End Mills Metric | 2,564 | 2,564 | 2,564 | bull nose end mill, flat end mill, ball end mill |
| Fraiser Tools/Boss Beaver | 123 | 119 | 119 | flat end mill, drill, chamfer mill |
| Fraiser Tools/Carbider Poly | 114 | 114 | 114 | flat end mill, chamfer mill, face mill |
| XEBEC/XEBEC Deburring tools | 42 | 42 | 42 | lollipop mill |
| Schunk/Schunk Holders | 642 | 0 | 0 | holder |
| Kennametal/Turning Imperial/Turning Imperial | 57 | 55 | 55 | turning general, turning boring, turning grooving |
| Kennametal/Turning Metric/Turning Metric | - | - | - | not available |
| MA Ford/MA Ford End Mills | 20 | 0 | 20 | bull nose end mill |
| MA Ford Europe/End Mills | 189 | 0 | 189 | bull nose end mill, flat end mill |
| Sandvik Coromant/Turning Imperial/Turning Imperial | 54 | 54 | 54 | turning general, turning boring, turning threading |
| Sandvik Coromant/Turning Metric/Turning Metric | 243 | 240 | 240 | turning general, turning boring, turning grooving |
| Iscar/Turning Imperial/Turning Imperial | 71 | 71 | 71 | turning general, turning boring, turning grooving |
| Iscar/Turning Metric/Turning Metric | 106 | 106 | 106 | turning general, turning boring, turning grooving |
| Mitsubishi Materials/Solid End Mills | 3,248 | 3,240 | 3,240 | flat end mill, ball end mill, bull nose end mill |
| Ingersoll/Turning Imperial/Turning Imperial | - | - | - | not available |
| Ingersoll/Turning Metric/Turning Metric | - | - | - | not available |
| Widia/Turning Imperial/Turning Imperial | - | - | - | not available |
| Widia/Turning Metric/Turning Metric | - | - | - | not available |
| **Total** | **134,498** | **16,334** | **125,623** | |

The tools skipped even with defaults, by reason: form mill 3,023; tapered mill
925; reamer 876; counterbore 434; dovetail with a corner radius 372; radius
mill 270; left-hand cutter 1,313; probe 22; undercut cutter without a neck narrower than the head 3;
other 17.

## Fusion tool types covered

Tools per type in all of `private/test-libraries/`. Autodesk's
[schema](https://cam.autodesk.com/tools/tools/Tooling%20Schema/ToolLibrary.schema.json)
names 40 types; 33 occur in a real library. The other 7 are in none of them,
so what we do with them rests on the schema alone (#19). Every type converts
or is skipped under a family name
(`test_every_fusion_type_converts_or_is_named`).

| Fusion type | Tools | Libraries | Result |
|---|---|---|---|
| ball end mill | 11,958 | 37 | `ball_nose` |
| boring bar | 1 | 1 | `bore` |
| bull nose end mill | 49,895 | 27 | `bull_nose` |
| center drill | 373 | 6 | `drill` |
| chamfer mill | 2,278 | 24 | `taper` |
| circle segment barrel | 1 | 1 | skipped: circle segment mill |
| circle segment lens | 1 | 1 | skipped: circle segment mill |
| circle segment oval | 1 | 1 | skipped: circle segment mill |
| circle segment taper | **none** | - | skipped: circle segment mill |
| corner chamfer end mill | **none** | - | `bull_nose` |
| counter bore | 435 | 3 | skipped: counterbore |
| counter sink | 108 | 7 | `taper` |
| dovetail mill | 633 | 5 | `dovetail` |
| drill | 34,969 | 27 | `drill` (`taper` for a point of 60 degrees or less) |
| electric arc wire | **none** | - | skipped: additive head |
| face mill | 168 | 14 | `end_mill` |
| flat end mill | 25,050 | 45 | `end_mill` |
| form mill | 3,026 | 7 | skipped: form mill |
| holder | 1,685 | 13 | counted, see `--holders` |
| laser cutter | 2 | 2 | skipped: sheet cutting head |
| laser powder | **none** | - | skipped: additive head |
| laser wire | **none** | - | skipped: additive head |
| lollipop mill | 1,018 | 5 | `lollipop` |
| plasma cutter | 1 | 1 | skipped: sheet cutting head |
| probe | 28 | 7 | skipped: probe |
| radius mill | 273 | 4 | skipped: roundover bit |
| reamer | 881 | 9 | skipped: reamer |
| shaft | **none** | - | skipped: holder part |
| slot mill | 3,746 | 9 | `t_slot` |
| spot drill | 1,249 | 16 | `drill` (`taper` for a point of 60 degrees or less) |
| tap left hand | 1 | 1 | `tap` |
| tap right hand | 2,744 | 7 | `tap` |
| tapered mill | 967 | 11 | skipped: tapered mill (`taper` with `--unreleased-tapers`; straight without an angle) |
| thread mill | 206 | 5 | `thread_mill` |
| tool block | **none** | - | skipped: holder part |
| turning boring | 200 | 8 | `bore` |
| turning general | 334 | 10 | `turning` |
| turning grooving | 73 | 10 | `groove` |
| turning threading | 19 | 6 | `thread` |
| waterjet | 2 | 2 | skipped: sheet cutting head |

## Download the others

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
get ThomasLeMezo/enstabretagne-inventor-cam tools/TL1_POMC.json enstab
get rr1706/RRFusionLibrary "2026 8L Library.json" rr1706
get TarcosS/Auto-Tool-Selector "Turning Tools (Metric).json" tarcos
get openspindle/openspindle public/tool-libraries/dreanique.json openspindle
```

Then:

```sh
python3 -m unittest tests.test_convert.PrivateLibraryTests        # convert + verify all
python3 -m fusitool2craftool private/test-libraries -o /tmp/out   # see every skip reason
```
