# Fusitool2Craftool

Tool library converter for Fusion 360 to [CamCraft](https://github.com/NeoTech/Camcraft).

Reads a Fusion 360 tool library (`.tools` or `.json` export) and writes a
CamCraft tool library (`.camtools`). It can also import the result straight
into a running CamCraft window.

Pure Python 3.10+, no dependencies.

## How to run

### 1. Export your library from Fusion 360

In Fusion, open the **Tool Library** (Manufacture workspace → Manage → Tool
Library), right-click your library and choose **Export**. Save it as a
`.tools` file (or `.json`). Both formats work.

### 2. Get the converter

You need Python 3.10 or newer (`python3 --version`). Nothing else.

```sh
git clone https://github.com/bnegt/Fusitool2Craftool.git
cd Fusitool2Craftool
```

### 3. Convert

Run it straight from the folder, no install needed:

```sh
python3 -m fusitool2craftool ~/Downloads/MyTools.tools
```

On Windows use `py -m fusitool2craftool C:\path\to\MyTools.tools`.

This writes `MyTools.camtools` next to the input file and prints a report:

```
wrote 4 tool(s) to /home/me/Downloads/MyTools.camtools
verified: all 4 tool(s) in MyTools.camtools match the Fusion data
```

Red lines are tools that were **not** converted (see
[Skipped tools](#skipped-tools-shown-in-red)). A red `mismatch:` line means
the output check failed - don't import that file.

Common variations:

```sh
# Choose where the output goes
python3 -m fusitool2craftool MyTools.tools -o ~/cam/shop.camtools

# Take speeds from a named Fusion preset, and say which material they are for
python3 -m fusitool2craftool MyTools.tools --preset "Finishing" --material aluminium_6061

# Library without feeds/speeds (e.g. vendor libraries): give defaults
python3 -m fusitool2craftool Vendor.json --default-rpm 18000 --default-feed 1000

# Show every option
python3 -m fusitool2craftool --help
```

### 4. Load the tools in CamCraft

**By hand (default):** in CamCraft, open the tool library and import the
`.camtools` file. Choose *update* if you are re-importing after changing
your Fusion library, so existing tools are refreshed instead of duplicated.

**Automatically (optional):** start CamCraft with an MCP port and add
`--push` to the command. This needs `camcraft-cli` (it ships with CamCraft):

```sh
camcraft-ui --mcp-port 7777 &
python3 -m fusitool2craftool MyTools.tools --push 7777
```

The tools land in the document open in the CamCraft window, as one undo step.
See [Import into a running CamCraft](#import-into-a-running-camcraft) for the options.

### Optional: install as a command

To get a `fusitool2craftool` command instead of `python3 -m ...`:

```sh
pipx install .        # recommended
# or, inside a virtual environment:
python3 -m venv .venv && . .venv/bin/activate && pip install .
```

(On Arch, Debian and other distros a plain `pip install .` into the system
Python is blocked; use pipx or a venv.)

Then: `fusitool2craftool MyTools.tools`

## Reference

### Output check

Every run double-checks its own output: the written `.camtools` file is
read back from disk and each tool is compared with the Fusion tool it came
from (diameter, flute/shank lengths, shank diameter, flute count, kind,
angles, thread pitch and hand, tool number, and that rpm/feed match a Fusion
preset), with inch values recomputed to mm. The tool count must equal the
Fusion tools minus the skipped ones.

```
wrote 114 tool(s) to shop.camtools (6 skipped)
verified: all 114 tool(s) in shop.camtools match the Fusion data
```

Any difference is printed in red as `mismatch: ...` and the run exits with
an error - don't import that file.

### Skipped tools (shown in red)

**Tapered ball mills and roundover bits are not imported.** CamCraft has no
tool type that can represent them, so they are left out of the `.camtools`
file. When the Fusion library contains any, each one is listed in red and a
red summary line is printed:

```
skipped [tapered ball mill]: TB-2F-D6.0-R0.5-20°-E2 ...: Fusion tool type 'tapered mill' has no CamCraft counterpart yet
skipped [roundover bit]: RBB-2F-D1/4-R1/4-F1b ...: corner radius 6.35 mm exceeds half the 6.35 mm diameter (form/roundover cutter)
NOT IMPORTED: 2 roundover bits, 4 tapered ball mills - CamCraft has no tool type for them, so they are skipped
```

Other tools that cannot be converted (see below) are listed in red the same
way. Colour is used when writing to a terminal; set `NO_COLOR=1` to turn it
off or `FORCE_COLOR=1` to force it.

### Import into a running CamCraft

Start CamCraft with an MCP port, then pass `--push`:

```sh
camcraft-ui --mcp-port 7777            # or set CAMCRAFT_MCP_PORT=7777
fusitool2craftool MyTools.tools --push 7777
```

This runs `camcraft-cli mcp --connect 127.0.0.1:7777` and calls CamCraft's
`import_tool_library`. The import is one undo step in the open document.

| Option | Meaning |
|---|---|
| `--push PORT` | import into the CamCraft window listening on PORT |
| `--mode add\|update\|replace` | `update` (default) refreshes same-named tools; `add` renames clashes; `replace` also removes tools not in the library (unless used) |
| `--host HOST` | CamCraft host (default `127.0.0.1`) |
| `--camcraft-cli PATH` | `camcraft-cli` if it is not on `PATH` |
| `--check` | verify the file by importing it into a headless CamCraft (no window) |

### Conversion options

| Option | Meaning |
|---|---|
| `--preset NAME` | Fusion preset to take rpm/feed from (default: each tool's first) |
| `--material WORD` | CamCraft material the presets are for: `aluminium_6061`, `mild_steel`, `stainless_304`, `grey_cast_iron`, `brass`, `plastics_pom`, `softwood`, `hardwood`, `mdf`, `plywood`, `acrylic`, `hdpe` |
| `--holder-d MM`, `--holder-len MM` | holder size for tools with no holder in Fusion (default 50 x 40) |
| `--default-rpm`, `--default-feed` | speeds for tools without a preset (otherwise skipped) |
| `--strict` | fail instead of skipping tools that cannot be converted |

## What is converted

| Fusion type | CamCraft kind |
|---|---|
| flat end mill, face mill | `end_mill` (`bull_nose` if it has a corner radius) |
| ball end mill | `ball_nose` |
| bull nose end mill | `bull_nose` |
| chamfer mill, counter sink | `taper` (Fusion's half angle `TA` is doubled) |
| drill, spot drill, center drill | `drill` |
| tap right/left hand | `tap` |
| thread mill | `thread_mill` |
| boring bar | `bore` |

- Inch libraries are converted to mm (CamCraft only reads mm).
- Stick-out: flute length `LCF`, shank length `LB - LCF`.
- Holder: Fusion's holder segments become CamCraft's holder cylinder
  (largest diameter x total height), which is a conservative envelope.
- Speeds: the preset's `n` and `v_f`. Stepdown/stepover go into a cutting
  data row (`unknown` material unless `--material` is given).
- Vendor, product id, product link, flute count, tool material (HSS/carbide)
  and the Fusion tool number are carried over.

### Not converted

| Fusion tool | Why |
|---|---|
| tapered ball mill / tapered mill | CamCraft has no tapered cutter |
| roundover bit (ball end mill with radius > D/2) | CamCraft has no form/roundover cutter |
| lathe tools, slot/dovetail/lollipop mills, probes, engraving, form mills | not mapped yet |
| tools without rpm/feed | skipped unless `--default-rpm` / `--default-feed` are given |

A ball end mill whose radius is within 10 % of D/2 is still a ball nose
(vendors round imperial radii, e.g. 1/8" listed as R1.5). A tool CamCraft
would refuse is skipped too, because CamCraft refuses a whole library over a
single bad tool. All skipped tools are listed in red with the reason; with
`--strict` the run fails instead.

**Tool numbers:** CamCraft currently assigns its own T numbers when it
imports a `.camtools` file; the Fusion number is written but may not be used.

## Development

```sh
python3 -m unittest discover -s tests -t .
```

Put your own Fusion library in `private/` (git-ignored); the tests then also
check that every tool in it converts and that the output verifies.

### CamCraft upstream check

The first tests to run (`tests/test_camcraft_upstream.py`) ask GitHub whether
CamCraft can now handle the tools we skip
([#1](https://github.com/bnegt/Fusitool2Craftool/issues/1)):

- CamCraft issues [#895](https://github.com/NeoTech/Camcraft/issues/895)
  (tapered ball mills, V-bit tip diameter) and
  [#896](https://github.com/NeoTech/Camcraft/issues/896) (roundover bits)
  are still open
- CamCraft's list of tool kinds is unchanged

If either fails, CamCraft has changed: update the converter, then update
`BLOCKING_ISSUES` / `KNOWN_KINDS` in that test. Offline (or rate-limited) the
check is skipped; `F2C_OFFLINE=1` skips it on purpose, and `GITHUB_TOKEN`
raises GitHub's rate limit.

| Module | Role |
|---|---|
| `fusion.py` | read `.tools` (zip or plain JSON) and `.json` libraries |
| `convert.py` | map Fusion tools to CamCraft definitions, apply CamCraft's sanity rules |
| `camtools.py` | write the `.camtools` envelope |
| `verify.py` | read the written file back and compare it with the Fusion source |
| `push.py` | import into CamCraft through `camcraft-cli mcp` |
| `cli.py` | command line, coloured report |
