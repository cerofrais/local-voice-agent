"""Make the pip-installed CUDA libraries visible to CTranslate2 and onnxruntime-gpu.

Import this module before faster_whisper or kokoro_onnx are used.
"""

import ctypes
import glob
import os
import site


def _preload_ctranslate2_libs() -> None:
    """CTranslate2 dlopen()s libcublas/libcudnn by name; load the pip-installed copies first."""
    for sp in site.getsitepackages():
        for pattern in (
            "nvidia/cublas/lib/libcublas*.so*",
            "nvidia/cuda_nvrtc/lib/*.so*",
            "nvidia/cudnn/lib/libcudnn*.so*",
        ):
            for lib in sorted(glob.glob(f"{sp}/{pattern}")):
                try:
                    ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def _use_gpu_onnx() -> None:
    """Point kokoro-onnx at the CUDA provider and load onnxruntime-gpu's CUDA libraries."""
    os.environ.setdefault("ONNX_PROVIDER", "CUDAExecutionProvider")
    try:
        import onnxruntime

        onnxruntime.preload_dlls()
    except Exception:
        pass


def setup() -> None:
    _preload_ctranslate2_libs()
    _use_gpu_onnx()


setup()
