from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/release.py"


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.initialize(self.root)

    def initialize(self, root, version="0.1.0", notes=""):
        (root / "scripts").mkdir()
        shutil.copyfile(SCRIPT, root / "scripts/release.py")
        package = root / "src/nautilus_docker"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(f'__version__ = "{version}"\n')
        (root / "CHANGELOG.md").write_text(f"# Changelog\n\n## [Unreleased]\n\n{notes}")
        self.git(root, "init", "-b", "main")
        self.git(root, "config", "user.name", "Release Tests")
        self.git(root, "config", "user.email", "release-tests@example.invalid")

    def git(self, root, *args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, check=True).stdout

    def commit(self, root, message):
        self.git(root, "add", ".")
        self.git(root, "commit", "--allow-empty", "-m", message)

    def cli(self, *args, root=None, success=True):
        root = root or self.root
        result = subprocess.run([sys.executable, str(root / "scripts/release.py"), *args],
                                cwd=root, capture_output=True, text=True, check=False)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertTrue(result.stderr.startswith("error: "), result.stderr)
        return result

    def package_text(self, root=None):
        return ((root or self.root) / "src/nautilus_docker/__init__.py").read_text()

    def test_first_release_keeps_version_and_emits_outputs(self):
        subject = "initial commit $(touch SHOULD_NOT_EXIST) `touch ALSO_NOT_CREATED`"
        self.commit(self.root, subject)
        output = self.root / "github-output"
        output.write_text("existing=value\n")
        result = self.cli("prepare", "--bump", "major", "--output", str(output))
        self.assertEqual(result.stdout, "0.1.0\n")
        self.assertEqual(output.read_text(), "existing=value\nversion=0.1.0\ntag=v0.1.0\n")
        changelog = (self.root / "CHANGELOG.md").read_text()
        self.assertRegex(changelog, r"## \[Unreleased\]\n\n## \[0\.1\.0\] - \d{4}-\d{2}-\d{2}")
        self.assertIn("- " + subject, changelog)
        self.assertEqual(self.package_text(), '__version__ = "0.1.0"\n')
        self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())
        self.assertFalse((self.root / "ALSO_NOT_CREATED").exists())
        self.cli("check", "--version", "0.1.0")

    def test_unborn_repository_can_prepare_first_release(self):
        self.cli("prepare")
        self.assertIn("- Initial release.", (self.root / "CHANGELOG.md").read_text())

    def test_manual_notes_and_older_releases_are_preserved(self):
        notes = "### Added\n\n- Native Docker sidebar.\n\n## [0.0.1] - 2026-01-01\n\n- Earlier release.\n"
        (self.root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n" + notes)
        self.commit(self.root, "feat: ignored when manual notes exist")
        self.cli("prepare")
        changelog = (self.root / "CHANGELOG.md").read_text()
        self.assertIn(notes, changelog)
        self.assertNotIn("ignored when manual notes exist", changelog)

    def test_auto_bumps_from_commit_subjects_and_breaking_footers(self):
        cases = [("fix: spacing", "1.2.4"), ("feat: Docker hosts", "1.3.0"),
                 ("feat(sidebar): Docker hosts", "1.3.0"), ("Feat: add hosts", "1.3.0"),
                 ("FEAT(sidebar): add hosts", "1.3.0"), ("fix!: new access model", "2.0.0"),
                 ("refactor(access)!: new access model", "2.0.0"),
                 ("fix: access\n\nBREAKING CHANGE: old config is unsupported", "2.0.0"),
                 ("fix: access\n\nBREAKING-CHANGE: old config is unsupported", "2.0.0"),
                 ("docs: explain BREAKING CHANGE: footer syntax", "1.2.4"),
                 ("fix: access\n\nThis is not a BREAKING CHANGE: existing config still works.", "1.2.4"),
                 ("fix: access\n\nbreaking change: lower-case text is not a breaking footer", "1.2.4"),
                 ("feat: access\n\nDescribe the BREAKING-CHANGE: footer without using it.", "1.3.0")]
        for message, expected in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.initialize(root, "1.2.3")
                self.commit(root, "feat!: already released")
                self.git(root, "tag", "v1.2.3")
                self.commit(root, message)
                self.assertEqual(self.cli("prepare", "--bump", "auto", root=root).stdout, expected + "\n")
                self.assertIn(f'__version__ = "{expected}"', self.package_text(root))

    def test_explicit_bumps_override_commit_type(self):
        for bump, expected in [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0")]:
            with self.subTest(bump=bump), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.initialize(root, "1.2.3")
                self.commit(root, "initial commit")
                self.git(root, "tag", "v1.2.3")
                self.commit(root, "fix: spacing")
                self.assertEqual(self.cli("prepare", "--bump", bump, root=root).stdout, expected + "\n")

    def test_highest_reachable_stable_tag_ignores_other_branches_and_prereleases(self):
        (self.root / "src/nautilus_docker/__init__.py").write_text('__version__ = "1.3.0"\n')
        self.commit(self.root, "initial commit")
        for tag in ["v1.2.3", "v1.3.0", "v8.0.0-rc.1", "v01.0.0"]:
            self.git(self.root, "tag", tag)
        self.git(self.root, "checkout", "-b", "unrelated")
        self.commit(self.root, "feat!: unrelated feature")
        self.git(self.root, "tag", "v9.0.0")
        self.git(self.root, "checkout", "main")
        self.commit(self.root, "fix: main branch spacing")
        self.assertEqual(self.cli("prepare").stdout, "1.3.1\n")
        changelog = (self.root / "CHANGELOG.md").read_text()
        self.assertIn("- fix: main branch spacing", changelog)
        self.assertNotIn("initial commit", changelog)
        self.assertNotIn("unrelated feature", changelog)

    def test_package_version_ahead_of_latest_is_used(self):
        self.commit(self.root, "initial commit")
        self.git(self.root, "tag", "v0.0.1")
        self.commit(self.root, "feat!: ignored because version is already prepared")
        self.assertEqual(self.cli("prepare").stdout, "0.1.0\n")

    def test_package_version_behind_latest_is_rejected_without_mutations(self):
        self.commit(self.root, "initial commit")
        self.git(self.root, "tag", "v0.2.0")
        before = (self.root / "CHANGELOG.md").read_text()
        result = self.cli("prepare", success=False)
        self.assertIn("behind reachable release v0.2.0", result.stderr)
        self.assertEqual((self.root / "CHANGELOG.md").read_text(), before)
        self.assertEqual(self.package_text(), '__version__ = "0.1.0"\n')

    def test_existing_target_tag_on_other_branch_is_rejected(self):
        self.commit(self.root, "initial commit")
        self.git(self.root, "tag", "v0.1.0")
        self.git(self.root, "checkout", "-b", "unrelated")
        self.commit(self.root, "fix: unrelated change")
        self.git(self.root, "tag", "v0.1.1")
        self.git(self.root, "checkout", "main")
        result = self.cli("prepare", success=False)
        self.assertIn("target tag v0.1.1 already exists", result.stderr)

    def test_existing_changelog_release_is_rejected(self):
        with (self.root / "CHANGELOG.md").open("a") as handle:
            handle.write("## [0.1.0] - 2026-01-01\n\n- Existing release.\n")
        before = (self.root / "CHANGELOG.md").read_text()
        result = self.cli("prepare", success=False)
        self.assertIn("already contains release 0.1.0", result.stderr)
        self.assertEqual((self.root / "CHANGELOG.md").read_text(), before)

    def test_invalid_versions_and_missing_unreleased_heading_are_rejected(self):
        for version in ["01.2.3", "1.02.3", "1.2.03", "1.2", "v1.2.3", "1.2.3-rc.1"]:
            with self.subTest(version=version):
                (self.root / "src/nautilus_docker/__init__.py").write_text(f'__version__ = "{version}"\n')
                self.assertIn("invalid stable version", self.cli("prepare", success=False).stderr)
        (self.root / "src/nautilus_docker/__init__.py").write_text('__version__ = "0.1.0"\n')
        (self.root / "CHANGELOG.md").write_text("# Changelog\n")
        self.assertIn("Unreleased", self.cli("prepare", success=False).stderr)

    def test_check_rejects_package_mismatch_and_missing_release(self):
        self.assertIn("does not match", self.cli("check", "--version", "0.2.0", success=False).stderr)
        self.assertIn("missing release", self.cli("check", "--version", "0.1.0", success=False).stderr)
        self.assertIn("invalid stable version", self.cli("check", "--version", "00.1.0", success=False).stderr)


if __name__ == "__main__":
    unittest.main()
