from __future__ import annotations

import argparse
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


IMPORTANT_FIELDS = {
    "midlet-name",
    "midlet-vendor",
    "midlet-version",
}


@dataclass
class MidletComparison:
    matching: list[tuple[str, str]]
    mismatching: list[tuple[str, str, str]]
    jad_only: list[tuple[str, str]]
    manifest_only: list[tuple[str, str]]


@dataclass
class CandidateScore:
    jar: Path
    score: int
    expected_name_match: bool
    size_match: bool | None
    comparison: MidletComparison
    reasons: list[str]


@dataclass
class RenamePlan:
    old_jad: Path
    old_jar: Path
    new_jad: Path
    new_jar: Path


def normalize_value(value: str) -> str:
    """Trim and normalize whitespace, while keeping value comparison case-sensitive."""
    return " ".join(value.strip().split())


def parse_properties(text: str) -> dict[str, str]:
    """
    Parse JAD / MANIFEST.MF style key-value lines.

    Handles manifest continuation lines that begin with a single space.
    """
    props: dict[str, str] = {}
    current_key: str | None = None

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw_line:
            continue

        if raw_line.startswith(" ") and current_key:
            props[current_key] += raw_line[1:].strip()
            continue

        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        if key:
            props[key] = value
            current_key = key

    return props


def read_jad(path: Path) -> dict[str, str]:
    return parse_properties(path.read_text(encoding="utf-8-sig"))


def read_manifest_from_jar(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path, "r") as jar:
        for name in jar.namelist():
            if name.upper() == "META-INF/MANIFEST.MF":
                data = jar.read(name)
                return parse_properties(data.decode("utf-8-sig"))
    raise ValueError("META-INF/MANIFEST.MF not found")


def casefold_props(props: dict[str, str]) -> dict[str, tuple[str, str]]:
    return {key.casefold(): (key, value) for key, value in props.items()}


def midlet_props(props: dict[str, str]) -> dict[str, tuple[str, str]]:
    return {
        key.casefold(): (key, value)
        for key, value in props.items()
        if key.casefold().startswith("midlet-")
    }


def compare_all_midlet_fields(jad_props: dict[str, str], manifest_props: dict[str, str]) -> MidletComparison:
    """Compare every MIDlet-* value that appears in either the JAD or manifest."""
    jad_midlets = midlet_props(jad_props)
    manifest_midlets = midlet_props(manifest_props)

    shared_keys = sorted(set(jad_midlets) & set(manifest_midlets))
    jad_only_keys = sorted(set(jad_midlets) - set(manifest_midlets))
    manifest_only_keys = sorted(set(manifest_midlets) - set(jad_midlets))

    matching: list[tuple[str, str]] = []
    mismatching: list[tuple[str, str, str]] = []

    for key_cf in shared_keys:
        jad_key, jad_value = jad_midlets[key_cf]
        _manifest_key, manifest_value = manifest_midlets[key_cf]

        if normalize_value(jad_value) == normalize_value(manifest_value):
            matching.append((jad_key, jad_value))
        else:
            mismatching.append((jad_key, jad_value, manifest_value))

    jad_only = [(jad_midlets[key][0], jad_midlets[key][1]) for key in jad_only_keys]
    manifest_only = [(manifest_midlets[key][0], manifest_midlets[key][1]) for key in manifest_only_keys]

    return MidletComparison(
        matching=matching,
        mismatching=mismatching,
        jad_only=jad_only,
        manifest_only=manifest_only,
    )


def expected_jar_filename(jad_props: dict[str, str]) -> str | None:
    props_cf = casefold_props(jad_props)
    item = props_cf.get("midlet-jar-url")
    if not item:
        return None

    _key, raw_url = item
    raw_url = raw_url.strip()
    if not raw_url:
        return None

    parsed = urlparse(raw_url)
    filename = Path(unquote(parsed.path)).name

    if not filename:
        return None

    return filename


