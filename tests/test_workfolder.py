"""workfolder.contained and replace_file: the one gate build and serve write through."""

import pathlib
import tempfile
import unittest

import helpers  # noqa: F401 — puts the skill folder on sys.path

import workfolder


class Contained(unittest.TestCase):
    def setUp(self):
        self.top = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.base = self.top / "data"
        self.base.mkdir()
        self.outside = self.top / "outside"
        self.outside.mkdir()

    def test_normal_file(self):
        (self.base / "sub").mkdir()
        (self.base / "sub" / "a.txt").write_text("x")
        self.assertEqual(workfolder.contained(self.base, "sub/a.txt"), self.base / "sub" / "a.txt")

    def test_missing_file_is_still_a_place_to_write(self):
        self.assertEqual(workfolder.contained(self.base, "new/b.txt"), self.base / "new" / "b.txt")

    def test_base_reached_through_a_linked_parent_is_fine(self):
        # Only the base itself must not be a link; /tmp on the Mac is one above it.
        alias = self.top / "alias"
        alias.symlink_to(self.top)
        self.assertEqual(workfolder.contained(alias / "data", "a.txt"), self.base / "a.txt")

    def test_symlinked_base(self):
        linked = self.top / "linked"
        linked.symlink_to(self.outside)
        self.assertIsNone(workfolder.contained(linked, "a.txt"))

    def test_symlinked_file(self):
        (self.outside / "victim").write_text("keep")
        (self.base / "a.txt").symlink_to(self.outside / "victim")
        self.assertIsNone(workfolder.contained(self.base, "a.txt"))

    def test_dangling_symlinked_file(self):
        (self.base / "a.txt").symlink_to(self.outside / "not-yet")
        self.assertIsNone(workfolder.contained(self.base, "a.txt"))

    def test_symlinked_intermediate_folder(self):
        (self.base / "sub").symlink_to(self.outside)
        self.assertIsNone(workfolder.contained(self.base, "sub/a.txt"))

    def test_no_dots_but_a_link_deeper_down_leads_out(self):
        (self.base / "deep").mkdir()
        (self.base / "deep" / "hop").symlink_to(self.outside)
        self.assertIsNone(workfolder.contained(self.base, "deep/hop/inner/a.txt"))

    def test_link_that_stays_inside_still_refused_as_a_file(self):
        (self.base / "real.txt").write_text("x")
        (self.base / "a.txt").symlink_to(self.base / "real.txt")
        self.assertIsNone(workfolder.contained(self.base, "a.txt"))

    def test_names_that_climb_or_hide(self):
        for name in ("../a.txt", "sub/../../a.txt", "/etc/hosts", ".hidden", "a//b", "", "a/"):
            with self.subTest(name=name):
                self.assertIsNone(workfolder.contained(self.base, name))


class ReplaceFile(unittest.TestCase):
    def setUp(self):
        self.top = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()

    def test_writes_a_new_file(self):
        target = self.top / "a.bin"
        workfolder.replace_file(target, b"\x00x")
        self.assertEqual(target.read_bytes(), b"\x00x")

    def test_a_hard_link_keeps_its_old_content(self):
        other = self.top / "other"
        other.write_bytes(b"keep")
        target = self.top / "a.bin"
        target.hardlink_to(other)
        workfolder.replace_file(target, b"new")
        self.assertEqual((target.read_bytes(), other.read_bytes()), (b"new", b"keep"))

    def test_a_symlink_is_replaced_not_followed(self):
        other = self.top / "other"
        other.write_bytes(b"keep")
        target = self.top / "a.bin"
        target.symlink_to(other)
        workfolder.replace_file(target, b"new")
        self.assertFalse(target.is_symlink())
        self.assertEqual(other.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
