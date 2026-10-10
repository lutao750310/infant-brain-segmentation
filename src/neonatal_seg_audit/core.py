"""Read-only, native-grid measurements. Never resample or clean input labels."""
from pathlib import Path
import hashlib

import nibabel as nib
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_image(path, *, labels=False, assume_mm=False):
    image = nib.load(str(path))
    if len(image.shape) != 3:
        raise ValueError("Only 3D images are supported")
    if not np.isfinite(image.affine).all() or abs(np.linalg.det(image.affine[:3, :3])) < 1e-10:
        raise ValueError("Non-finite or singular affine")
    unit = image.header.get_xyzt_units()[0]
    if unit != "mm" and not (assume_mm and unit == "unknown"):
        raise ValueError("Spatial units must be mm; unknown units require explicit assume_mm")
    q, qc = image.get_qform(coded=True)
    s, sc = image.get_sform(coded=True)
    if qc and sc and not np.allclose(q, s, atol=1e-4, rtol=0):
        raise ValueError("Conflicting valid qform and sform: resolve geometry upstream")
    data = np.asarray(image.dataobj)
    if not np.isfinite(data).all():
        raise ValueError("Image contains non-finite values")
    if labels:
        if np.any(data < 0) or not np.allclose(data, np.rint(data), atol=1e-6, rtol=0):
            raise ValueError("Labels must be non-negative integers; no rounding is performed")
        if data.max(initial=0) > np.iinfo(np.int32).max:
            raise ValueError("Label exceeds supported integer range")
        data = np.rint(data).astype(np.int32)
    return image, data


def geometry(image):
    return {
        "shape": list(image.shape),
        "spacing_mm": list(map(float, image.header.get_zooms()[:3])),
        "affine_RAS_mm": image.affine.tolist(),
        "axis_codes": list(nib.aff2axcodes(image.affine)),
        "qform_code": int(image.header["qform_code"]),
        "sform_code": int(image.header["sform_code"]),
        "spatial_unit": image.header.get_xyzt_units()[0],
        "voxel_mm3_affine": float(abs(np.linalg.det(image.affine[:3, :3]))),
        "voxel_mm3_pixdim": float(np.prod(image.header.get_zooms()[:3])),
    }


def require_same_grid(reference, candidate):
    if reference.shape != candidate.shape or not np.allclose(
        reference.affine, candidate.affine, atol=1e-4, rtol=0
    ):
        raise ValueError("Native grids differ: no automatic alignment/resampling is allowed")


def label_mask(data, values):
    if not isinstance(values, list) or not values:
        raise ValueError("ROI labels must be a non-empty list")
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in values):
        raise ValueError("ROI label IDs must be positive integers")
    return np.isin(data, values)


def mask_stats(mask, affine):
    count = int(mask.sum())
    result = {"voxels": count, "volume_mL": count * abs(float(np.linalg.det(affine[:3, :3]))) / 1000,
              "centroid_RAS_mm": None, "bbox_IJK_inclusive": None}
    points = np.argwhere(mask)
    if count:
        result["centroid_RAS_mm"] = nib.affines.apply_affine(affine, points.mean(0)).tolist()
        result["bbox_IJK_inclusive"] = [points.min(0).tolist(), points.max(0).tolist()]
    for connectivity in (1, 3):
        components, n = ndimage.label(mask, ndimage.generate_binary_structure(3, connectivity))
        sizes = sorted(map(int, np.bincount(components.ravel())[1:]), reverse=True)
        result[f"components_{6 if connectivity == 1 else 26}conn"] = {
            "count": int(n), "sizes_voxels": sizes,
            "outside_largest_voxels": count - (sizes[0] if sizes else 0),
        }
    return result


