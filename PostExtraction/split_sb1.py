"""Export decrypted SB1 applications and media to their native files.

Usage:
    python split_sb1.py INPUT_FILE_OR_DIRECTORY OUTPUT_DIRECTORY
    python split_sb1.py INPUT OUTPUT --header-size 0
    python split_sb1.py INPUT OUTPUT --dry-run

Python standard library only. Input directories are searched recursively.
The default clear SB1 header is 4096 bytes. Encrypted SB1s must be decrypted
first. Applications become .jar/.jam/.sp files in individual backup folders,
named using PackageURL. Media becomes .jpg/.gif/.png/.3gp/.mp4/.mht files,
keeping the source subfolders and using the header title or SB1 filename.
"""
import argparse
import binascii
from collections import Counter
from email import policy
from email.parser import BytesParser
import hashlib
import io
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, unquote, urlsplit
import zipfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(jam):
    result = {}
    for line in jam.decode('cp932', errors='replace').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            result.setdefault(key.strip().lower(), value.strip().strip('\0'))
    return result


def split(raw, header_size):
    data = raw[header_size:]
    require(data[:2] == b'\x01\x04',
            'No decrypted SB1 metadata at the requested header offset; check decryption/header size.')
    limit = int.from_bytes(data[2:6], 'big')
    require(6 <= limit <= len(data), 'Invalid SB1 metadata length')
    cursor = 0
    tags = {}
    while cursor < limit:
        require(cursor + 2 <= limit, 'Truncated metadata tag')
        tag, size = data[cursor:cursor + 2]
        cursor += 2
        if size == 255:
            require(cursor + 2 <= limit, 'Truncated extended tag length')
            size = int.from_bytes(data[cursor:cursor + 2], 'big')
            cursor += 2
        require(cursor + size <= limit and tag not in tags, 'Invalid or duplicate metadata tag')
        tags[tag] = data[cursor:cursor + size]
        cursor += size
    require(3 in tags, 'Missing JAM descriptor')
    jam = tags[3]
    metadata = fields(jam)
    require(metadata.get('appsize', '').isdigit(), 'Missing or invalid JAM AppSize')
    jar_size = int(metadata['appsize'])
    require(limit + jar_size <= len(data), 'JAR extends past the SB1 end')
    jar = data[limit:limit + jar_size]
    scratchpad = data[limit + jar_size:]
    require(jar[:4] == b'PK\x03\x04', 'Missing JAR/ZIP signature')
    sp_value = metadata.get('spsize', '')
    sp_sizes = [int(n.strip()) for n in sp_value.split(',')] if sp_value else []
    require(all(n >= 0 for n in sp_sizes) and sum(sp_sizes) == len(scratchpad),
            'Scratchpad length disagrees with JAM SPsize')
    return (jar, jam, scratchpad), metadata, tags.get(2, b'').decode('cp932', errors='replace')


def audit_zip(data, depth=0):
    require(depth < 8, 'Excessive nested JAR depth')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        bad = archive.testzip()
        require(bad is None, f'JAR CRC failed: {bad}')
        for item in archive.infolist():
            if item.filename.lower().endswith('.jar'):
                nested = archive.read(item)
                if nested[:4] == b'PK\x03\x04':
                    audit_zip(nested, depth + 1)


def leaf(value):
    return value.strip().replace('\\', '/').rsplit('/', 1)[-1]


def jar_name(value):
    name = leaf(value)
    return name if len(name) > 4 and name.lower().endswith('.jar') else None


def package_stem(metadata, download_url, source):
    url = metadata.get('packageurl', '')
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True, encoding='cp932', errors='replace')
    # Download endpoints often use a generic path such as dljar.jar, but the
    # query carries the real filename: a=WAMA.jar, f=game.jar, bin=game.jar.
    for key, value in query:
        name = jar_name(value)
        if name:
            return name[:-4], f'PackageURL query parameter {key}'
    for key, value in query:
        if not value:
            name = jar_name(key)  # e.g. down.php?uid=...&i.jar
            if name:
                return name[:-4], 'PackageURL bare query filename'
    name = jar_name(unquote(parts.path, encoding='cp932', errors='replace'))
    if name:
        return name[:-4], 'PackageURL path'
    # Never pretend an opaque PHP/servlet endpoint is a known JAR filename.
    download_name = leaf(unquote(urlsplit(download_url).path, encoding='cp932', errors='replace'))
    if len(download_name) > 4 and download_name.lower().endswith('.jam'):
        return download_name[:-4], 'fallback: saved JAM download URL'
    return metadata.get('appname') or source.stem, 'fallback: AppName or SB1 filename'


