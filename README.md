# Fusitool2Craftool

Tool library converter for Fusion 360 to [CamCraft](https://github.com/NeoTech/Camcraft).

Reads a Fusion 360 tool library (`.tools` or `.json` export), or a whole
folder of them, and writes CamCraft tool libraries (`.camtools`). It can also
check the result in a headless CamCraft, or import it straight into a running
CamCraft window. A Fusion script that exports **all** your tool libraries in
one go is included.

Pure Python 3.10+, no dependencies. For **CamCraft 0.26.0 or newer**; older
versions may refuse a library. Tested against CamCraft 0.26.0: all
134,623 tools converted from 65 real libraries import without a change (see
[Testing against CamCraft](#testing-against-camcraft)).

## How to run

### 1. Export your library from Fusion 360

In Fusion, open the **Tool Library** (Manufacture workspace → Manage → Tool
Library), right-click your library and choose **Export**. Save it as a
`.tools` file (or `.json`). Both formats work.

**Many libraries?** Fusion only exports one at a time. The Fusion script in
[`fusion_script/ExportAllToolLibraries`](fusion_script/ExportAllToolLibraries/README.md)
exports every Local, Cloud and Hub library in one run (one `.json` each,
read-only). Install it in Fusion's Scripts list (Shift+S), run it, pick a
folder, then convert the whole folder in step 3.

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
# A whole folder of libraries (e.g. the ExportAllToolLibraries export):
# one .camtools per library, next to each .json
python3 -m fusitool2craftool ~/"Documents/Fusion tool libraries"

# ...or into a separate folder, keeping the subfolders
python3 -m fusitool2craftool ~/"Documents/Fusion tool libraries" -o ~/cam/libraries

# Choose where the output goes
python3 -m fusitool2craftool MyTools.tools -o ~/cam/shop.camtools

# Take speeds from a named Fusion preset, and say which material they are for
python3 -m fusitool2craftool MyTools.tools --preset "Finishing" --material aluminium_6061

# Bring every preset over as per-material cutting data (aluminium, steel, ...)
python3 -m fusitool2craftool MyTools.tools --all-presets

# Library without feeds/speeds (e.g. vendor libraries): give defaults
python3 -m fusitool2craftool Vendor.json --default-rpm 18000 --default-feed 1000

# ...or per tool size and kind: cutting speed + chip load, capped at the spindle's max
python3 -m fusitool2craftool Vendor.json --default-vc 150 --default-fz 0.03 --max-rpm 24000 \
    --default-vc drill=60 --default-feed drill=150

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

**Check first (optional):** `--check` imports the file into a headless
CamCraft (no window) and prints what CamCraft would add. Works for a folder
too, one library at a time:

```sh
python3 -m fusitool2craftool ~/"Documents/Fusion tool libraries" --check
```

#### Getting `camcraft-cli`

It comes with every CamCraft install. Download from
[CamCraft's releases](https://github.com/NeoTech/Camcraft/releases)
(`.deb`, `.rpm`, Arch `.pkg.tar.zst`, AppImage, flatpak, snap, macOS,
Windows). Without installing anything, the Linux `.tar.gz` holds
`camcraft-cli` and `camcraft-ui` ready to run:

```sh
curl -LO https://github.com/NeoTech/Camcraft/releases/download/v0.26.0/camcraft-0.26.0-linux-x86_64.tar.gz
tar xzf camcraft-0.26.0-linux-x86_64.tar.gz
python3 -m fusitool2craftool MyTools.tools --check \
    --camcraft-cli ./camcraft-0.26.0-linux-x86_64/camcraft-cli
```

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
skipped [tapered ball mill]: TB-2F-D6.0-R0.5-20°-E2 ...: tapered mill: its tip form needs a CamCraft newer than 0.26.0 (--unreleased-tapers)
skipped [roundover bit]: RBB-2F-D1/4-R1/4-F1b ...: corner radius 6.35 mm exceeds half the 6.35 mm diameter (form/roundover cutter)
NOT IMPORTED: 2 roundover bits, 4 tapered ball mills - CamCraft has no tool type for them, so they are skipped
```

Other tools that cannot be converted ([Not converted](#not-converted)) are
listed in red the same way, with their family if it has a name: reamers,
counterbores, form mills, circle segment mills, corner radius dovetail mills,
probes, holder parts, cutting and additive heads, and some lathe tools. Colour is used when writing to a terminal; set `NO_COLOR=1` to turn it
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
| `--push PORT` | import into the CamCraft window listening on PORT (one library at a time, not a folder) |
| `--mode add\|update\|replace` | `update` (default) refreshes same-named tools; `add` renames clashes; `replace` also removes tools not in the library (unless used) |
| `--host HOST` | CamCraft host (default `127.0.0.1`) |
| `--camcraft-cli PATH` | `camcraft-cli` if it is not on `PATH` |
| `--check` | verify the file by importing it into a headless CamCraft (no window) |

### Conversion options

| Option | Meaning |
|---|---|
| `--preset NAME` | Fusion preset to take rpm/feed from (default: each tool's first) |
| `--material WORD` | CamCraft material the chosen preset is for: `aluminium_6061`, `mild_steel`, `stainless_304`, `grey_cast_iron`, `brass`, `plastics_pom`, `softwood`, `hardwood`, `mdf`, `plywood`, `acrylic`, `hdpe` |
| `--all-presets` | also write one cutting data row per Fusion preset whose name names a CamCraft material (see [Cutting data per material](#cutting-data-per-material)) |
| `--unreleased-tapers` | write tapered mills with their flat or ball tip, for a CamCraft build newer than 0.26.0 (0.26.0 refuses the whole file; see [Tapered mills](#tapered-mills)) |
| `--unreleased-materials` | allow material words that are only on CamCraft's main, which its latest release refuses (none at the moment) |
| `--holder-d MM`, `--holder-len MM` | holder size for tools with no holder in Fusion (default 50 x 40) |
| `--default-holder NAME` | a stand-alone Fusion holder (its description or product id) for tools with no holder in Fusion, drawn at each tool's own stick-out; beats `--holder-d` / `--holder-len` |
| `--holders PATH` | the Fusion library, or folder of them, `--default-holder` is in (default: the input) |
| `--holder-cylinders` | write Fusion holders as one cylinder instead of their drawn outline |
| `--default-rpm [KIND=]RPM`, `--default-feed [KIND=]MM_MIN` | speed / feed for tools Fusion gives none (otherwise skipped); see [Default speeds](#default-speeds) |
| `--default-vc [KIND=]M_MIN`, `--default-fz [KIND=]MM` | the same from cutting speed and chip load: rpm = v_c x 1000 / (pi x D), feed = rpm x flutes x f_z |
| `--max-rpm RPM` | cap for an rpm computed from `--default-vc` |
| `--strict` | fail instead of skipping tools that cannot be converted |

## What is converted

| Fusion type | CamCraft kind |
|---|---|
| flat end mill, face mill | `end_mill` (`bull_nose` if it has a corner radius) |
| ball end mill | `ball_nose` |
| bull nose end mill | `bull_nose` (`ball_nose` if the corner radius is D/2, up to 10 % over for vendor rounding) |
| chamfer mill, counter sink | `taper` (Fusion's half angle `TA` is doubled) |
| drill, spot drill, center drill | `drill` (`end_mill` if the point angle is 180: a flat bottom drill; `taper` if it is 60 or less) |
| tap right/left hand | `tap` |
| thread mill | `thread_mill` (`thread_mill_single` if it has one row of teeth, `NT` 1, and no `LCF`) |
| corner chamfer end mill | `bull_nose` (an approximation, see below) |
| boring bar | `bore` |
| slot mill, slitting saw | `t_slot` (`keyseat` if the name says keyseat/Woodruff) |
| lollipop mill | `lollipop` |
| turning general / boring / grooving / threading | `turning` / `bore` / `groove` / `thread` (see [Lathe tools](#lathe-tools)) |
| dovetail mill | `dovetail` (angle 90 - `TA`: Fusion measures off the axis, CamCraft off the bottom face); with a corner radius it is skipped |

- Inch libraries are converted to mm (CamCraft only reads mm).
- Stick-out: flute length `LCF`, shank length `LB - LCF`.
- Undercut cutters (slot mills, slitting saws, lollipops, dovetails): the head
  is `DC` x `LCF`, the neck is the wider of Fusion's shaft (`SFDM`) and
  shoulder diameters (so CamCraft never lets the body pass where it would
  hit), and the neck length is `LB - LCF`. A lollipop's flute length is
  capped where the neck leaves the ball (CamCraft's limit; above it there is
  only neck), with a warning. A dovetail's head is capped the same way where
  its flank narrows to the neck: Fusion records the shank, not the narrower
  neck under the head, so CamCraft sees a shallower cutter and a body no
  narrower than the real one.
- Undercut cutters **with a shank as wide as the head** (most Harvey keyseat
  and dovetail cutters): the neck is the narrow start of Fusion's shoulder and
  shaft segments, as far as it stays that narrow. The rest of the shaft is
  drawn under the holder, so CamCraft holds a slot deeper than the neck's
  reach. Without a holder in Fusion that holder is the default one
  (`--holder-d`, `--holder-len`, `--default-holder`). With
  `--holder-cylinders` the one cylinder starts at the neck's top and encloses
  shank and holder. A dovetail whose shank widens right from the head has no
  reach past its head. If Fusion states no narrower neck, the tool is skipped.
- Arbor-mounted slitting saws (`LB` = `LCF`: a blade without a shank) get a
  neck of 0.001 mm, because CamCraft needs one, with a warning. The Fusion
  holder starts right on the blade, so it must be the saw's arbor: CamCraft
  then holds a cut where the arbor gets too wide. Without one the default
  holder stands there, and CamCraft holds anything deeper than the blade is
  thick. Fusion does not model the screw or flange under the blade, so
  CamCraft does not see it.
- Drills and spot drills with a point of 60 degrees or less (many "drill/end
  mills") become a `taper` (chamfer mill) with the same cone, with a warning:
  CamCraft wants a point over 60 degrees on a drill. Its Drill operation does
  not take a chamfer mill; Engrave, for one, does. The speeds follow Fusion's
  drill rules, as for flat bottom drills below.
- Flat bottom drills (point angle `SIG` 180) become a center-cutting
  `end_mill`, with a warning. CamCraft refuses a tip angle of 180 and wants a
  point on every drill, and its drilling cycles take only drills, taps and
  boring bars. So the tool can't be drilled with a cycle. Its flat-floor
  operations (Pocket, Circular, milled Bore and the others that take an end
  mill) can plunge it, because it is center cutting. Its speeds still follow
  Fusion's drill rules: the feed is the plunge feed `v_f_plunge`, and a
  `--default-* drill=` value applies to it.
- Chamfer mills, countersinks and drills with a flat tip (`tip-diameter`) are
  written with a sharp point, with a warning: CamCraft has no tip diameter yet
  ([NeoTech/Camcraft#895](https://github.com/NeoTech/Camcraft/issues/895)).
  Near the tip the real cutter is wider than CamCraft's cone.
- Single-form thread mills (`NT` 1, stored without `LCF`) cut any pitch from
  `TPN` to `TPX`. Their flute length is one coarsest pitch, the V's own
  height. CamCraft knows one shank cylinder, so the **neck** is written as the
  shank, up to `shoulder-length`, and the real shank is drawn at the bottom of
  the holder body (with the default holder above it if Fusion has none).
  Without a `shoulder-diameter` the neck is the tooth root at the coarsest
  pitch, `DC - 2 x 0.5413 x TPX`, with a warning: a wider neck could not cut
  that thread over its reach. With `--holder-cylinders` the one cylinder
  starts at the neck's top and encloses shank and holder. A thread mill that
  has an `LCF` stays multi-form, whatever its `NT`: in real libraries the same
  cutter is stored with `NT` 1 and 3, so `NT` alone does not tell.
- Corner chamfer end mills become a `bull_nose`, with a warning: CamCraft has
  no chamfered corner. The corner radius is the chamfer's shorter leg (for a
  45 degree chamfer: `chamfer-width`), so the written cutter is never smaller
  than the real one and closer to it than a sharp end mill. Rest material in
  corners is understated. Without chamfer numbers it is a sharp `end_mill`.
  **Not yet tried on a real Fusion tool** ([#18](https://github.com/bnegt/Fusitool2Craftool/issues/18)).
- Holder: a tool's Fusion holder is drawn as it is. Its segments (from the
  tool end up to the gauge line) become a CamCraft drawn holder body
  (`body.spun`), seated on the shank's top. CamCraft's collision checks then
  read the real outline (nut, neck, flange) instead of one fat cylinder,
  and the import reports "N tools; M with their holder". The cylinder
  (largest diameter x total height) is still written as `holder_d` /
  `holder_len`. Points closer than 0.02 mm merge, keeping the wider one, so
  the drawn holder is never thinner than Fusion's. A holder CamCraft cannot
  draw (narrowing to the axis, over 64 points or 2000 x 500 mm) keeps the
  cylinder, with a warning. `--holder-cylinders` writes only cylinders.
  Needs CamCraft 0.20.0 or newer to read the body; older versions use the
  cylinder.
- Speeds: the preset's `n` and `v_f`. Fusion leaves `v_f` empty on drills
  and taps, so a drill takes its plunge feed `v_f_plunge` (or feed per rev
  `f_n` x rpm) and a tap feeds rpm x pitch, which is what CamCraft's tapping
  cycle runs; a warning names the source. Older libraries (format version 1)
  keep one unnamed preset as `start-values: {"*": ...}`; it is read the same.
  Stepdown/stepover go into a cutting data row (`unknown` material unless
  `--material` is given). With `--all-presets` every preset can become a
  row of its own, see below.
- Stand-alone holders: holder entries (and holder-only libraries) are
  counted and reported, not converted on their own; `--default-holder` draws
  one on every tool that has no holder. CamCraft 0.26.0's holder library is
  built in (BT, CAT, ER, TTS) and `.camtools` carries tools only.
- Vendor, product id, product link, flute count, tool material (HSS/carbide)
  and the Fusion tool number are carried over.

### Cutting data per material

A Fusion tool often has several presets (aluminium, steel, plastic, ...).
By default only one becomes the tool's rpm/feed (`--preset`, or the first).
With `--all-presets`, each preset whose **name** names a CamCraft material
becomes a cutting data row for that material, so one file carries them all:

| Preset name contains | Row |
|---|---|
| aluminum/aluminium, 6061, 7075, ... | `aluminium_6061` |
| steel, mild, low carbon, 1018, 4140, ... | `mild_steel` |
| stainless, 303/304/316, inox | `stainless_304` |
| cast iron, gray/grey iron | `grey_cast_iron` |
| brass | `brass` |
| POM, acetal, Delrin, plastic | `plastics_pom` |
| softwood/hardwood, MDF, plywood, acrylic, HDPE | `softwood`, `hardwood`, `mdf`, `plywood`, `acrylic`, `hdpe` |

Fusion's own preset material field is empty in nearly every library, so the
name is the only lead, and nothing is guessed: a name naming two materials
("Aluminum/Copper/Brass"), a hardened grade ("P20 (30-40 HRC) tool steel",
"440C (30-40 HRC)"), titanium, copper, graphite or composites gives no row.
The first preset for a material wins. Every preset that does not become a
row is listed with the reason (`unmatched`, `duplicate <material>`,
`no speeds`, or needs a newer CamCraft), counted over the library.

### Lathe tools

| Fusion | CamCraft |
|---|---|
| nose radius `RE` (round insert: `INSD` / 2) | `nose_radius` (turning, boring) |
| `tool_grooveWidth`, the cutting width | blade `width` (`tool_insertWidth` is the body behind the edge) |
| `TP`, 60 degree form | thread V `width` = 0.866 x `TP`, `tip_angle` 60 |
| holder `W` x `H`, head `LH` | shank diameter (the larger), length of the head |
| surface speed `v_c`, feed per turn `f_n` | `turning` data: G96 and feed per turn |
| hand, with the usual mounting | `orientation` 2 (right) / 1 (left), 3 for a right hand bar |

- **Speeds:** a lathe preset has no rpm, and CamCraft wants one, so rpm and
  feed are written for a 50 mm work diameter (`--turning-d`). CamCraft posts
  the surface speed and the feed per turn; `--max-rpm` also becomes the tool's
  rpm limit. Threads run at a constant rpm, one pitch per turn. Presets become
  no material rows: a row holds an rpm. A feed of 0.8 mm per turn or more is
  warned about: Fusion's untouched preset is 1 mm.
- **Boring bars:** the tip stands `CW` from the bar's axis, so the bar reaches
  `CW + W/2` across. CamCraft's bar has no offset, so head and shank are both
  written that wide: the smallest body that covers the real bar, and CamCraft
  then refuses a hole it cannot enter. Fusion has no stick-out, so the whole
  bar's length is written, with a warning. It can trip a machine's tool length
  limit: set the real one in CamCraft.
- **Outside tools** end at their head; the turret clamps the shank. CamCraft
  judges their cut by the nose, blade or V, not by the insert's outline or the
  holder. Nothing warns if an insert's flank or the holder would rub.
- **Orientation** is left to CamCraft (2, or 3 for a bar), with a warning, for
  neutral tools, left hand bars, other angles, a reversed spindle or another
  compensation point than Fusion's tip tangent. A blade's touch-off corner is
  always CamCraft's default: check it ([#20](https://github.com/bnegt/Fusitool2Craftool/issues/20)).
- A blade's corner radius is not written (CamCraft's blade is square), with a
  warning. A partial profile threading insert is written for its `TP` (the
  coarsest pitch, in vendor libraries), with a warning if Fusion states a range.

### Default speeds

Vendor catalogs often have no presets, or only a placeholder. Without help
those tools are skipped; with defaults they get speeds, by kind and by size.
A placeholder is a preset of 1 rpm whose feeds are all 1, 0 or missing (Harvey,
Helical, SwiftCARB, Valor and MA Ford ship every tool with one). It states no
speeds, so another preset is used if there is one
([#35](https://github.com/bnegt/Fusitool2Craftool/issues/35)). 1 rpm with a
real feed is kept: that is a drag engraver.

| Option | Gives |
|---|---|
| `--default-rpm 18000` / `--default-feed 1000` | one value for every tool |
| `--default-vc 150` | rpm = v_c x 1000 / (pi x D): a 0.5 mm end mill spins far faster than a 25 mm one |
| `--default-fz 0.03` | feed = rpm x flutes x f_z, using Fusion's flute (tooth) count |
| `--turning-d 50` | work diameter for a lathe tool's rpm, from Fusion's surface speed or `--default-vc` |
| `--max-rpm 24000` | caps an rpm computed from `--default-vc` (an explicit `--default-rpm` is not capped) |
| `KIND=value` on any of them | that kind only, e.g. `--default-vc drill=60 --default-feed drill=150`; repeat per kind |

Kinds are CamCraft's: `end_mill`, `ball_nose`, `bull_nose`, `drill`, `tap`,
`taper`, `thread_mill`, `thread_mill_single`, `bore`, `turning`, `groove`, `thread`, `t_slot`, `keyseat`, `lollipop`, `dovetail`.
Order for a missing value: the kind's own default beats the global one, and
at each level an explicit rpm/feed beats v_c/f_z. A tap always feeds
rpm x pitch. A value Fusion does state is never replaced. Every tool that
gets a default is warned about, the run ends with a count per default, and
the output check requires exactly the value the options give.

Choose the numbers for your machine and material: they are starting
values, not cutting data.

### Tapered mills

CamCraft 0.26.0 has no tapered cutter. Its `main` has one: a `taper` with a flat
or ball tip, in `.camtools` version 6
([NeoTech/Camcraft#3368](https://github.com/NeoTech/Camcraft/issues/3368)).
`--unreleased-tapers` writes tapered mills for such a build
([#10](https://github.com/bnegt/Fusitool2Craftool/issues/10)). It is off by
default, because **0.26.0 refuses the whole file**, and a newer build imports
the tools but **no operation cuts with a flat or ball tip yet**.

| CamCraft | from Fusion |
|---|---|
| `tip_angle` | 2 x `TA` (`TA` is off the axis) |
| `tip_form` | `ball` where `RE` leaves no flat face or is half of `DC`, else `flat` |
| `tip_diameter` (flat) | `DC`, the cone's diameter at the tip |
| `corner_r` | `RE`: the corner of a flat tip, or the ball (see below) |
| `diameter` | the widest: `DC + 2 x LCF x tan TA` |

- A ball whose `RE` is half of `DC` (Garr, Mitsubishi, SpeTool and others) is
  written as the ball that fits Fusion's cone, with a warning. It is 2 to 14 %
  larger than the real one, so the real tip leaves slightly higher scallops. A
  ball of exactly `RE` would have a thinner flank than Fusion's, and the small
  flat Fusion draws would keep the tool out of CamCraft's planned 3D Finish,
  which takes only ball tips
  ([NeoTech/Camcraft#3374](https://github.com/NeoTech/Camcraft/issues/3374)).
  Where that ball is over 20 % larger (a steep cone), it stays a flat tip with
  a rounded corner. Harvey's `DC` already fits the ball.
- A cone wider than the shank at the top of the flutes gets a warning: it is
  written as Fusion has it, and the flute length may be wrong.
- `--default-vc` is computed at the widest diameter, not at the tip.
- A tapered mill with `TA` 0 is a straight cutter and needs no flag: `end_mill`,
  `bull_nose` or `ball_nose`, unless its name says taper (then it is skipped).
- Chamfer mills and V-bits with a flat tip stay sharp, with their warning:
  with a flat tip no CamCraft operation would take them.

### Not converted

| Fusion tool | Why |
|---|---|
| tapered ball mill / tapered mill | CamCraft 0.26.0 has no tapered cutter; with `--unreleased-tapers` they are written for a newer build ([Tapered mills](#tapered-mills)) |
| roundover bit (ball end mill with radius > D/2) | CamCraft has no form/roundover cutter |
| reamer, counterbore | CamCraft has no kind for them ([NeoTech/Camcraft#897](https://github.com/NeoTech/Camcraft/issues/897)) |
| form mill | CamCraft has no custom-profile mill (its `Form` kind is a lathe form gauge) |
| internal and face grooving, round grooving, internal threading tools | CamCraft's blade and threading insert are outside tools, and its blade is square |
| threading tools without a pitch (`TP`) or with a form other than 60 degrees | no known V width ([#20](https://github.com/bnegt/Fusitool2Craftool/issues/20)) |
| dovetail mill with a corner radius | CamCraft's dovetail has a sharp corner. Fusion's `DC` is at the top of the radius, so CamCraft's whole flank would be too thin (up to 1.3 mm in vendor libraries) and it would miss a gouge ([NeoTech/Camcraft#4004](https://github.com/NeoTech/Camcraft/issues/4004)) |
| circle segment mill (barrel, lens, oval, taper) | CamCraft has no kind for them |
| probe | not a cutter |
| waterjet, laser and plasma cutter; laser powder, laser wire and electric arc wire | cutting and additive heads, not cutters |
| `shaft`, `tool block` | parts of a holder, not cutters |
| milling or drilling tools without a flute length (`LCF`) | nothing else Fusion states gives the cutting length: CamCraft would think the tool cuts along `shoulder-length`, `LB` or `OAL`, and could bury the shank or neck. Give the tool a flute length in Fusion. Single-form thread mills are the exception: their tooth height follows from the pitch |
| tools without rpm/feed, or with only a placeholder | skipped unless a [default](#default-speeds) is given |

A ball end mill whose radius is within 10 % of D/2 is still a ball nose
(vendors round imperial radii, e.g. 1/8" listed as R1.5). A tool CamCraft
would refuse is skipped too, because CamCraft refuses a whole library over a
single bad tool. All skipped tools are listed in red with the reason; with
`--strict` the run fails instead.

**Tool numbers:** the Fusion tool number is written as `number`, but
CamCraft does not import numbers from a `.camtools` file (only from
PathPilot/Hurco tool tables): it gives imported tools its own lowest free T
numbers. Tools that share a Fusion number therefore never clash.

## Development

```sh
python3 -m unittest discover -s tests -t .
```

Put your own Fusion libraries in `private/` (git-ignored), for example a whole
ExportAllToolLibraries export; subfolders are searched. The tests then also
check that every library converts and verifies, and that any skipped tool
belongs to a named unsupported family (tapered, roundover, reamer, ...). That
last check leaves out `private/test-libraries/`, the place for other people's
libraries.

Real third-party libraries to test against (RobbJack, Haas/Matsuura/Tormach
shop libraries, Carbide 3D, ...) are listed in
[TEST_LIBRARIES.md](TEST_LIBRARIES.md), with a script that downloads them
into `private/test-libraries/`. `tests/get_autodesk_libraries.py` fetches
Autodesk's public vendor libraries (Harvey, Sandvik, Iscar, Haas, ...;
134,498 entries in 45 files).

### Testing against CamCraft

`tests/test_camcraft_live.py` imports converted libraries into a real,
headless CamCraft: the sample fixture, an edge-case library (inch units,
left-hand tap, drill/tap feeds, legacy presets, material rows, thread mills,
corner chamfers, chamfer, face mill, boring bar, undercut cutters, lathe tools)
and every library in `private/`. Every tool written must come back as added, none
renamed or skipped. It runs when
`camcraft-cli` is on `PATH` or `CAMCRAFT_CLI` points at it, and is skipped
otherwise:

```sh
CAMCRAFT_CLI=~/camcraft-0.26.0-linux-x86_64/camcraft-cli \
    python3 -m unittest discover -s tests -t .
```

Last run (CamCraft 0.26.0, 2026-11-25): 65 libraries, 134,623 tools written
with default speeds (23,951 without), all imported, none renamed or skipped,
with and without `--all-presets` (37,276 material rows). 40 of the libraries
and 126,936 of the tools are Autodesk's vendor libraries; with them the whole
suite takes about 2.5 minutes (1 minute without CamCraft).

Converted lathe tools turn, bore, groove and thread a part in CamCraft, and
a boring bar is held in a hole it does not fit. The live tests also generate
a real thread in CamCraft with a converted single-form thread mill: it must
verify clean within the reach, and be held for a shank collision deeper than
the reach, or with the plain shank. A shank no wider than the teeth is not
caught by CamCraft past the reach, so mind the `shoulder-length` there.
Keyseat and dovetail cutters with a shank as wide as their head cut an undercut
slot clean within the neck's reach and are held for a shank collision past it.

### CamCraft upstream check

The first tests to run (`tests/test_camcraft_upstream.py`) ask GitHub whether
CamCraft can now handle the tools we skip
([#1](https://github.com/bnegt/Fusitool2Craftool/issues/1)):

- CamCraft issues [#895](https://github.com/NeoTech/Camcraft/issues/895)
  (tapered ball mills, V-bit tip diameter),
  [#896](https://github.com/NeoTech/Camcraft/issues/896) (roundover bits),
  [#897](https://github.com/NeoTech/Camcraft/issues/897) (reamers,
  counterbores), [#3368](https://github.com/NeoTech/Camcraft/issues/3368)
  (tapered mill tip forms; closed, waiting for a release) and
  [#4004](https://github.com/NeoTech/Camcraft/issues/4004) (dovetails with a
  corner radius) are still open
- the tool kinds, material words and `.camtools` versions of CamCraft's
  **latest release** are unchanged (what is only on main gives a skip note)

If either fails, CamCraft has changed: update the converter, then update
`BLOCKING_ISSUES` / `KNOWN_KINDS` in that test. Offline (or rate-limited) the
check is skipped; `F2C_OFFLINE=1` skips it on purpose, and `GITHUB_TOKEN`
raises GitHub's rate limit.

| Module | Role |
|---|---|
| `fusion.py` | read `.tools` (zip or plain JSON) and `.json` libraries |
| `convert.py` | map Fusion tools to CamCraft definitions, apply CamCraft's sanity rules |
| `lathe.py` | the same for lathe tools |
| `camtools.py` | write the `.camtools` envelope |
| `verify.py` | read the written file back and compare it with the Fusion source |
| `push.py` | import into CamCraft through `camcraft-cli mcp` (`--push`, `--check`) |
| `cli.py` | command line (one library or a folder of them), coloured report |
| `fusion_script/` | Fusion script that exports all tool libraries (runs inside Fusion, not part of the package) |
