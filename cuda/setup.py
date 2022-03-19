import os
from setuptools import setup
from torch.utils.cpp_extension import BuildExtension, CUDAExtension

include_dirs = [os.path.dirname(os.path.abspath(__file__)), "/usr/local/include/eigen3/"]
print(include_dirs)
setup(
    name='sampler',
    ext_modules=[
        CUDAExtension('sampler', [
                'src/nerf_extension.cu',
                'src/sampling.cu',
                'src/pe.cu'
            ],
            include_dirs=include_dirs,
            extra_compile_args={'cxx': ['-g',
            ],
                'nvcc': ['-O3', '-use_fast_math',
            ]},
        )
    ],
    cmdclass={
        'build_ext': BuildExtension
    })