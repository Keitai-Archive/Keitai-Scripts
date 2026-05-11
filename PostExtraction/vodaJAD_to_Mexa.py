#!/usr/bin/env python3
from pathlib import Path
import shutil
import sys

REPLACEMENTS = {
    "MIDlet-OCL: JSCL-1.2.0": "MIDxlet-API: JSCL-1.3.2",
    "MIDlet-OCL: JSCL-1.3.2": "MIDxlet-API: JSCL-1.3.2",
}

def patch_jad(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    original = text

    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)

    if text == original:
        print(f"[SKIP] No matching MIDlet-OCL entry in {path}")
        return

    # Create backup once
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)

    path.write_text(text, encoding="utf-8")
    print(f"[OK] Patched {path} (backup: {bak.name})")

def main():
    if len(sys.argv) < 2:
        print("Usage: python patch_jad.py <file.jad> [more.jad ...]")
        sys.exit(2)

    for p in map(Path, sys.argv[1:]):
        if not p.exists() or p.suffix.lower() != ".jad":
            print(f"[SKIP] Not a .jad file or doesn't exist: {p}")
            continue
        patch_jad(p)

if __name__ == "__main__":
    main()
