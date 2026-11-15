"""Compile the Cython kernel using a configured Windows native toolchain."""
import os
from pathlib import Path
import sysconfig
from Cython.Build import cythonize
from setuptools import Extension, setup

project = Path(__file__).resolve().parent
toolchain = Path(os.environ['SPOOF_TOOLCHAIN']).resolve()
os.environ['PATH'] = str(toolchain/'w64devkit/bin') + os.pathsep + os.environ['PATH']
os.chdir(project)
python_include = Path(sysconfig.get_paths()['include']).resolve()
home = Path.home().resolve()
extra_compile_args = [
    '-O3',
    f'-ffile-prefix-map={project}=.',
    f'-ffile-prefix-map={toolchain}=.',
    f'-ffile-prefix-map={python_include}=.',
    f'-ffile-prefix-map={home}=.',
]
extension = Extension('descartes_search', ['descartes_search.py'], language='c++',
    include_dirs=[str(toolchain/'build/primesieve/source/include')],
    extra_objects=[str(toolchain/'build/primesieve/build/libprimesieve.a')],
    define_macros=[('MS_WIN64', None)], extra_compile_args=extra_compile_args,
    extra_link_args=['-static-libgcc', '-static-libstdc++'])
setup(name='spoof-search', packages=[], ext_modules=cythonize([extension], force=True,
    build_dir=str(project/'.runtime/generated'),
    compiler_directives=dict(language_level=3, infer_types=False, annotation_typing=True)),
    script_args=['build_ext','--compiler=mingw32',
                 '--build-lib',str(project/'.runtime/compiled'),
                 '--build-temp',str(project/'.runtime/build')])
