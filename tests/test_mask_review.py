"""Protocol tests use synthetic fixtures; they do not evaluate an AI model."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

import nibabel as nib
import numpy as np

from neonatal_seg_audit.core import sha256
from neonatal_seg_audit.cli import drawem_plan
from neonatal_seg_audit.mask_review import (CHECKS, decide_review, load_packet, prepare_packet,
    read_json, record_review, resource, response_template, validate_response, verify_decision, write_json)


class MaskReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = np.indices((6, 7, 8)).sum(0).astype(np.float32)+1
        self.mask = np.zeros(self.data.shape, np.uint8)
        self.mask[2:4, 2:5, 2:6] = 1
        self.mask[0, 0, 0] = 1
        self.image = self.save("image", self.data)
        self.mask_path = self.save("mask", self.mask)
        # Fast hashes/coverage fixtures; rendering has a separate real-image test.
        with patch("neonatal_seg_audit.mask_review.render_pages", side_effect=self.fixture_pages):
            self.packet = prepare_packet(self.image, self.mask_path, self.root / "packet")
        self.packet_path = self.root / "packet/packet.json"

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, name, data, affine=None):
        image = nib.Nifti1Image(data, np.diag([1., 1.5, 2., 1.]) if affine is None else affine)
        image.header.set_xyzt_units("mm")
        image.set_qform(image.affine, code=1)
        image.set_sform(image.affine, code=1)
        path = self.root / f"{name}.nii.gz"
        nib.save(image, path)
        return path

    def fixture_pages(self, out, image, data, mask, per_page):
        evidence = []
        for axis in range(3):
            eid, relative = f"axis{axis}_page000", f"images/axis{axis}_page000.png"
            (out / relative).write_bytes(b"SYNTHETIC protocol fixture, not a diagnostic image")
            evidence.append({"id": eid, "path": relative, "sha256": sha256(out/relative),
                             "axis": axis, "indices": list(range(data.shape[axis]))})
        return evidence, {"vmin": 1, "vmax": 20}

    def clear_response(self):
        response = response_template(self.packet)
        response["reviewer"] = {"kind": "agent", "name": "SYNTHETIC_TEST_RUNNER", "model": "SIMULATED_NO_MODEL_CALL"}
        ids = [ev["id"] for ev in self.packet["evidence"]]
        response["reviewed_evidence_ids"] = ids
        for check in response["checks"]:
            check.update(status="no_visible_problem", evidence_ids=ids, explanation="Synthetic protocol assertion, not anatomical validation")
        response["recommendation"] = "suggest_pass"
        return response

    def review_record(self, response=None):
        path = self.root / "response.json"
        write_json(path, self.clear_response() if response is None else response)
        record_review(self.packet_path, path, self.root / "review")
        return self.root / "review/review.json"

    def human_decision(self, recommendation="approve_for_exploratory_drawem"):
        review = self.review_record()
        decision = read_json(self.root / "review/decision.template.json")
        decision.update(reviewer_name="SYNTHETIC_HUMAN_ATTESTATION", decision=recommendation,
                        reviewed_evidence_ids=[ev["id"] for ev in self.packet["evidence"]],
                        confirmed_checks=list(CHECKS), note="Synthetic software test only")
        return review, decision

    def test_schema_valid_and_template_not_accepted_as_completed(self):
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(json.loads(resource("mask_review_schema.json")))
        with self.assertRaises(ValueError):
            validate_response(self.packet, response_template(self.packet))

    def test_input_unchanged_and_island_not_deleted(self):
        self.assertEqual(sha256(self.mask_path), self.packet["sources"]["mask"]["sha256"])
        self.assertEqual(self.packet["mask_stats"]["components_26conn"]["count"], 2)
        self.assertFalse(self.packet["automation"]["model_called"])
        self.assertFalse(self.packet["clinical_acceptance"])

    def test_valid_clear_response_is_suggestion_only(self):
        coverage = validate_response(self.packet, self.clear_response())
        self.assertTrue(coverage["all_pages_declared_reviewed"])
        self.assertFalse(coverage["viewing_independently_verified"])

    def test_partial_review_cannot_suggest_pass(self):
        response = self.clear_response()
        response["reviewed_evidence_ids"].pop()
        for check in response["checks"]:
            check["evidence_ids"] = response["reviewed_evidence_ids"]
        with self.assertRaises(ValueError):
            validate_response(self.packet, response)
        response["recommendation"] = "needs_human_review"
        self.assertFalse(validate_response(self.packet, response)["all_pages_declared_reviewed"])

    def test_unknown_or_unreviewed_evidence_rejected(self):
        for mode in ("unknown", "unreviewed"):
            response = self.clear_response()
            if mode == "unknown":
                response["reviewed_evidence_ids"].append("invented")
            else:
                response["reviewed_evidence_ids"].pop()
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_response(self.packet, response)

    def test_wrong_packet_prompt_or_mode_rejected(self):
        for key, value in (("packet_id", "other"), ("prompt_sha256", "a"*64), ("prompt_version", "other")):
            response = self.clear_response()
            response[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_response(self.packet, response)
        response = self.clear_response()
        response["reviewer"]["kind"] = "human"
        with self.assertRaises(ValueError):
            validate_response(self.packet, response)

    def test_clinical_acceptance_and_extra_fields_rejected(self):
        for key, value in (("clinical_acceptance", True), ("automatic_mask_edit", True)):
            response = self.clear_response()
            response[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_response(self.packet, response)

    def test_duplicate_or_missing_check_rejected(self):
        response = self.clear_response()
        response["checks"][-1] = deepcopy(response["checks"][0])
        with self.assertRaises(ValueError):
            validate_response(self.packet, response)

    def test_uncertainty_or_finding_blocks_pass(self):
        response = self.clear_response()
        response["checks"][0]["status"] = "not_assessable"
        with self.assertRaises(ValueError):
            validate_response(self.packet, response)
        response = self.clear_response()
        response["findings"] = [{"category": "isolated_components", "description": "Synthetic island needs review",
                                 "evidence_ids": ["axis0_page000"], "coordinate_IJK": [0, 0, 0]}]
        with self.assertRaises(ValueError):
            validate_response(self.packet, response)
        response["recommendation"] = "needs_human_review"
        self.assertTrue(validate_response(self.packet, response)["all_pages_declared_reviewed"])

    def test_coordinate_bounds_rejected(self):
        response = self.clear_response()
        response["recommendation"] = "needs_human_review"
        response["findings"] = [{"category": "brain_coverage", "description": "Synthetic location",
                                 "evidence_ids": ["axis0_page000"], "coordinate_IJK": [99, 0, 0]}]
        with self.assertRaises(ValueError):
            validate_response(self.packet, response)

    def test_tampered_input_image_prompt_and_evidence_rejected(self):
        for path in (self.image, self.mask_path, self.root/"packet/prompt.md", self.root/"packet/images/axis0_page000.png"):
            before = path.read_bytes()
            path.write_bytes(before+b"synthetic change")
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                load_packet(self.packet_path)
            path.write_bytes(before)

    def test_missing_native_plane_rejected(self):
        packet = deepcopy(self.packet)
        packet["evidence"][0]["indices"].pop()
        changed = self.root / "packet/changed.json"
        write_json(changed, packet)
        with self.assertRaises(ValueError):
            load_packet(changed)

    def test_output_collision_and_bad_mask_rejected(self):
        with self.assertRaises(FileExistsError):
            prepare_packet(self.image, self.mask_path, self.root / "packet")
        bad = self.save("bad", self.mask*2)
        with self.assertRaises(ValueError):
            prepare_packet(self.image, bad, self.root/"bad-output")
        self.assertFalse((self.root/"bad-output").exists())

    def test_grid_shift_rejected_before_output(self):
        affine = np.diag([1., 1.5, 2., 1.])
        affine[0, 3] = 1
        shifted = self.save("shift", self.mask, affine)
        with self.assertRaises(ValueError):
            prepare_packet(self.image, shifted, self.root/"shift-output")

    def test_human_mode_supported_and_model_must_be_null(self):
        packet = deepcopy(self.packet)
        packet["mode"] = "human"
        response = self.clear_response()
        response["reviewer"] = {"kind": "human", "name": "SYNTHETIC_HUMAN", "model": None}
        validate_response(packet, response)
        response["reviewer"]["model"] = "invented"
        with self.assertRaises(ValueError):
            validate_response(packet, response)

    def test_agent_cannot_approve_without_named_human(self):
        review, decision = self.human_decision()
        decision["reviewer_name"] = "UNSET"
        path = self.root/"human.json"
        write_json(path, decision)
        with self.assertRaises(ValueError):
            decide_review(self.packet_path, review, path, self.root/"approval")

    def test_partial_human_attestation_blocks_approval(self):
        review, decision = self.human_decision()
        decision["reviewed_evidence_ids"].pop()
        path = self.root/"human.json"
        write_json(path, decision)
        with self.assertRaises(ValueError):
            decide_review(self.packet_path, review, path, self.root/"approval")

    def test_approval_binds_hashes_and_still_not_clinical(self):
        review, decision = self.human_decision()
        path = self.root/"human.json"
        write_json(path, decision)
        result = decide_review(self.packet_path, review, path, self.root/"approval")
        self.assertTrue(result["ready_for_exploratory_drawem"])
        self.assertFalse(result["clinical_acceptance"])
        self.assertFalse(result["identity_authenticated"])
        certificate = self.root/"approval/decision.json"
        verify_decision(certificate, self.image, self.mask_path)
        different = self.save("different", np.roll(self.mask, 1, axis=0))
        with self.assertRaises(ValueError):
            verify_decision(certificate, self.image, different)
        drawem_plan(self.image, self.mask_path, self.root/"work", 40, 2, "sha256:"+"a"*64,
                    self.root/"plan", certificate)
        plan = read_json(self.root/"plan/drawem_plan.json")
        self.assertTrue(plan["ready_for_exploratory_drawem"])
        self.assertFalse(plan["executed"])

    def test_rejection_cannot_produce_approved_plan(self):
        review, decision = self.human_decision("reject")
        path = self.root/"human.json"
        write_json(path, decision)
        decide_review(self.packet_path, review, path, self.root/"approval")
        with self.assertRaises(ValueError):
            verify_decision(self.root/"approval/decision.json", self.image, self.mask_path)

    def test_plan_without_decision_explicitly_unapproved(self):
        drawem_plan(self.image, self.mask_path, self.root/"work", 40, 2, "sha256:"+"a"*64, self.root/"plan")
        self.assertFalse(read_json(self.root/"plan/drawem_plan.json")["ready_for_exploratory_drawem"])

    def test_non_json_nan_rejected(self):
        path = self.root/"nan.json"
        path.write_text('{"value":NaN}')
        with self.assertRaises(ValueError):
            read_json(path)

    def test_real_render_all_planes_and_no_input_changes(self):
        before = sha256(self.image), sha256(self.mask_path)
        packet = prepare_packet(self.image, self.mask_path, self.root/"real-images", "human", 6)
        self.assertEqual(sum(len(ev["indices"]) for ev in packet["evidence"]), sum(self.data.shape))
        self.assertEqual((sha256(self.image), sha256(self.mask_path)), before)
        self.assertTrue(all((self.root/"real-images"/ev["path"]).read_bytes().startswith(b"\x89PNG") for ev in packet["evidence"]))
        self.assertFalse(packet["coverage"]["whole_volume_review_claimed"])

    def test_empty_mask_or_constant_image_rejected(self):
        empty = self.save("empty", self.mask*0)
        constant = self.save("constant", np.ones_like(self.data))
        for image, mask in ((self.image, empty), (constant, self.mask_path)):
            with self.subTest(image=image.name), self.assertRaises(ValueError):
                prepare_packet(image, mask, self.root/"invalid")
        self.assertFalse((self.root/"invalid").exists())

    def test_packet_path_escape_rejected(self):
        packet = deepcopy(self.packet)
        packet["evidence"][0]["path"] = "../mask.nii.gz"
        path = self.root/"packet/escape.json"
        write_json(path, packet)
        with self.assertRaises(ValueError):
            load_packet(path)

    def test_packet_geometry_change_rejected(self):
        packet = deepcopy(self.packet)
        packet["geometry"]["affine_RAS_mm"][0][3] += 1
        path = self.root/"packet/geometry.json"
        write_json(path, packet)
        with self.assertRaises(ValueError):
            load_packet(path)

    def test_agent_requires_actual_model_identifier(self):
        response = self.clear_response()
        response["reviewer"]["model"] = None
        with self.assertRaises(ValueError):
            validate_response(self.packet, response)

    def test_human_decision_change_invalidates_approval(self):
        review, decision = self.human_decision()
        path = self.root/"human.json"
        write_json(path, decision)
        decide_review(self.packet_path, review, path, self.root/"approval")
        decision["note"] = "Changed after approval"
        path.write_text(json.dumps(decision))
        with self.assertRaises(ValueError):
            verify_decision(self.root/"approval/decision.json", self.image, self.mask_path)

    def test_coordinate_must_intersect_cited_page(self):
        packet = deepcopy(self.packet)
        packet["evidence"][0]["indices"] = [0]
        response = self.clear_response()
        response["recommendation"] = "needs_human_review"
        response["findings"] = [{"category": "brain_coverage", "description": "Synthetic mismatch",
                                 "evidence_ids": ["axis0_page000"], "coordinate_IJK": [3, 3, 3]}]
        with self.assertRaises(ValueError):
            validate_response(packet, response)


if __name__ == "__main__":
    unittest.main()
