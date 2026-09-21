"""CPU fallback for the ``torchmcubes`` extension (unbuildable here: no nvcc).

Same call signature ``marching_cubes(volume, isovalue) -> (verts, faces)``
as torch tensors, backed by scikit-image on CPU. Marching cubes is a tiny
fraction of TripoSR runtime; the neural inference still runs on CUDA.

Output order matches torchmcubes (x, y, z): skimage returns (z, y, x) for
a (D, H, W) grid, so the last axis is flipped.
"""
import numpy as np
import torch
from skimage.measure import marching_cubes as _sk_mc


def marching_cubes(volume, isovalue):
    if isinstance(volume, torch.Tensor):
        vol = volume.detach().cpu().to(torch.float32).numpy()
    else:
        vol = np.asarray(volume, dtype=np.float32)
    verts, faces, _, _ = _sk_mc(vol, float(isovalue))
    # Do NOT permute axes here: skimage emits (z, y, x) triples and TripoSR
    # applies its own [..., [2, 1, 0]] swap. Pre-flipping mirrors the mesh
    # and inverts winding (dark inside-out look in viewers).
    verts = np.ascontiguousarray(verts, dtype=np.float32)
    faces = np.ascontiguousarray(faces, dtype=np.int64)
    # Do NOT permute axes here: skimage emits (z, y, x) triples and TripoSR
    # applies its own [..., [2, 1, 0]] swap. Pre-flipping mirrors the mesh
    # and inverts winding (dark inside-out look in viewers).
    return torch.from_numpy(verts).float(), torch.from_numpy(faces).long()