def score_candidate(jad_props: dict[str, str], jar_path: Path, manifest_props: dict[str, str]) -> CandidateScore:
    score = 0
    reasons: list[str] = []

    wanted_name = expected_jar_filename(jad_props)
    expected_name_match = bool(wanted_name and jar_path.name.casefold() == wanted_name.casefold())
    if expected_name_match:
        score += 100
        reasons.append(f"current JAR filename already matches MIDlet-Jar-URL: {wanted_name}")

    size_match: bool | None = None
    props_cf = casefold_props(jad_props)
    jar_size_item = props_cf.get("midlet-jar-size")
    if jar_size_item:
        _key, jar_size_text = jar_size_item
        try:
            expected_size = int(jar_size_text.strip())
            actual_size = jar_path.stat().st_size
            size_match = expected_size == actual_size
            if size_match:
                score += 125
                reasons.append(f"MIDlet-Jar-Size matches actual JAR size: {actual_size}")
            else:
                score -= 100
                reasons.append(f"MIDlet-Jar-Size mismatch: JAD says {expected_size}, actual is {actual_size}")
        except ValueError:
            reasons.append(f"MIDlet-Jar-Size is not numeric: {jar_size_text!r}")

    comparison = compare_all_midlet_fields(jad_props, manifest_props)

    for key, value in comparison.matching:
        key_cf = key.casefold()
        score += 20
        if key_cf in IMPORTANT_FIELDS:
            score += 30
        reasons.append(f"{key} matches")

    for key, jad_value, manifest_value in comparison.mismatching:
        key_cf = key.casefold()
        score -= 25
        if key_cf in IMPORTANT_FIELDS:
            score -= 75
        reasons.append(f"{key} mismatch: JAD={jad_value!r}, MANIFEST={manifest_value!r}")

    return CandidateScore(
        jar=jar_path,
        score=score,
        expected_name_match=expected_name_match,
        size_match=size_match,
        comparison=comparison,
        reasons=reasons,
    )


def target_paths(jad_path: Path, jar_path: Path, wanted_jar_name: str) -> tuple[Path, Path]:
    wanted = Path(wanted_jar_name).name
    stem = Path(wanted).stem
    return jad_path.with_name(stem + ".jad"), jar_path.with_name(stem + ".jar")


def path_is_same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False


def find_collision(paths: list[tuple[Path, Path]]) -> str | None:
    """
    Return a collision message if a rename target already exists and is not the same file.
    paths contains (old_path, new_path) pairs.
    """
    for old_path, new_path in paths:
        if new_path.exists() and not path_is_same_file(old_path, new_path):
            return f"target already exists: {new_path}"
    return None


def safe_rename(plan: RenamePlan, dry_run: bool) -> bool:
    collision = find_collision([
        (plan.old_jad, plan.new_jad),
        (plan.old_jar, plan.new_jar),
    ])
    if collision:
        print(f"  SKIP: {collision}")
        return False

    print(f"  Rename JAD: {plan.old_jad.name} -> {plan.new_jad.name}")
    print(f"  Rename JAR: {plan.old_jar.name} -> {plan.new_jar.name}")

    if dry_run:
        print("  DRY RUN: no files renamed")
        return True

    # Use temporary names so a partial overlap or case-only rename is safer.
    temp_jad = plan.old_jad.with_name(plan.old_jad.name + ".renaming_tmp")
    temp_jar = plan.old_jar.with_name(plan.old_jar.name + ".renaming_tmp")

    if temp_jad.exists() or temp_jar.exists():
        print("  SKIP: temporary rename file already exists")
        return False

    try:
        if not path_is_same_file(plan.old_jad, plan.new_jad):
            plan.old_jad.rename(temp_jad)
        else:
            temp_jad = plan.old_jad

        if not path_is_same_file(plan.old_jar, plan.new_jar):
            plan.old_jar.rename(temp_jar)
        else:
            temp_jar = plan.old_jar

        if not path_is_same_file(temp_jad, plan.new_jad):
            temp_jad.rename(plan.new_jad)
        if not path_is_same_file(temp_jar, plan.new_jar):
            temp_jar.rename(plan.new_jar)

        print("  Renamed")
        return True
    except Exception as exc:
        print(f"  ERROR while renaming: {exc}")
        print("  Some files may already have been moved to *.renaming_tmp; check the folder before rerunning.")
        return False


