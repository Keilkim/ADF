"""Keep both installers and Windows binary resources on the shared app version."""
from pathlib import Path
import re

from adf import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_windows_package_versions_match_application():
    installer = (ROOT / 'installer/adf.iss').read_text(encoding='utf-8-sig')
    assert re.search(r'#define AppVersion "([^"]+)"', installer).group(1) == __version__
    resource = (ROOT / 'installer/version-info.txt').read_text(encoding='utf-8-sig')
    expected = tuple(map(int, __version__.split('.'))) + (0,)
    for field in ('filevers', 'prodvers'):
        actual = re.search(rf'{field}=\(([^)]+)\)', resource).group(1)
        assert tuple(map(int, actual.split(','))) == expected, field
    for field in ('FileVersion', 'ProductVersion'):
        assert re.search(rf"StringStruct\('{field}', '([^']+)'\)", resource).group(1) == __version__
    native = (ROOT / 'native-shell/adf_shell.rc').read_text(encoding='utf-8')
    for field in ('FILEVERSION', 'PRODUCTVERSION'):
        actual = re.search(rf'\b{field}\s+([\d,]+)', native).group(1)
        assert tuple(map(int, actual.split(','))) == expected, field
    for field in ('FileVersion', 'ProductVersion'):
        assert re.search(rf'VALUE "{field}", "([^"]+)"', native).group(1) == __version__ + '.0'
    assert f'"ADFShell-{__version__}.dll"' in native
    build = (ROOT / 'scripts/build-windows.ps1').read_text(encoding='utf-8-sig')
    assert re.search(r'ADFShell-([\d.]+)\.dll', build).group(1) == __version__
    smoke = (ROOT / 'scripts/test-installer.ps1').read_text(encoding='utf-8-sig')
    assert re.search(r"\$version = '([^']+)'", smoke).group(1) == __version__
