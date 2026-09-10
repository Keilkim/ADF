"""Build the pinned OpenCV for macOS from its official source without video I/O.

The macOS opencv-python wheels link Homebrew FFmpeg and bundle its GPL codecs
(x264, x265 and others). ADF never reads or writes video: OCR uses the core,
imgproc, geometry and imgcodecs modules. features and calib are built too
because opencv-python's typing stub generator refines their functions. This
builds the same unmodified source archive that ADF-ThirdParty-Sources contains,
compiles image codecs from OpenCV's own source tree, and refuses configure-time
downloads and non-system libraries. The build record is added to the macOS
build manifest.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'.tools/opencv-macos'
RECORD = OUTPUT/'build.json'
PACKAGE = 'opencv-python'
DEPLOYMENT_TARGET = '14.0'
CMAKE_ARGS = [
    # BUILD_LIST also builds the modules these require.
    '-DBUILD_LIST=core,imgproc,geometry,imgcodecs,features,calib,python_bindings_generator,python3',
    '-DBUILD_opencv_videoio=OFF', '-DBUILD_opencv_video=OFF', '-DBUILD_opencv_highgui=OFF',
    '-DWITH_FFMPEG=OFF', '-DWITH_AVFOUNDATION=OFF', '-DWITH_GSTREAMER=OFF', '-DWITH_OBSENSOR=OFF',
    '-DBUILD_ZLIB=ON', '-DBUILD_JPEG=ON', '-DBUILD_PNG=ON', '-DBUILD_TIFF=ON', '-DBUILD_WEBP=ON',
    '-DBUILD_OPENJPEG=ON', '-DWITH_JASPER=OFF', '-DWITH_OPENEXR=OFF', '-DBUILD_OPENEXR=OFF',
    '-DWITH_AVIF=OFF', '-DWITH_JPEGXL=OFF',
    # These would otherwise be downloaded while configuring and are not in the source archive.
    '-DWITH_KLEIDICV=OFF', '-DWITH_UNIFONT=OFF', '-DWITH_IPP=OFF',
    '-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local',
    f'-DCMAKE_OSX_DEPLOYMENT_TARGET={DEPLOYMENT_TARGET}',
]
SYSTEM_PREFIXES = ('/usr/lib/', '/System/Library/')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pinned_version():
    for line in (ROOT/'requirements-ocr-lock.txt').read_text(encoding='utf-8').splitlines():
        if line.startswith(PACKAGE+'=='):
            return line.split('==', 1)[1].strip()
    raise RuntimeError(f'{PACKAGE} is not pinned in requirements-ocr-lock.txt')


def source_archive(version):
    with urllib.request.urlopen(f'https://pypi.org/pypi/{PACKAGE}/{version}/json', timeout=30) as response:
        entry = next(item for item in json.load(response)['urls'] if item['packagetype'] == 'sdist')
    # Shared with collect-licenses.py, which packages this archive as corresponding source.
    target = ROOT/'.tools/sources'/entry['filename']
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        print('Downloading '+target.name, flush=True)
        temporary = target.with_suffix(target.suffix+'.partial')
        with urllib.request.urlopen(entry['url'], timeout=120) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output, 1024*1024)
        temporary.replace(target)
    if digest(target) != entry['digests']['sha256']:
        raise RuntimeError('OpenCV source checksum mismatch: '+target.name)
    return target


def linked_libraries(binary):
    lines = subprocess.run(['otool', '-L', str(binary)], check=True, capture_output=True, text=True).stdout.splitlines()
    return sorted({line.split(' (', 1)[0].strip() for line in lines[1:] if line.strip()})


def check_wheel(wheel):
    with zipfile.ZipFile(wheel) as archive, tempfile.TemporaryDirectory() as directory:
        names = archive.namelist()
        if any('/.dylibs/' in name for name in names):
            raise RuntimeError('The OpenCV wheel bundles third-party libraries')
        binaries = [name for name in names if name.startswith('cv2/') and name.endswith('.so')]
        if not binaries:
            raise RuntimeError('The OpenCV wheel has no Python extension')
        libraries = []
        for name in binaries:
            libraries += linked_libraries(archive.extract(name, directory))
    foreign = sorted({library for library in libraries if not library.startswith(SYSTEM_PREFIXES)})
    if foreign:
        raise RuntimeError('OpenCV links non-system libraries: '+', '.join(foreign))
    return sorted(set(libraries))


def build(version, archive):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for old in OUTPUT.glob('*.whl'):
        old.unlink()
    with tempfile.TemporaryDirectory(prefix='adf-opencv-') as directory:
        downloads = Path(directory)/'downloads'
        downloads.mkdir()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('CMAKE_', 'PKG_CONFIG', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS'))}
        # Only Apple's compilers and system libraries are visible to the build.
        env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', MACOSX_DEPLOYMENT_TARGET=DEPLOYMENT_TARGET,
                   CMAKE_ARGS=' '.join(CMAKE_ARGS), CMAKE_BUILD_PARALLEL_LEVEL=str(os.cpu_count() or 4),
                   OPENCV_DOWNLOAD_PATH=str(downloads), OPENCV_PYTHON_SKIP_GIT_COMMANDS='1')
        print(f'Building {PACKAGE} {version} for macOS without video I/O', flush=True)
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-cache-dir',
                        '-w', str(OUTPUT), str(archive)], env=env, check=True)
        fetched = [path.name for path in downloads.rglob('*') if path.is_file() and path.name != '.gitignore']
        if fetched:
            raise RuntimeError('OpenCV downloaded components while configuring: '+', '.join(fetched))
    wheels = list(OUTPUT.glob('opencv_python-*.whl'))
    if len(wheels) != 1:
        raise RuntimeError('Expected one OpenCV wheel in '+str(OUTPUT))
    return wheels[0]


def main():
    if sys.platform != 'darwin':
        raise SystemExit('This OpenCV build is only used for the macOS application.')
    version = pinned_version()
    archive = source_archive(version)
    expected = dict(package=PACKAGE, version=version, source=archive.name, source_sha256=digest(archive),
                    cmake_args=CMAKE_ARGS, deployment_target=DEPLOYMENT_TARGET)
    record = json.loads(RECORD.read_text(encoding='utf-8')) if RECORD.is_file() else {}
    wheel = OUTPUT/record.get('wheel', '-')
    if {key: record.get(key) for key in expected} != expected or not wheel.is_file() or digest(wheel) != record.get('wheel_sha256'):
        wheel = build(version, archive)
    libraries = check_wheel(wheel)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--force-reinstall', str(wheel)], check=True)
    subprocess.run([sys.executable, '-c', 'import cv2, numpy; image = numpy.zeros((8, 8, 3), numpy.uint8); '
                    'assert cv2.imencode(".png", cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))[0]; '
                    'cv2.boxPoints(cv2.minAreaRect(numpy.array([[0, 0], [4, 0], [4, 2]], numpy.float32)))'], check=True)
    RECORD.write_text(json.dumps(dict(expected, wheel=wheel.name, wheel_sha256=digest(wheel), linked_libraries=libraries,
                                      source_modifications='None'), indent=2)+'\n', encoding='utf-8')
    print(f'{PACKAGE} {version} installed from {wheel.name} (no FFmpeg or bundled libraries).')


if __name__ == '__main__':
    main()
