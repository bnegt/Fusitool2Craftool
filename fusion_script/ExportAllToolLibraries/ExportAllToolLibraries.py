"""Export every Local, Cloud and Hub tool library to .json files, one file per library.

Runs inside Fusion (Shift+S -> Scripts). Read-only: it reads libraries and
writes files into the folder you pick; nothing in Fusion or your account is
changed. Autodesk's own "Fusion Library" samples are skipped unless
INCLUDE_FUSION_SAMPLES is set below. A log is written
to <folder>/export-log.txt.

Convert the whole export to CamCraft afterwards with:
    python3 -m fusitool2craftool <folder>
"""
import os
import re
import traceback

import adsk.cam
import adsk.core

# Folder the picker opens in; the export goes into a subfolder of your choice.
DEFAULT_OUT = os.path.join(os.path.expanduser('~'), 'Documents')
SUBFOLDER = 'Fusion tool libraries'
# True also exports Autodesk's built-in "Fusion Library" samples (14 libraries,
# 29 tool types; handy as converter test data) into a Fusion/ subfolder.
INCLUDE_FUSION_SAMPLES = False


def safe(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip() or 'library'


def unique_path(folder, name, taken):
    """<folder>/<name>.json, numbered if another library already took it."""
    base = name[:-5] if name.lower().endswith('.json') else name
    path = os.path.join(folder, base + '.json')
    i = 2
    while path.lower() in taken:
        path = os.path.join(folder, '%s (%d).json' % (base, i))
        i += 1
    taken.add(path.lower())
    return path


def pick_folder(ui):
    dlg = ui.createFolderDialog()
    dlg.title = 'Export all tool libraries to (a "%s" folder is made inside)' % SUBFOLDER
    if os.path.isdir(DEFAULT_OUT):
        dlg.initialDirectory = DEFAULT_OUT
    if dlg.showDialog() != adsk.core.DialogResults.DialogOK:
        return None
    return os.path.join(dlg.folder, SUBFOLDER)


def export_all(libs, out, log):
    done, failed, taken = 0, 0, set()
    L = adsk.cam.LibraryLocations
    locations = [('Local', L.LocalLibraryLocation), ('Cloud', L.CloudLibraryLocation)]
    if hasattr(L, 'HubLibraryLocation'):
        locations.append(('Hub', L.HubLibraryLocation))
    if INCLUDE_FUSION_SAMPLES:
        # The API names it Fusion360LibraryLocation (the UI says "Fusion Library").
        loc = getattr(L, 'Fusion360LibraryLocation', None)
        if loc is None:
            loc = getattr(L, 'FusionLibraryLocation', None)
        if loc is None:
            log.append('Fusion: no sample library location in this Fusion API')
        else:
            locations.append(('Fusion', loc))
    for label, loc in locations:
        try:
            root = libs.urlByLocation(loc)
        except Exception as e:
            log.append('%s: cannot open location: %s' % (label, e))
            continue
        stack = [(root, label)]
        while stack:
            url, rel = stack.pop()
            try:
                for child in libs.childFolderURLs(url):
                    stack.append((child, os.path.join(rel, safe(child.leafName))))
                assets = libs.childAssetURLs(url)
            except Exception as e:
                log.append('%s: cannot list: %s' % (rel, e))
                failed += 1
                continue
            for asset in assets:
                name = safe(asset.leafName)
                try:
                    text = libs.toolLibraryAtURL(asset).toJson()
                    folder = os.path.join(out, rel)
                    os.makedirs(folder, exist_ok=True)
                    with open(unique_path(folder, name, taken), 'w', encoding='utf-8') as f:
                        f.write(text)
                    done += 1
                except Exception as e:
                    log.append('%s/%s: %s' % (rel, name, e))
                    failed += 1
    return done, failed


def run(context):
    ui = adsk.core.Application.get().userInterface
    try:
        out = pick_folder(ui)
        if not out:
            return
        log = []
        try:
            done, failed = export_all(adsk.cam.CAMManager.get().libraryManager.toolLibraries, out, log)
        except Exception:
            done, failed = 0, 1
            log.append(traceback.format_exc())
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, 'export-log.txt'), 'w', encoding='utf-8') as f:
            f.write('exported %d libraries, %d failed\n' % (done, failed) + '\n'.join(log) + '\n')
        ui.messageBox(
            'Exported %d tool libraries to\n%s\n(%d failed; see export-log.txt)\n\n'
            'Convert them all for CamCraft with:\npython3 -m fusitool2craftool "%s"'
            % (done, out, failed, out))
    except Exception:
        ui.messageBox('ExportAllToolLibraries failed:\n%s' % traceback.format_exc())
