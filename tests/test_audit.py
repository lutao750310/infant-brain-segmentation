from pathlib import Path
import json
import tempfile
import unittest

import nibabel as nib
import numpy as np

from neonatal_seg_audit.core import (agreement, label_mask, load_image, mask_stats,
                                     regional_volumes, require_same_grid, roi_definitions, sha256)
from neonatal_seg_audit.cli import compare, drawem_plan, new_output


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.affine = np.diag([1., 2., 3., 1.])
        self.data = np.zeros((12, 14, 16), np.uint16)
        self.data[2:5, 4:8, 6:10] = 1

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, name, data=None, affine=None, units="mm"):
        im = nib.Nifti1Image(self.data if data is None else data, self.affine if affine is None else affine)
        im.header.set_xyzt_units(units)
        p = self.root / (name + ".nii.gz")
        nib.save(im, p)
        return p

    def test_exact_agreement(self):
        m = self.data == 1
        result = agreement(m, m, self.affine)
        self.assertEqual(result["Dice_agreement_not_accuracy"], 1)
        self.assertEqual(result["Hausdorff_max_mm"], 0)

    def test_translation_physical(self):
        m = self.data == 1
        result = agreement(np.roll(m, 1, axis=1), m, self.affine)
        self.assertAlmostEqual(result["centroid_distance_mm_not_anatomical_error"], 2)
        self.assertEqual(result["candidate_minus_reference_centroid_RAS_mm"], [0, 2, 0])

    def test_empty_both_not_perfect(self):
        m = self.data == 99
        self.assertIsNone(agreement(m, m, self.affine)["Dice_agreement_not_accuracy"])

    def test_empty_one(self):
        result = agreement(self.data == 99, self.data == 1, self.affine)
        self.assertEqual(result["Dice_agreement_not_accuracy"], 0)
        self.assertIsNone(result["centroid_distance_mm_not_anatomical_error"])

    def test_components_not_cleaned(self):
        m = self.data == 1
        m[10, 12, 14] = True
        stats = mask_stats(m, self.affine)
        self.assertEqual(stats["components_6conn"]["count"], 2)
        self.assertEqual(stats["voxels"], 49)

    def test_oblique_centroid(self):
        affine = self.affine.copy()
        angle = .3
        rotation = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        affine[:3, :3] = rotation @ affine[:3, :3]
        m = self.data == 1
        result = agreement(np.roll(m, 1, axis=1), m, affine)
        self.assertAlmostEqual(result["centroid_distance_mm_not_anatomical_error"], 2)

    def test_fractional_labels_rejected(self):
        data = self.data.astype(np.float32)
        data[0, 0, 0] = .25
        with self.assertRaises(ValueError):
            load_image(self.save("fractional", data), labels=True)

    def test_nan_rejected(self):
        data = self.data.astype(np.float32)
        data[0, 0, 0] = np.nan
        with self.assertRaises(ValueError):
            load_image(self.save("nan", data))

    def test_unknown_units_explicit(self):
        p = self.save("units", units="unknown")
        with self.assertRaises(ValueError):
            load_image(p, labels=True)
        self.assertEqual(load_image(p, labels=True, assume_mm=True)[1].shape, self.data.shape)

    def test_qform_sform_conflict(self):
        im = nib.Nifti1Image(self.data, self.affine)
        im.header.set_xyzt_units("mm")
        im.set_qform(self.affine, code=1)
        other = self.affine.copy()
        other[0, 3] = 10
        im.set_sform(other, code=1)
        p = self.root / "conflict.nii.gz"
        nib.save(im, p)
        with self.assertRaises(ValueError):
            load_image(p, labels=True)

    def test_grid_mismatch(self):
        im, _ = load_image(self.save("a"))
        affine = self.affine.copy()
        affine[0, 3] = 1
        other, _ = load_image(self.save("b", affine=affine))
        with self.assertRaises(ValueError):
            require_same_grid(im, other)

    def test_26_plus_ventricles(self):
        self.assertEqual(len(roi_definitions()), 27)
        im, data = load_image(self.save("regions"), labels=True)
        result = regional_volumes(im, data)
        left = result["regions"][0]
        self.assertEqual(left["voxels"], 48)
        self.assertAlmostEqual(left["volume_mL_historical_pixdim"], .288)
        self.assertAlmostEqual(left["volume_mL_affine_determinant"], .288)

    def test_group_mappings(self):
        r = roi_definitions()
        self.assertEqual(r["Thalamus_L"], [43, 87])
        self.assertEqual(r["Basal_ganglia_R"], [40, 46])
        self.assertEqual(r["Ventricles_Bilateral"], [49, 50])

    def test_affine_vs_pixdim_both_reported(self):
        affine = self.affine.copy()
        affine[0, 1] = .4
        im, data = load_image(self.save("shear", affine=affine), labels=True)
        result = regional_volumes(im, data)
        row = result["regions"][0]
        self.assertNotAlmostEqual(row["volume_mL_historical_pixdim"], row["volume_mL_affine_determinant"], places=5)

    def test_unsupported_label_rejected(self):
        data = self.data.copy()
        data[0, 0, 0] = 90
        im, values = load_image(self.save("bad", data), labels=True)
        with self.assertRaises(ValueError):
            regional_volumes(im, values)

    def test_output_collision(self):
        with self.assertRaises(FileExistsError):
            new_output(self.root)

    def test_label_config_validated(self):
        for bad in ([], [0], [-1], [1.5], [True]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                label_mask(self.data, bad)

    def test_compare_preserves_source_and_unilateral(self):
        p = self.save("labels")
        before = sha256(p)
        config = {"reference": p.name, "regions": {"Left": [1], "Right": [2]},
                  "candidates": {"A": {"path": p.name}}}
        cfg = self.root / "config.json"
        cfg.write_text(json.dumps(config))
        result = compare(cfg, self.root / "out")
        self.assertEqual(before, sha256(p))
        self.assertEqual(result["comparisons"]["A"]["Left"]["Dice_agreement_not_accuracy"], 1)
        self.assertIsNone(result["comparisons"]["A"]["Right"]["Dice_agreement_not_accuracy"])

    def test_pma_bounds_never_clamped(self):
        for age in (27.9, 44.1, float("nan")):
            with self.subTest(age=age), self.assertRaises(ValueError):
                drawem_plan("unused", "unused", self.root / "work", age, 8, "sha256:" + "a"*64, self.root / "plan")

    def test_plan_only_no_workdir_or_docker_execution(self):
        image = self.save("image")
        mask = self.save("mask", (self.data > 0).astype(np.uint16))
        drawem_plan(image, mask, self.root / "work", 40.3, 8, "sha256:" + "a"*64, self.root / "plan")
        result = json.loads((self.root / "plan/drawem_plan.json").read_text())
        self.assertTrue(result["dry_run_only"])
        self.assertFalse(result["executed"])
        self.assertFalse((self.root / "work").exists())


if __name__ == "__main__":
    unittest.main()
