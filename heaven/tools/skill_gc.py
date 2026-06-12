#!/usr/bin/env python3
"""Skill garbage-collection ANALYSIS (report-only by default).

Surfaces duplicated / template-bloat SKILL.md across projects/*/.claude/skills/.
IMPORTANT: skills are project-root-scoped — deleting a project's copy REMOVES
that capability from the project (there is no shared inheritance). So this is
NOT free dedup; --archive must be run per chosen project with eyes open.

Default = report. --archive <project> moves that project's exact-duplicate
skills to projects/<project>/.claude/skills/.archive/ (REVERSIBLE, no rm).

Usage:
    python3 tools/skill_gc.py                 # report
    python3 tools/skill_gc.py --archive <project> --dry-run
    python3 tools/skill_gc.py --archive <project>
"""
import argparse
import glob
import hashlib
import os
import shutil
from collections import defaultdict

HOME = os.path.expanduser("~")
ROOT = os.path.join(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()), "projects")


def skill_files():
    return glob.glob(os.path.join(ROOT, "*/.claude/skills/*/SKILL.md")) + glob.glob(
        os.path.join(ROOT, "*/*/.claude/skills/*/SKILL.md")
    )


def project_of(path):
    rel = os.path.relpath(path, ROOT)
    return rel.split(os.sep)[0]


def skill_name(path):
    return os.path.basename(os.path.dirname(path))


def md5(path):
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None


def report():
    files = skill_files()
    by_hash = defaultdict(list)
    by_project = defaultdict(list)
    by_name = defaultdict(set)
    for p in files:
        by_hash[md5(p)].append(p)
        by_project[project_of(p)].append(p)
        by_name[skill_name(p)].add(project_of(p))

    dup_files = sum(len(v) - 1 for v in by_hash.values() if len(v) > 1)
    print("=== Skill GC report ===")
    print(f"total SKILL.md:        {len(files)}")
    print(f"unique by content:     {len(by_hash)}")
    print(f"exact-duplicate files: {dup_files}\n")

    print("-- per-project skill count (bloat suspects on top):")
    for proj, ps in sorted(by_project.items(), key=lambda x: -len(x[1])):
        print(f"   {len(ps):4d}  {proj}")
    print()

    print("-- most-replicated skill names (copied across N projects):")
    for name, projs in sorted(by_name.items(), key=lambda x: -len(x[1]))[:15]:
        print(f"   x{len(projs):<2} {name}  ->  {', '.join(sorted(projs))}")
    print()
    print("Next: pick a bloated, recently-created project (e.g. a 1-2 session "
          "project with 100+ skills) and run --archive <project> --dry-run.")


def archive(project, dry_run):
    """Archive a project's skills that are exact duplicates of a copy living in
    ANOTHER project (i.e. template bloat, not project-unique)."""
    files = skill_files()
    # hash -> set of projects owning it
    hash_projects = defaultdict(set)
    for p in files:
        hash_projects[md5(p)].add(project_of(p))

    proj_dir = os.path.join(ROOT, project, ".claude/skills")
    archive_dir = os.path.join(proj_dir, ".archive")
    targets = []
    for p in glob.glob(os.path.join(proj_dir, "*/SKILL.md")):
        h = md5(p)
        # duplicate elsewhere => safe-ish to archive from THIS project
        if len(hash_projects[h] - {project}) >= 1:
            targets.append(p)

    print(f"=== archive '{project}' ({'DRY-RUN' if dry_run else 'EXECUTE'}) ===")
    print(f"skills that are exact dups of another project's copy: {len(targets)}")
    for p in targets:
        skill_dir = os.path.dirname(p)
        dest = os.path.join(archive_dir, os.path.basename(skill_dir))
        print(f"   {skill_name(p)}  ->  {os.path.relpath(dest, ROOT)}")
        if not dry_run:
            if os.path.exists(dest):
                print(f"     SKIP (dest exists): {os.path.relpath(dest, ROOT)}")
                continue
            os.makedirs(archive_dir, exist_ok=True)
            shutil.move(skill_dir, dest)
    if dry_run:
        print("\n(dry-run — nothing moved. drop --dry-run to execute; reversible "
              "via .archive/)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", metavar="PROJECT")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.archive:
        archive(args.archive, args.dry_run)
    else:
        report()
