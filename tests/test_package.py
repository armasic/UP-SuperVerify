import importlib.util
from pathlib import Path
import tempfile
import unittest

CHECKER = Path(__file__).with_name('check_package.py')


class PackageTests(unittest.TestCase):
    def load_checker(self):
        self.assertTrue(CHECKER.is_file(), 'package checker is not implemented')
        spec = importlib.util.spec_from_file_location('package_checker', CHECKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.check

    def package(self, root):
        (root / 'agents').mkdir()
        (root / 'references').mkdir()
        (root / 'SKILL.md').write_text('---\nname: up-superverify\ndescription: Use when RTL projects need verification.\n---\n# Guide\n[Detail](references/detail.md)\n')
        (root / 'references/detail.md').write_text('# Detail\n')
        (root / 'agents/openai.yaml').write_text('interface:\n  display_name: "up-superverify"\n  short_description: "Functional verification and defect regression"\n  default_prompt: "Use $up-superverify for RTL verification."\n')

    def test_valid_package(self):
        check = self.load_checker()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.package(root)
            self.assertEqual(check(root), [])

    def test_broken_and_escaping_reference(self):
        check = self.load_checker()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.package(root)
            (root / 'references/detail.md').unlink()
            with (root / 'SKILL.md').open('a') as out:
                out.write('\n[Outside](../private.md)\n')
            errors = check(root)
            self.assertTrue(any('missing link' in e for e in errors), errors)
            self.assertTrue(any('escapes package' in e for e in errors), errors)

    def test_name_prompt_and_private_path(self):
        check = self.load_checker()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.package(root)
            path = root / 'SKILL.md'
            path.write_text(path.read_text().replace('name: up-superverify', 'name: wrong') + '\nTool lives at /home/example/private/bin.\n')
            (root / 'agents/openai.yaml').write_text('interface:\n  default_prompt: "Use another skill."\n')
            errors = check(root)
            self.assertTrue(any('name' in e for e in errors), errors)
            self.assertTrue(any('default_prompt' in e for e in errors), errors)
            self.assertTrue(any('machine path' in e for e in errors), errors)

    def test_local_process_files_are_not_scanned(self):
        check = self.load_checker()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.package(root)
            (root / 'docs').mkdir()
            (root / 'docs/private.md').write_text('/home/example/local/notes [bad](missing.md)')
            self.assertEqual(check(root), [])


if __name__ == '__main__':
    unittest.main()