def print_verbose_comparison(comparison: MidletComparison) -> None:
    if comparison.matching:
        print("  Matching MIDlet-* fields:")
        for key, value in comparison.matching:
            print(f"    = {key}: {value}")

    if comparison.mismatching:
        print("  Mismatching MIDlet-* fields:")
        for key, jad_value, manifest_value in comparison.mismatching:
            print(f"    ! {key}: JAD={jad_value!r} | MANIFEST={manifest_value!r}")

    if comparison.jad_only:
        print("  MIDlet-* fields only in JAD:")
        for key, value in comparison.jad_only:
            print(f"    JAD only: {key}: {value}")

    if comparison.manifest_only:
        print("  MIDlet-* fields only in MANIFEST.MF:")
        for key, value in comparison.manifest_only:
            print(f"    MANIFEST only: {key}: {value}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Match JAD/JAR pairs and rename both to the filename from MIDlet-Jar-URL."
    )
    parser.add_argument("folder", help="Folder containing .jad and .jar files")
    parser.add_argument("--recursive", action="store_true", help="Search subfolders too")
    parser.add_argument("--dry-run", action="store_true", help="Preview matches without renaming")
    parser.add_argument("--verbose", action="store_true", help="Print every compared MIDlet-* field")
    parser.add_argument(
        "--min-score",
        type=int,
        default=120,
        help="Minimum confidence score required before renaming. Default: 120",
    )
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists() or not folder.is_dir():
        print(f"Folder not found: {folder}", file=sys.stderr)
        return 2

    globber = folder.rglob if args.recursive else folder.glob
    jad_files = sorted(p for p in globber("*.jad") if p.is_file())
    jar_files = sorted(p for p in globber("*.jar") if p.is_file())

    print(f"Found {len(jad_files)} JAD file(s) and {len(jar_files)} JAR file(s)")
    print("Mode: " + ("DRY RUN" if args.dry_run else "RENAME"))

    jar_manifests: dict[Path, dict[str, str]] = {}
    for jar in jar_files:
        try:
            jar_manifests[jar] = read_manifest_from_jar(jar)
        except Exception as exc:
            print(f"Skipping JAR without readable UTF-8 manifest: {jar.name} ({exc})")

    used_jars: set[Path] = set()
    renamed_count = 0
    skipped_count = 0

    for jad in jad_files:
        print(f"\nJAD: {jad.name}")

        try:
            jad_props = read_jad(jad)
        except Exception as exc:
            print(f"  SKIP: could not read JAD as UTF-8: {exc}")
            skipped_count += 1
            continue

        wanted_jar_name = expected_jar_filename(jad_props)
        if not wanted_jar_name:
            print("  SKIP: no filename found in MIDlet-Jar-URL")
            skipped_count += 1
            continue

        print(f"  Target names: {Path(wanted_jar_name).stem}.jad / {Path(wanted_jar_name).stem}.jar")

        candidates: list[CandidateScore] = []
        for jar, manifest in jar_manifests.items():
            if jar in used_jars:
                continue
            candidates.append(score_candidate(jad_props, jar, manifest))

        if not candidates:
            print("  SKIP: no readable JAR candidates available")
            skipped_count += 1
            continue

        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]

        print(f"  Best JAR: {best.jar.name}")
        print(f"  Score: {best.score}")
        print(
            "  MIDlet-* shared fields: "
            f"{len(best.comparison.matching)} match, "
            f"{len(best.comparison.mismatching)} mismatch, "
            f"{len(best.comparison.jad_only)} JAD-only, "
            f"{len(best.comparison.manifest_only)} manifest-only"
        )

        if args.verbose:
            print_verbose_comparison(best.comparison)

        if best.score < args.min_score:
            print(f"  SKIP: best score is below --min-score {args.min_score}")
            skipped_count += 1
            continue

        new_jad, new_jar = target_paths(jad, best.jar, wanted_jar_name)
        plan = RenamePlan(jad, best.jar, new_jad, new_jar)

        if safe_rename(plan, dry_run=args.dry_run):
            used_jars.add(best.jar)
            renamed_count += 1
        else:
            skipped_count += 1

    print(f"\nDone. Matched/processed: {renamed_count}. Skipped: {skipped_count}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