def safe_component(value, limit=100):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip().rstrip(' .')
    value = value[:limit].rstrip(' .') or 'unnamed'
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value):
        value = '_' + value
    return value


def one_line(value):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(value))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def clear_header(raw, header_size):
    """Read the unencrypted outer header; do not confuse it with JAM metadata."""
    tags, cursor = {}, 0
    while cursor + 2 <= header_size:
        tag, size = raw[cursor:cursor + 2]
        if tag == 0:
            break
        cursor += 2
        if size == 255:
            require(cursor + 2 <= header_size, 'Truncated outer-header tag length')
            size = int.from_bytes(raw[cursor:cursor + 2], 'big')
            cursor += 2
        require(cursor + size <= header_size and tag not in tags,
                'Invalid or duplicate outer-header tag; check --header-size')
        tags[tag] = raw[cursor:cursor + size]
        cursor += size
    return tags


def audit_iso_boxes(data):
    """Check container boundaries, not audio/video decoding."""
    cursor, types = 0, []
    while cursor < len(data):
        require(cursor + 8 <= len(data), 'Truncated MP4/3GP box header')
        size = int.from_bytes(data[cursor:cursor + 4], 'big')
        kind = data[cursor + 4:cursor + 8]
        header = 8
        if size == 1:
            require(cursor + 16 <= len(data), 'Truncated extended MP4/3GP size')
            size = int.from_bytes(data[cursor + 8:cursor + 16], 'big')
            header = 16
        elif size == 0:
            size = len(data) - cursor
        require(size >= header and cursor + size <= len(data), 'MP4/3GP box extends past payload')
        if not types:
            require(kind == b'ftyp' and size >= header + 8, 'Missing MP4/3GP file-type box')
        types.append(kind)
        cursor += size
    require(b'moov' in types and b'mdat' in types, 'Missing MP4/3GP movie or media-data box')


def audit_png(data):
    cursor, types = 8, []
    while cursor < len(data):
        require(cursor + 12 <= len(data), 'Truncated PNG chunk')
        size = int.from_bytes(data[cursor:cursor + 4], 'big')
        end = cursor + 12 + size
        require(end <= len(data), 'PNG chunk extends past payload')
        kind = data[cursor + 4:cursor + 8]
        body = data[cursor + 8:end - 4]
        crc = int.from_bytes(data[end - 4:end], 'big')
        require(binascii.crc32(kind + body) & 0xffffffff == crc, 'PNG chunk CRC mismatch')
        if not types:
            require(kind == b'IHDR' and size == 13, 'Invalid PNG image header')
            require(int.from_bytes(body[:4], 'big') > 0 and int.from_bytes(body[4:8], 'big') > 0,
                    'Invalid PNG dimensions')
        types.append(kind)
        cursor = end
        if kind == b'IEND':
            require(size == 0 and cursor == len(data), 'Invalid PNG end chunk or trailing data')
            break
    require(types and types[-1] == b'IEND' and b'IDAT' in types, 'Incomplete PNG')


def media_type(data, declared):
    """Require recognizable plaintext, not just a clear-header extension."""
    if data.startswith(b'\xff\xd8\xff'):
        require(data.endswith(b'\xff\xd9'), 'JPEG is missing its end marker')
        extension, validation = '.jpg', 'JPEG start/end signatures; pixels not decoded by this script'
    elif data.startswith((b'GIF87a', b'GIF89a')):
        require(len(data) >= 14 and data.endswith(b';'), 'Incomplete GIF')
        require(int.from_bytes(data[6:8], 'little') > 0 and int.from_bytes(data[8:10], 'little') > 0,
                'Invalid GIF dimensions')
        extension, validation = '.gif', 'GIF header, dimensions and end marker; pixels not decoded by this script'
    elif data.startswith(b'\x89PNG\r\n\x1a\n'):
        audit_png(data)
        extension, validation = '.png', 'PNG chunk boundaries and CRCs verified'
    elif len(data) >= 16 and data[4:8] == b'ftyp':
        audit_iso_boxes(data)
        # 3GP containers can use a vendor major brand, such as mmp4.
        first_size = int.from_bytes(data[:4], 'big')
        brand_start = 16 if first_size == 1 else 8
        if first_size == 1:
            first_size = int.from_bytes(data[8:16], 'big')
        brands = [data[brand_start:brand_start + 4]]
        brands += [data[p:p + 4] for p in range(brand_start + 8, first_size, 4)]
        is_3gp = declared in ('3GP', '3GPP') or any(b.startswith(b'3g') for b in brands)
        extension = '.3gp' if is_3gp else '.mp4'
        if declared == 'MP4':
            extension = '.mp4'
        validation = 'ISO media box boundaries verified; audio/video streams not decoded'
    elif re.search(br'(?im)^mime-version:[ \t]*1\.0\r?$', data[:4096]):
        message = BytesParser(policy=policy.default).parsebytes(data)
        require(not message.defects, 'Malformed MHT MIME structure')
        require(message.is_multipart() or message.get_content_type() == 'text/html',
                'Unsupported MIME payload; expected a saved webpage')
        extension, validation = '.mht', 'Saved-webpage MIME structure verified; original bytes retained'
    else:
        raise ValueError(f'Unsupported or still-encrypted SB1 payload (header format: {declared or "unknown"}); '
                         'check decryption and --header-size')
    aliases = {'JPG': '.jpg', 'JPEG': '.jpg', 'GIF': '.gif', 'PNG': '.png',
               '3GP': '.3gp', '3GPP': '.3gp', 'MP4': '.mp4', 'MHT': '.mht', 'MHTML': '.mht'}
    if declared in aliases:
        require(aliases[declared] == extension, 'SB1 header format disagrees with the decrypted payload')
    return extension, validation


