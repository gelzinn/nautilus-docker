#!/usr/bin/env python3
"""Prepare and validate stable releases using only Python's standard library."""

import argparse
from datetime import date
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
VERSION_ASSIGNMENT = re.compile(r"^__version__\s*=\s*(['\"])([^'\"\n]+)\1\s*$", re.MULTILINE)


class ReleaseError(Exception):
    """A release cannot safely be prepared or published."""


def version_parts(version):
    match = re.fullmatch(VERSION_PATTERN, version)
    if not match:
        raise ReleaseError(f"invalid stable version: {version!r}")
    return tuple(map(int, match.groups()))


def git(root, *args):
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise ReleaseError(result.stderr.strip() or "git command failed")
    return result.stdout


def package_version(root):
    path = root / "src/nautilus_docker/__init__.py"
    contents = path.read_text(encoding="utf-8")
    matches = list(VERSION_ASSIGNMENT.finditer(contents))
    if len(matches) != 1:
        raise ReleaseError("package must contain exactly one __version__ assignment")
    version = matches[0].group(2)
    version_parts(version)
    return path, contents, matches[0], version


def detect(root):
    lines = git(root, "log", "-1", "--format=%B").strip().splitlines()
    subject = lines[0]
    if re.fullmatch(r"Merge pull request #[1-9][0-9]* from \S+", subject):
        subject = next((line for line in lines[1:] if line.strip()), "")
    match = re.fullmatch(r"chore: release v(" + VERSION_PATTERN + r")(?: \(#[0-9]+\))?", subject)
    return match.group(1) if match else None


def release_history(root):
    git(root, "rev-parse", "--git-dir")
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    if head.returncode:
        return None, []
    tags = git(root, "tag", "--merged", "HEAD", "--list", "v*").splitlines()
    stable_tags = [tag for tag in tags if re.fullmatch("v" + VERSION_PATTERN, tag)]
    latest = max(stable_tags, key=lambda tag: version_parts(tag[1:]), default=None)
    revision = f"{latest}..HEAD" if latest else "HEAD"
    records = git(root, "log", "--reverse", "--format=%s%x00%b%x00", revision).split("\0")
    commits = [(records[i].strip(), records[i + 1].strip())
               for i in range(0, len(records) - 1, 2)]
    return latest, commits


def automatic_bump(commits):
    for subject, body in commits:
        if (re.match(r"^[a-zA-Z][\w-]*(?:\([^\n)]+\))?!:", subject)
                or re.search(r"^BREAKING(?: CHANGE|-CHANGE):[ \t]+", body, re.MULTILINE)):
            return "major"
    if any(re.match(r"^feat(?:\([^\n)]+\))?:", subject, re.IGNORECASE) for subject, _ in commits):
        return "minor"
    return "patch"


def next_version(current, latest, bump, commits):
    current_parts = version_parts(current)
    if latest is None:
        return current
    latest_parts = version_parts(latest[1:])
    if current_parts < latest_parts:
        raise ReleaseError(f"package version {current} is behind reachable release {latest}")
    if current_parts > latest_parts:
        return current
    bump = automatic_bump(commits) if bump == "auto" else bump
    major, minor, patch = latest_parts
    versions = {"major": (major + 1, 0, 0), "minor": (major, minor + 1, 0),
                "patch": (major, minor, patch + 1)}
    return ".".join(map(str, versions[bump]))


def has_release(contents, version):
    return re.search(r"^## \[" + re.escape(version) + r"\](?:\s|$)", contents, re.MULTILINE)


def promote_changelog(contents, version, commits, first_release):
    if has_release(contents, version):
        raise ReleaseError(f"CHANGELOG.md already contains release {version}")
    headings = list(re.finditer(r"^## \[Unreleased\][^\n]*(?:\n|$)", contents, re.MULTILINE))
    if len(headings) != 1:
        raise ReleaseError("CHANGELOG.md must contain exactly one ## [Unreleased] heading")
    heading = headings[0]
    following = re.search(r"^## ", contents[heading.end():], re.MULTILINE)
    section_end = heading.end() + following.start() if following else len(contents)
    notes = contents[heading.end():section_end].strip()
    if not notes:
        notes = "\n".join(f"- {subject}" for subject, _ in commits)
        if not notes:
            notes = "- Initial release." if first_release else "- Maintenance release."
    section = (f"## [Unreleased]\n\n## [{version}] - {date.today().isoformat()}\n\n"
               f"{notes}\n\n")
    return contents[:heading.start()] + section + contents[section_end:].lstrip("\n")


def prepare(root, bump, output=None):
    path, contents, assignment, current = package_version(root)
    latest, commits = release_history(root)
    version = next_version(current, latest, bump, commits)
    tag = "v" + version
    if git(root, "tag", "--list", tag).strip():
        raise ReleaseError(f"target tag {tag} already exists")
    changelog = root / "CHANGELOG.md"
    promoted = promote_changelog(changelog.read_text(encoding="utf-8"), version, commits, latest is None)
    updated = contents[:assignment.start(2)] + version + contents[assignment.end(2):]
    if updated != contents:
        path.write_text(updated, encoding="utf-8")
    changelog.write_text(promoted, encoding="utf-8")
    if output is not None:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"version={version}\ntag={tag}\n")
    return version


def check(root, version):
    version_parts(version)
    _, _, _, current = package_version(root)
    if current != version:
        raise ReleaseError(f"package version {current} does not match release {version}")
    if not has_release((root / "CHANGELOG.md").read_text(encoding="utf-8"), version):
        raise ReleaseError(f"CHANGELOG.md is missing release {version}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare", help="update package version and changelog")
    prepare_parser.add_argument("--bump", choices=("auto", "patch", "minor", "major"), default="auto")
    prepare_parser.add_argument("--output", type=Path, help="append version and tag to GitHub output file")
    check_parser = commands.add_parser("check", help="validate a version before packaging")
    check_parser.add_argument("--version", required=True)
    commands.add_parser("detect", help="read the release version from a squash or merge commit")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(prepare(ROOT, args.bump, args.output))
        elif args.command == "check":
            check(ROOT, args.version)
        else:
            version = detect(ROOT)
            if version is not None:
                print(version)
    except (ReleaseError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