def agreement(candidate, reference, affine):
    """Voxel-centre boundary distances in physical RAS, including oblique grids."""
    nc, nr = int(candidate.sum()), int(reference.sum())
    overlap = int((candidate & reference).sum())
    result = {
        "intersection_voxels": overlap, "candidate_only_voxels": nc - overlap,
        "reference_only_voxels": nr - overlap,
        "Dice_agreement_not_accuracy": 2 * overlap / (nc + nr) if nc + nr else None,
        "IoU_agreement_not_accuracy": overlap / (nc + nr - overlap) if nc + nr - overlap else None,
        "candidate_fraction_inside_reference": overlap / nc if nc else None,
        "reference_fraction_inside_candidate": overlap / nr if nr else None,
        "candidate_minus_reference_volume_percent": 100 * (nc / nr - 1) if nr else None,
        "candidate_minus_reference_centroid_RAS_mm": None,
        "centroid_distance_mm_not_anatomical_error": None,
        "symmetric_mean_boundary_distance_mm": None,
        "HD95_max_directed_percentiles_mm": None,
        "Hausdorff_max_mm": None,
        "warning": "empty mask; unavailable distances are null" if not nc or not nr else None,
    }
    if not nc or not nr:
        return result
    delta = (nib.affines.apply_affine(affine, np.argwhere(candidate).mean(0)) -
             nib.affines.apply_affine(affine, np.argwhere(reference).mean(0)))
    result["candidate_minus_reference_centroid_RAS_mm"] = delta.tolist()
    result["centroid_distance_mm_not_anatomical_error"] = float(np.linalg.norm(delta))
    structure = ndimage.generate_binary_structure(3, 1)
    surfaces = [m & ~ndimage.binary_erosion(m, structure=structure, border_value=0)
                for m in (candidate, reference)]
    c, r = [nib.affines.apply_affine(affine, np.argwhere(surface)) for surface in surfaces]
    dc = cKDTree(r).query(c)[0]
    dr = cKDTree(c).query(r)[0]
    result.update({
        "mean_candidate_surface_to_reference_mm": float(dc.mean()),
        "mean_reference_surface_to_candidate_mm": float(dr.mean()),
        "symmetric_mean_boundary_distance_mm": float((dc.mean() + dr.mean()) / 2),
        "HD95_max_directed_percentiles_mm": float(max(np.percentile(dc, 95), np.percentile(dr, 95))),
        "Hausdorff_max_mm": float(max(dc.max(), dr.max())),
    })
    return result


def roi_definitions():
    """Frozen historical DHF87 definitions; overlapping groups are intentional."""
    gm = list(range(5, 17)) + list(range(20, 40))
    deep = [40, 41, 42, 43, 44, 45, 46, 47, 86, 87]
    wm = list(range(51, 83))
    return {
        "Hippocampus_L": [1], "Hippocampus_R": [2], "Hippocampus_Bilateral": [1, 2],
        "Amygdala_L": [3], "Amygdala_R": [4], "Amygdala_Bilateral": [3, 4],
        "Corpus_callosum_Midline": [48], "Thalamus_L": [43, 87], "Thalamus_R": [42, 86],
        "Thalamus_Bilateral": [42, 43, 86, 87], "Basal_ganglia_L": [41, 47],
        "Basal_ganglia_R": [40, 46], "Basal_ganglia_Bilateral": [40, 41, 46, 47],
        "Parietal_lobe_GM_L": [39], "Parietal_lobe_GM_R": [38],
        "Parietal_lobe_WM_L": [82], "Parietal_lobe_WM_R": [81],
        "Cortical_gray_matter_Whole": gm, "Deep_gray_matter_Whole": deep,
        "Total_gray_matter_Whole": [1, 2, 3, 4] + gm + deep,
        "White_matter_Whole": wm, "Cerebellum_L": [17], "Cerebellum_R": [18],
        "Cerebellum_Bilateral": [17, 18], "Brainstem_Midline": [19],
        "Brain_parenchyma_Whole": list(range(1, 49)) + wm + [86, 87],
        "Ventricles_Bilateral": [49, 50],
    }


def regional_volumes(image, data):
    if data.max(initial=0) > 87:
        raise ValueError("Frozen DHF87 definitions require label IDs 0..87")
    geom = geometry(image)
    rows = []
    for name, ids in roi_definitions().items():
        n = int(np.isin(data, ids).sum())
        rows.append({"region": name, "label_ids": ids, "voxels": n,
                     "volume_mL_historical_pixdim": n * geom["voxel_mm3_pixdim"] / 1000,
                     "volume_mL_affine_determinant": n * geom["voxel_mm3_affine"] / 1000})
    return {"geometry": geom, "regions": rows,
            "brain_union": mask_stats(np.isin(data, roi_definitions()["Brain_parenchyma_Whole"]), image.affine),
            "clinical_acceptance": False,
            "note": "26 historical regions plus ventricles; overlapping groups must not be summed. "
                    "Volumes and component counts do not establish anatomical correctness."}
