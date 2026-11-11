#!/usr/bin/env python3
"""Check the distributable skill's entry metadata, links and portability."""
import argparse
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit


def check(root):
    root = Path(root).resolve()
    errors = []
    entry = root / 'SKILL.md'
    ui = root / 'agents/openai.yaml'
    for path in (entry, ui):
        if not path.is_file():
            errors.append('missing required file: ' + str(path.relative_to(root)))
    if errors:
        return errors
    content = entry.read_text(encoding='utf-8')
    front = re.match(r'\A---\n(.*?)\n---(?:\n|$)', content, re.S)
    if not front:
        errors.append('SKILL.md: missing YAML frontmatter')
    else:
        fields = {}
        for line in front.group(1).splitlines():
            if ':' in line and not line.startswith((' ', '\t')):
                key, value = line.split(':', 1)
                if key in fields:
                    errors.append('SKILL.md: duplicate metadata ' + key)
                fields[key] = value.strip().strip('"\'')
        if fields.get('name') != 'up-superverify':
            errors.append('SKILL.md: name must be up-superverify')
        if not fields.get('description', '').startswith('Use when '):
            errors.append('SKILL.md: description requires a precise Use when trigger')
        if len(fields.get('description', '')) > 1024:
            errors.append('SKILL.md: description is too long')
    metadata = ui.read_text(encoding='utf-8')
    for key in ('display_name', 'short_description', 'default_prompt'):
        match = re.search(r'^\s+' + key + r':\s+"([^"\n]+)"\s*$', metadata, re.M)
        if not match:
            errors.append('agents/openai.yaml: missing quoted ' + key)
        elif key == 'default_prompt' and '$up-superverify' not in match.group(1):
            errors.append('agents/openai.yaml: default_prompt must name $up-superverify')
        elif key == 'short_description' and not 25 <= len(match.group(1)) <= 64:
            errors.append('agents/openai.yaml: short_description must have 25–64 characters')
    docs = [entry]
    if (root / 'README.md').is_file():
        docs.append(root / 'README.md')
    docs.extend(sorted((root / 'references').rglob('*.md')))
    for path in docs:
        text = path.read_text(encoding='utf-8')
        label = str(path.relative_to(root))
        if re.search(r'/(?:Users|home)/[^/\s]+/', text):
            errors.append(label + ': contains private machine path')
        if re.search(r'\b(?:TODO|FIXME|PLACEHOLDER)\b', text):
            errors.append(label + ': contains unfinished scaffold')
        prose = re.sub(r'```.*?```', '', text, flags=re.S)
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', prose):
            target = target.strip().strip('<>')
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            candidate = (path.parent / unquote(parsed.path)).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                errors.append(label + ': link escapes package: ' + target)
                continue
            if not candidate.is_file():
                errors.append(label + ': missing link: ' + target)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = check(args.root)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print('Package checks passed (metadata, local links, portability).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