def split_payload(raw, header_size, source):
    require(0 <= header_size < len(raw), 'Header size leaves no payload')
    data = raw[header_size:]
    if data.startswith(b'\x01\x04'):
        payloads, metadata, download_url = split(raw, header_size)
        audit_zip(payloads[0])
        stem, method = package_stem(metadata, download_url, source)
        return {'kind': 'application', 'format': 'JAM/JAR/SP', 'payloads': payloads,
                'extensions': ('.jar', '.jam', '.sp'), 'stem': stem, 'method': method,
                'title': metadata.get('appname', source.stem), 'packageurl': metadata.get('packageurl', ''),
                'validation': 'JAR and nested-JAR CRCs verified; scratchpad length matches JAM SPsize'}
    tags = clear_header(raw, header_size)
    declared = tags.get(11, b'').rstrip(b'\0').decode('ascii', errors='replace').strip().upper()
    extension, validation = media_type(data, declared)
    title = tags.get(1, b'').rstrip(b'\0').decode('cp932', errors='replace').strip()
    usable_title = title and '\ufffd' not in title and not re.search(r'[\x00-\x1f\x7f]', title)
    stem = title if usable_title else source.stem
    method = 'SB1 header title' if usable_title else 'fallback: SB1 filename'
    for suffix in {extension, '.jpeg' if extension == '.jpg' else extension,
                   '.mhtml' if extension == '.mht' else extension}:
        if stem.lower().endswith(suffix) and len(stem) > len(suffix):
            stem = stem[:-len(suffix)]
            break
    return {'kind': 'media', 'format': extension[1:].upper(), 'payloads': (data,),
            'extensions': (extension,), 'stem': stem, 'method': method,
            'title': title or source.stem, 'declared_format': declared, 'validation': validation}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('input', type=Path, help='Decrypted SB1 file or directory containing SB1 files')
    parser.add_argument('output', type=Path, help='Separate export folder; unrelated existing files are rejected')
    parser.add_argument('--header-size', type=lambda x: int(x, 0), default=4096,
                        help='Clear bytes before decrypted payload (default: 4096; hexadecimal accepted)')
    parser.add_argument('--dry-run', action='store_true', help='Validate inputs and output conflicts without writing any files')
    args = parser.parse_args()
    source = args.input.resolve()
    output = args.output.resolve()
    require(args.header_size >= 0, 'Header size must be nonnegative')
    require(source.exists(), f'Input does not exist: {source}')
    require(output != source and output not in source.parents, 'Output must be separate from the source')
    root = source if source.is_dir() else source.parent
    sources = sorted((p for p in source.rglob('*') if p.is_file() and p.suffix.lower() == '.sb1'),
                     key=lambda p: p.relative_to(root).as_posix().casefold()) if source.is_dir() else [source]
    require(bool(sources), 'No SB1 files found')
    plans = []
    used_folders = set()
    expected = {'index.txt'}
    # Validate all inputs, package names, collisions and existing outputs first.
    for number, path in enumerate(sources, 1):
        raw = path.read_bytes()
        try:
            item = split_payload(raw, args.header_size, path)
        except (ValueError, zipfile.BadZipFile, RuntimeError) as error:
            raise ValueError(f'{path}: {error}') from error
        relative = path.relative_to(root)
        payloads = item.pop('payloads')
        original_stem = item['stem']
        stem = safe_component(original_stem)
        if item['kind'] == 'application':
            # Preserve the established P-06C application naming/layout exactly.
            group = next((p for p in path.parts if re.fullmatch(r'(?i)Card\d+', p)), '')
            folder = Path(group) / safe_component(f"{path.stem}_{item['title']}", 80)
            if folder.as_posix().casefold() in used_folders:
                folder = folder.with_name(folder.name + '_' + digest(relative.as_posix().encode())[:10])
            require(folder.as_posix().casefold() not in used_folders, 'Unresolved output folder collision')
            used_folders.add(folder.as_posix().casefold())
        else:
            folder = relative.parent
        targets = [folder / (stem + extension) for extension in item['extensions']]
        if any(t.as_posix().casefold() in expected for t in targets):
            stem += '_' + digest(relative.as_posix().encode())[:10]
            targets = [folder / (stem + extension) for extension in item['extensions']]
        require(all(t.as_posix().casefold() not in expected for t in targets), 'Unresolved output filename collision')
        for target, payload in zip(targets, payloads):
            dest = output / target
            require(dest.resolve().is_relative_to(output), 'Output path escapes export directory')
            for parent in dest.parents:
                if parent == output.parent:
                    break
                require(not parent.exists() or parent.is_dir(), f'Output directory conflicts with a file: {parent}')
            if dest.exists():
                require(dest.is_file() and dest.read_bytes() == payload, f'Conflicting existing output: {dest}')
            expected.add(target.as_posix().casefold())
        plans.append({**item, 'source': path, 'relative': relative.as_posix(), 'source_hash': digest(raw),
                      'targets': targets, 'renamed': stem != original_stem,
                      'hashes': [digest(p) for p in payloads], 'sizes': [len(p) for p in payloads]})
        if number % 100 == 0:
            print(f'Validated {number}/{len(sources)} SB1 files', flush=True)
    for plan in plans:
        for target in plan['targets']:
            require(all(parent == Path('.') or parent.as_posix().casefold() not in expected
                        for parent in target.parents), 'Planned output file conflicts with an output directory')
    if output.exists():
        require(output.is_dir(), 'Output must be a directory')
        require(not (output / 'index.txt').exists() or (output / 'index.txt').is_file(),
                'The master index path conflicts with a directory')
        extras = [p.relative_to(output).as_posix() for p in output.rglob('*')
                  if p.is_file() and p.relative_to(output).as_posix().casefold() not in expected]
        require(not extras, f'Output contains unrelated files; choose a fresh folder: {extras[:5]}')
    applications = sum(p['kind'] == 'application' for p in plans)
    media = len(plans) - applications
    count = sum(len(p['targets']) for p in plans)
    formats = Counter(p['format'] for p in plans)
    if args.dry_run:
        print(f'Validated {applications} applications and {media} media files; {count} payload files planned. No files written.')
        print('Formats: ' + ', '.join(f'{name}: {number}' for name, number in sorted(formats.items())))
        return
    lines = ['SB1 payload export', f'Applications: {applications}', f'Media files: {media}',
             f'Formats: {", ".join(f"{name}: {number}" for name, number in sorted(formats.items()))}',
             f'Exported payload files: {count}',
             'Master index: index.txt', f'Source root: {root}',
             f'Clear SB1 header bytes skipped: {args.header_size}',
             'Every exported payload is preserved byte-for-byte; no media conversion or URL fetching.', '']
    output.mkdir(parents=True, exist_ok=True)
    for number, plan in enumerate(plans, 1):
        raw = plan['source'].read_bytes()
        require(digest(raw) == plan['source_hash'], f"Input changed during export: {plan['source']}")
        payloads = split_payload(raw, args.header_size, plan['source'])['payloads']
        lines.extend([f"[{number:04d}] {one_line(plan['title'])}", f"Source SB1: {plan['relative']}",
                      f"Source SHA256: {plan['source_hash']}", f"Type: {plan['format']}",
                      f"Filename source: {plan['method']}", f"Validation: {plan['validation']}"])
        if plan['kind'] == 'application':
            lines.append(f"PackageURL: {one_line(plan['packageurl'])}")
        elif plan['declared_format']:
            lines.append(f"SB1 header format: {one_line(plan['declared_format'])}")
        if plan['renamed']:
            lines.append('Filename adjusted for Windows filesystem limits or to avoid a collision.')
        for target, payload, hash_value in zip(plan['targets'], payloads, plan['hashes']):
            dest = output / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.write_bytes(payload)
            require(digest(dest.read_bytes()) == hash_value, f'Export verification failed: {dest}')
            lines.append(f'File: {target.as_posix()} | {len(payload)} bytes | SHA256 {hash_value}')
        lines.append('')
    (output / 'index.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    fallback_count = sum(p['method'].startswith('fallback:') for p in plans)
    print(f'Exported {applications} applications and {media} media files / {count} payload files; {fallback_count} fallback names.')
    print(f'Master index: {output / "index.txt"}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
