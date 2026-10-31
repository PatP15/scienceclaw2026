"""Pure-numpy rasterisation helpers for closed triangle meshes (no display / GL needed).

* ``column_hits`` intersects a regular grid of vertical (+z) rays with every triangle.
* ``heightmap`` takes the topmost hit per column (used for renders and cut-face analysis).
* ``voxelize_parity`` fills a voxel grid by ray parity (even-odd rule) along each column.
  This is an independent re-voxelisation of the *mesh* used by validation (it does not reuse
  the generator's voxel field). Ray origins are offset by a tiny irrational jitter so rays do
  not pass exactly through shared edges/vertices.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh

_JITTER = (1e-3 * np.sqrt(2.0), 1e-3 * np.sqrt(3.0))


@dataclass
class Grid2D:
    origin: np.ndarray  # (2,) xy of the grid corner
    pitch: float
    nx: int
    ny: int

    def centers(self) -> tuple[np.ndarray, np.ndarray]:
        x = self.origin[0] + (np.arange(self.nx) + 0.5) * self.pitch + _JITTER[0] * self.pitch
        y = self.origin[1] + (np.arange(self.ny) + 0.5) * self.pitch + _JITTER[1] * self.pitch
        return x, y


def grid_for_mesh(mesh: trimesh.Trimesh, pitch: float, margin: float = 0.0) -> Grid2D:
    lo = mesh.bounds[0, :2] - margin
    hi = mesh.bounds[1, :2] + margin
    n = np.ceil((hi - lo) / pitch).astype(int)
    return Grid2D(lo.astype(float), float(pitch), int(n[0]), int(n[1]))


def column_hits(mesh: trimesh.Trimesh, grid: Grid2D) -> tuple[np.ndarray, np.ndarray]:
    """Return (flat_pixel_index, z) for every ray/triangle crossing."""
    tri = np.asarray(mesh.triangles, dtype=np.float64)
    p = grid.pitch
    ox = grid.origin[0] + (0.5 + _JITTER[0]) * p
    oy = grid.origin[1] + (0.5 + _JITTER[1]) * p
    ax, ay = tri[:, 0, 0], tri[:, 0, 1]
    bx, by = tri[:, 1, 0], tri[:, 1, 1]
    cx, cy = tri[:, 2, 0], tri[:, 2, 1]
    det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    keep = np.abs(det) > 1e-14  # vertical triangles never cross a vertical ray generically
    i0 = np.ceil((tri[:, :, 0].min(1) - ox) / p).astype(np.int64)
    i1 = np.floor((tri[:, :, 0].max(1) - ox) / p).astype(np.int64)
    j0 = np.ceil((tri[:, :, 1].min(1) - oy) / p).astype(np.int64)
    j1 = np.floor((tri[:, :, 1].max(1) - oy) / p).astype(np.int64)
    w = np.maximum(i1 - i0 + 1, j1 - j0 + 1)
    keep &= (i1 >= i0) & (j1 >= j0)
    out_pix, out_z = [], []
    if not keep.any():
        return np.zeros(0, np.int64), np.zeros(0)
    kbucket = np.ceil(np.log2(np.maximum(w, 1))).astype(int)
    for kb in np.unique(kbucket[keep]):
        k = 1 << int(kb)
        ids = np.nonzero(keep & (kbucket == kb))[0]
        chunk = max(1, 2_000_000 // (k * k))
        di, dj = np.meshgrid(np.arange(k), np.arange(k), indexing="ij")
        di, dj = di.ravel(), dj.ravel()
        for s in range(0, len(ids), chunk):
            t = ids[s : s + chunk]
            ii = i0[t, None] + di[None, :]
            jj = j0[t, None] + dj[None, :]
            valid = (ii <= i1[t, None]) & (jj <= j1[t, None])
            valid &= (ii >= 0) & (ii < grid.nx) & (jj >= 0) & (jj < grid.ny)
            px = ox + ii * p
            py = oy + jj * p
            d = det[t, None]
            l1 = ((by[t, None] - cy[t, None]) * (px - cx[t, None])
                  + (cx[t, None] - bx[t, None]) * (py - cy[t, None])) / d
            l2 = ((cy[t, None] - ay[t, None]) * (px - cx[t, None])
                  + (ax[t, None] - cx[t, None]) * (py - cy[t, None])) / d
            l3 = 1.0 - l1 - l2
            inside = valid & (l1 >= 0) & (l2 >= 0) & (l3 >= 0)
            z = l1 * tri[t, 0, 2][:, None] + l2 * tri[t, 1, 2][:, None] + l3 * tri[t, 2, 2][:, None]
            out_pix.append((ii * grid.ny + jj)[inside])
            out_z.append(z[inside])
    return np.concatenate(out_pix), np.concatenate(out_z)


def heightmap(mesh: trimesh.Trimesh, grid: Grid2D) -> np.ndarray:
    """Topmost surface z per column, NaN where the column misses the mesh. Shape (nx, ny)."""
    pix, z = column_hits(mesh, grid)
    hm = np.full(grid.nx * grid.ny, -np.inf)
    np.maximum.at(hm, pix, z)
    hm[~np.isfinite(hm)] = np.nan
    return hm.reshape(grid.nx, grid.ny)


def voxelize_parity(
    mesh: trimesh.Trimesh, pitch: float, margin_vox: int = 2
) -> tuple[np.ndarray, np.ndarray]:
    """Solid occupancy grid (nx, ny, nz) by even-odd ray parity; returns (occ, origin_xyz).

    Node (i, j, k) sits at origin + (i + 0.5 + jx, j + 0.5 + jy, k + 0.5) * pitch.
    A margin of empty voxels surrounds the mesh.
    """
    margin = margin_vox * pitch
    grid = grid_for_mesh(mesh, pitch, margin)
    z0 = mesh.bounds[0, 2] - margin
    nz = int(np.ceil((mesh.bounds[1, 2] + margin - z0) / pitch))
    zk = z0 + (np.arange(nz) + 0.5) * pitch
    pix, z = column_hits(mesh, grid)
    idx = np.searchsorted(zk, z, side="right")  # first node above the crossing
    counts = np.zeros((grid.nx * grid.ny, nz + 1), dtype=np.int16)
    np.add.at(counts, (pix, idx), 1)
    occ = (np.cumsum(counts, axis=1)[:, :nz] % 2).astype(bool)
    origin = np.array([grid.origin[0], grid.origin[1], z0])
    return occ.reshape(grid.nx, grid.ny, nz), origin
