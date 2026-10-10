"""Parse Windows entry points without executing startup or account actions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows PowerShell parser')
class WindowsEntryScriptTests(unittest.TestCase):
    def test_control_switches_are_real_parameters(self):
        # A second UTF-8 BOM makes `param` a command, so -Status can otherwise
        # fall through into startup installation. Parse only; never run it.
        scripts = {
            'tools/install_startup.ps1': {'Disable', 'Status'},
            '.codebuddy/skills/fastnews-dashboard/scripts/dashboard.ps1': {'Action', 'ProjectPath'},
        }
        for name, expected in scripts.items():
            with self.subTest(script=name):
                path = str(ROOT / name).replace("'", "''")
                command = (
                    '$tokens=$null; $parseErrors=$null; '
                    "$ast=[System.Management.Automation.Language.Parser]::ParseFile('" + path + "',[ref]$tokens,[ref]$parseErrors); "
                    'if ($parseErrors.Count -or -not $ast.ParamBlock) { exit 2 }; '
                    '@($ast.ParamBlock.Parameters | ForEach-Object {$_.Name.VariablePath.UserPath}) | ConvertTo-Json -Compress'
                )
                result = subprocess.run(
                    ['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
                    capture_output=True, text=True, timeout=25, check=True,
                )
                self.assertEqual(set(json.loads(result.stdout)), expected)


if __name__ == '__main__':
    unittest.main()
