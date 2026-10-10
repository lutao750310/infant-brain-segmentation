"""Offline evidence workflow. No model calls, mask edits, or clinical acceptance."""
from datetime import datetime, timezone
from importlib.resources import files
from importlib.metadata import version
from pathlib import Path
import hashlib
import html
import json
import platform
import uuid

import nibabel as nib
import numpy as np
from jsonschema import Draft202012Validator

from . import __version__
from .core import geometry, load_image, mask_stats, require_same_grid, sha256

PROMPT_VERSION = "mask-review-1.0"
CHECKS = ("brain_coverage", "eyes_scalp", "face_neck", "posterior_fossa", "csf_support", "isolated_components")


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def read_json(path):
    def invalid(value):
        raise ValueError(f"Non-JSON numeric constant: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid)


def fresh_output(path):
    out = Path(path).resolve()
    out.mkdir(parents=True, exist_ok=False)
    return out


def resource(name):
    return files("neonatal_seg_audit").joinpath(name).read_text(encoding="utf-8")


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def render_pages(out, image, data, mask, per_page=5):
    """Every native plane, including planes with no mask. Never resample input."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.patches import Rectangle
    positive = data[data > 0]
    values = positive if positive.size else data.ravel()
    vmin, vmax = map(float, np.percentile(values, [1, 99]))
    if vmax <= vmin:
        vmin, vmax = float(data.min()), float(data.max())
    if vmax <= vmin:
        raise ValueError("T2 has no displayable intensity variation")
    points = np.argwhere(mask)
    lo, hi = np.maximum(points.min(0)-4, 0), np.minimum(points.max(0)+5, data.shape)
    spacing = np.linalg.norm(image.affine[:3, :3], axis=0)
    evidence = []
    for axis in range(3):
        other = [i for i in range(3) if i != axis]
        for start in range(0, data.shape[axis], per_page):
            indices = list(range(start, min(start+per_page, data.shape[axis])))
            eid = f"axis{axis}_page{start//per_page:03d}"
            fig, axes = plt.subplots(len(indices), 4, figsize=(14, 2.6*len(indices)), squeeze=False)
            centers = []
            for row, index in enumerate(indices):
                ijk = (np.array(data.shape)-1)/2
                ijk[axis] = index
                ras = nib.affines.apply_affine(image.affine, ijk)
                centers.append(ras.tolist())
                plane, overlay = np.take(data, index, axis=axis).T, np.take(mask, index, axis=axis).T
                for col in range(4):
                    ax = axes[row, col]
                    ax.imshow(plane, cmap="gray", origin="lower", vmin=vmin, vmax=vmax,
                              aspect=float(spacing[other[1]]/spacing[other[0]]))
                    if col % 2 and overlay.any():
                        # Pad to show boundaries even when foreground reaches the image edge.
                        yy = np.arange(-1, overlay.shape[0]+1)
                        xx = np.arange(-1, overlay.shape[1]+1)
                        ax.contour(xx, yy, np.pad(overlay.astype(float), 1), levels=[.5],
                                   colors=["#00bfe8"], linewidths=.8)
                    x0, y0 = lo[other[0]]-.5, lo[other[1]]-.5
                    x1, y1 = hi[other[0]]-.5, hi[other[1]]-.5
                    if col < 2:
                        ax.set_xlim(-.5, plane.shape[1]-.5)
                        ax.set_ylim(-.5, plane.shape[0]-.5)
                        ax.add_patch(Rectangle((x0, y0), x1-x0, y1-y0, fill=False, edgecolor="#e6b300"))
                    else:
                        ax.set_xlim(x0, x1)
                        ax.set_ylim(y0, y1)
                    ax.set_title(f'{"Full" if col < 2 else "Detail"}: {"T2 + boundary" if col % 2 else "T2 only"}\n'
                                 f"axis {axis}, index {index}; center RAS {np.round(ras, 1)}", fontsize=9)
                    ax.set_xlabel(f"native axis {other[0]} index", fontsize=8)
                    ax.set_ylabel(f"native axis {other[1]} index", fontsize=8)
            fig.suptitle(f"{eid} | same window {vmin:.2f}..{vmax:.2f} | native index planes may be oblique\n"
                         "All planes generated, not proof all were reviewed. Detail box follows mask bounds; inspect full views for omissions.", fontsize=10)
            fig.tight_layout(rect=(0, 0, 1, .95))
            relative = f"images/{eid}.png"
            fig.savefig(out / relative, dpi=110)
            plt.close(fig)
            evidence.append({"id": eid, "path": relative, "sha256": sha256(out / relative),
                             "axis": axis, "indices": indices, "plane_center_RAS_mm": centers})
    return evidence, {"vmin": vmin, "vmax": vmax, "same_for_all_views": True,
                      "detail_bbox_IJK_exclusive": [lo.tolist(), hi.tolist()]}


def response_template(packet):
    return {"schema_version": "1.0", "packet_id": packet["packet_id"],
            "reviewer": {"kind": packet["mode"], "name": "UNSET", "model": "UNSET" if packet["mode"] == "agent" else None},
            "prompt_version": PROMPT_VERSION, "prompt_sha256": packet["resources"]["prompt"]["sha256"],
            "reviewed_evidence_ids": [],
            "checks": [{"id": key, "status": "not_assessable", "evidence_ids": [],
                        "explanation": "尚未查看，不能判断"} for key in CHECKS],
            "findings": [], "recommendation": "needs_human_review", "clinical_acceptance": False}


def prepare_packet(image_path, mask_path, output, mode="agent", per_page=5):
    if mode not in ("agent", "human") or not isinstance(per_page, int) or not 1 <= per_page <= 6:
        raise ValueError("Mode must be human/agent; planes per page must be 1..6")
    paths = {"image": Path(image_path).resolve(), "mask": Path(mask_path).resolve()}
    before = {key: sha256(path) for key, path in paths.items()}
    image, data = load_image(paths["image"])
    mask_image, labels = load_image(paths["mask"], labels=True)
    require_same_grid(image, mask_image)
    if not np.isin(labels, [0, 1]).all() or not labels.any():
        raise ValueError("Mask must be nonempty binary 0/1; never clean or threshold automatically")
    if float(data.max()) <= float(data.min()):
        raise ValueError("T2 has no displayable intensity variation")
    out = fresh_output(output)
    (out / "images").mkdir()
    prompt, schema = resource("mask_review_prompt.md"), resource("mask_review_schema.json")
    (out / "prompt.md").write_text(prompt, encoding="utf-8")
    (out / "response.schema.json").write_text(schema, encoding="utf-8")
    evidence, window = render_pages(out, image, data, labels.astype(bool), per_page)
    warnings = []
    stats = mask_stats(labels.astype(bool), image.affine)
    if stats["components_26conn"]["count"] > 1:
        warnings.append("Multiple components: inspect their anatomy; do not keep only the largest automatically")
    if any(np.take(labels, index, axis=axis).any() for axis in range(3) for index in (0, labels.shape[axis]-1)):
        warnings.append("Mask reaches a field-of-view edge: possible coverage issue; not an anatomical verdict")
    if not int(image.header["qform_code"]) and not int(image.header["sform_code"]):
        warnings.append("Both transform codes are unset; verify provenance of fallback affine")
    packet = {"schema_version": "1.0", "packet_id": str(uuid.uuid4()), "package_version": __version__,
              "created_utc": datetime.now(timezone.utc).isoformat(), "mode": mode,
              "runtime": {"python": platform.python_version(), "numpy": np.__version__, "nibabel": nib.__version__,
                          "matplotlib": version("matplotlib"), "jsonschema": version("jsonschema")},
              "state": "technical_checks_complete_review_pending", "clinical_acceptance": False,
              "sources": {key: {"path": str(path), "sha256": before[key]} for key, path in paths.items()},
              "geometry": geometry(image), "mask_stats": stats, "warnings_not_verdicts": warnings,
              "coverage": {"all_native_planes_generated": True, "total_native_planes": sum(data.shape),
                           "whole_volume_review_claimed": False, "anatomical_resampling_performed": False},
              "display": window, "evidence": evidence,
              "resources": {"prompt": {"path": "prompt.md", "version": PROMPT_VERSION, "sha256": text_hash(prompt)},
                            "schema": {"path": "response.schema.json", "sha256": text_hash(schema)}},
              "automation": {"model_called": False, "network_used": False, "inputs_modified": False}}
    content = '<!doctype html><html lang="zh"><meta charset="utf-8"><title>脑掩膜质量复核</title><style>body{font:16px sans-serif;max-width:1450px;margin:24px auto;padding:0 16px}img{max-width:100%;height:auto}summary{cursor:pointer;padding:10px}code{overflow-wrap:anywhere}</style><h1>脑掩膜质量复核</h1><p>研究技术复核，不是医学验收。全部原生切片已生成，不代表全部已查看。原生索引平面可能倾斜，不能自动叫作解剖轴位、冠状或矢状。</p>'
    content += '<p>查看全图排除漏分；局部框跟随掩膜，不是独立的真实脑范围。固定窗宽窗位，原图与轮廓图并列。<a href="prompt.md">复核提示词</a> · <a href="response.template.json">回填模板</a></p>'
    for ev in evidence:
        content += f'<details><summary>{html.escape(ev["id"])} · axis {ev["axis"]} · indices {ev["indices"]}</summary><img loading="lazy" src="{ev["path"]}" alt="原图、掩膜边界、局部与连续邻层"></details>'
    (out / "report.html").write_text(content + '</html>\n', encoding="utf-8")
    write_json(out / "response.template.json", response_template(packet))
    if any(sha256(path) != before[key] for key, path in paths.items()):
        raise RuntimeError("Input changed during rendering; discard packet")
    write_json(out / "packet.json", packet)
    return packet


def safe_packet_file(root, relative):
    p = (root / relative).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        raise ValueError("Evidence/resource path escapes packet or is missing")
    return p


def load_packet(packet_path):
    path = Path(packet_path).resolve()
    packet, root = read_json(path), path.parent
    if packet.get("schema_version") != "1.0" or packet.get("mode") not in ("human", "agent"):
        raise ValueError("Unsupported packet")
    for source in packet["sources"].values():
        if sha256(source["path"]) != source["sha256"]:
            raise ValueError("Source changed after packet creation")
    current_image, _ = load_image(packet["sources"]["image"]["path"])
    current_mask, values = load_image(packet["sources"]["mask"]["path"], labels=True)
    require_same_grid(current_image, current_mask)
    if geometry(current_image) != packet["geometry"] or not np.isin(values, [0, 1]).all() or not values.any():
        raise ValueError("Packet geometry/binary mask inconsistent with sources")
    for name, filename in (("prompt", "mask_review_prompt.md"), ("schema", "mask_review_schema.json")):
        item = packet["resources"][name]
        if sha256(safe_packet_file(root, item["path"])) != item["sha256"] or item["sha256"] != text_hash(resource(filename)):
            raise ValueError("Prompt/schema changed or belongs to another package version; regenerate packet")
    seen, planes = set(), {axis: [] for axis in range(3)}
    for ev in packet["evidence"]:
        if ev["id"] in seen or ev["axis"] not in planes:
            raise ValueError("Duplicate evidence ID or invalid axis")
        if not ev["indices"] or any(type(index) is not int for index in ev["indices"]):
            raise ValueError("Invalid evidence indices")
        seen.add(ev["id"])
        planes[ev["axis"]].extend(ev["indices"])
        if sha256(safe_packet_file(root, ev["path"])) != ev["sha256"]:
            raise ValueError("Evidence image changed after packet creation")
    if any(sorted(planes[axis]) != list(range(packet["geometry"]["shape"][axis])) for axis in range(3)):
        raise ValueError("Packet must contain each native plane exactly once per axis")
    return packet


def validate_response(packet, response):
    schema = json.loads(resource("mask_review_schema.json"))
    Draft202012Validator.check_schema(schema)
    error = next(Draft202012Validator(schema).iter_errors(response), None)
    if error:
        raise ValueError(f"Review schema error at {list(error.path)}: {error.message}")
    if response["packet_id"] != packet["packet_id"] or response["prompt_sha256"] != packet["resources"]["prompt"]["sha256"]:
        raise ValueError("Review does not match packet/prompt")
    reviewer = response["reviewer"]
    if reviewer["kind"] != packet["mode"] or reviewer["name"].strip() in ("", "UNSET"):
        raise ValueError("Reviewer identity/mode must be explicitly filled")
    if reviewer["kind"] == "agent" and (not reviewer["model"] or reviewer["model"].strip() in ("", "UNSET")):
        raise ValueError("Agent review must name the actual model; no model call is made by this program")
    if reviewer["kind"] == "human" and reviewer["model"] is not None:
        raise ValueError("Human review must have model=null")
    known = {ev["id"] for ev in packet["evidence"]}
    reviewed = set(response["reviewed_evidence_ids"])
    if not reviewed <= known:
        raise ValueError("Review cites unknown evidence")
    if sorted(check["id"] for check in response["checks"]) != sorted(CHECKS):
        raise ValueError("Every required check must occur exactly once")
    for item in response["checks"] + response["findings"]:
        if not item.get("explanation", item.get("description", "")).strip():
            raise ValueError("Review explanation must contain meaningful text")
        if not set(item["evidence_ids"]) <= reviewed:
            raise ValueError("Check/finding must cite evidence declared actually reviewed")
        if item.get("status") != "not_assessable" and not item["evidence_ids"]:
            raise ValueError("Assessable check or finding needs image evidence")
        ijk = item.get("coordinate_IJK")
        if ijk is not None and any(value >= size for value, size in zip(ijk, packet["geometry"]["shape"])):
            raise ValueError("Finding coordinate is out of bounds")
        if ijk is not None and not any(ev["id"] in item["evidence_ids"] and ijk[ev["axis"]] in ev["indices"] for ev in packet["evidence"]):
            raise ValueError("Finding coordinate must intersect a cited evidence page")
    clear = all(check["status"] == "no_visible_problem" for check in response["checks"])
    if response["recommendation"] == "suggest_pass" and (reviewed != known or not clear or response["findings"]):
        raise ValueError("suggest_pass requires all evidence declared reviewed, all checks clear, no unresolved findings")
    return {"declared_reviewed_pages": len(reviewed), "generated_pages": len(known),
            "all_pages_declared_reviewed": reviewed == known, "viewing_independently_verified": False}


def record_review(packet_path, response_path, output):
    packet = load_packet(packet_path)
    response = read_json(response_path)
    coverage = validate_response(packet, response)
    result = {"state": "review_recorded_requires_human_decision", "clinical_acceptance": False,
              "packet_path": str(Path(packet_path).resolve()), "packet_sha256": sha256(packet_path),
              "response_sha256": sha256(response_path), "response": response, "coverage": coverage,
              "finding_coordinates_RAS_mm": [nib.affines.apply_affine(packet["geometry"]["affine_RAS_mm"], finding["coordinate_IJK"]).tolist()
                  if finding["coordinate_IJK"] is not None else None for finding in response["findings"]]}
    out = fresh_output(output)
    write_json(out / "review.json", result)
    write_json(out / "decision.template.json", {
        "packet_id": packet["packet_id"], "review_sha256": sha256(out / "review.json"),
        "reviewer_name": "UNSET", "reviewer_role": "researcher", "decision": "request_more_review",
        "reviewed_evidence_ids": [], "confirmed_checks": [], "note": "待人工独立查看并填写", "clinical_acceptance": False})
    return result


def validate_human_decision(packet, review, decision, review_hash):
    required = {"packet_id", "review_sha256", "reviewer_name", "reviewer_role", "decision", "reviewed_evidence_ids", "confirmed_checks", "note", "clinical_acceptance"}
    if not isinstance(decision, dict) or set(decision) != required:
        raise ValueError("Use the exact human decision template")
    if decision["packet_id"] != packet["packet_id"] or decision["review_sha256"] != review_hash:
        raise ValueError("Decision does not match packet/review")
    if not isinstance(decision["reviewer_name"], str) or decision["reviewer_name"].strip() in ("", "UNSET"):
        raise ValueError("Human reviewer must be explicitly named")
    if decision["reviewer_role"] not in ("clinician", "researcher") or decision["clinical_acceptance"] is not False:
        raise ValueError("Human research decision only; clinical acceptance cannot be granted")
    if not isinstance(decision["note"], str) or not decision["note"].strip():
        raise ValueError("Human decision needs an explanatory note")
    known = {ev["id"] for ev in packet["evidence"]}
    for key, allowed in (("reviewed_evidence_ids", known), ("confirmed_checks", set(CHECKS))):
        values = decision[key]
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values) or len(values) != len(set(values)) or not set(values) <= allowed:
            raise ValueError(f"Invalid human decision {key}")
    if decision["decision"] not in ("approve_for_exploratory_drawem", "reject", "request_more_review"):
        raise ValueError("Invalid human decision")
    approved = decision["decision"] == "approve_for_exploratory_drawem"
    if approved and (review["response"]["recommendation"] != "suggest_pass" or
                     set(decision["reviewed_evidence_ids"]) != known or set(decision["confirmed_checks"]) != set(CHECKS)):
        raise ValueError("Approval requires clear complete review plus human full-evidence/checklist attestation")
    return approved


def decide_review(packet_path, review_path, decision_path, output):
    packet, review, decision = load_packet(packet_path), read_json(review_path), read_json(decision_path)
    if review["packet_sha256"] != sha256(packet_path):
        raise ValueError("Review packet hash mismatch")
    validate_response(packet, review["response"])
    approved = validate_human_decision(packet, review, decision, sha256(review_path))
    result = {"state": "approved_for_exploratory_drawem" if approved else decision["decision"],
              "ready_for_exploratory_drawem": approved, "clinical_acceptance": False,
              "regional_review_passed": False, "research_included": False,
              "packet_path": str(Path(packet_path).resolve()), "packet_sha256": sha256(packet_path),
              "review_path": str(Path(review_path).resolve()), "review_sha256": sha256(review_path),
              "human_decision_path": str(Path(decision_path).resolve()),
              "human_decision_sha256": sha256(decision_path), "human_decision": decision,
              "sources": packet["sources"], "identity_authenticated": False,
              "note": "Local self-attestation, not authenticated medical sign-off; no segmentation executed"}
    out = fresh_output(output)
    write_json(out / "decision.json", result)
    return result


def verify_decision(decision_path, image_path, mask_path):
    decision = read_json(decision_path)
    packet_path, review_path = Path(decision["packet_path"]), Path(decision["review_path"])
    packet = load_packet(packet_path)
    review = read_json(review_path)
    if decision["packet_sha256"] != sha256(packet_path) or decision["review_sha256"] != sha256(review_path):
        raise ValueError("Decision audit chain changed")
    if review["packet_sha256"] != sha256(packet_path):
        raise ValueError("Review is bound to another packet")
    validate_response(packet, review["response"])
    human = decision["human_decision"]
    if sha256(decision["human_decision_path"]) != decision["human_decision_sha256"] or read_json(decision["human_decision_path"]) != human:
        raise ValueError("Human decision evidence changed")
    approved = validate_human_decision(packet, review, human, sha256(review_path))
    if (decision["state"] != "approved_for_exploratory_drawem" or
        decision["ready_for_exploratory_drawem"] is not True or decision["clinical_acceptance"] is not False or
        not approved or review["response"]["recommendation"] != "suggest_pass" or
        set(human["reviewed_evidence_ids"]) != {ev["id"] for ev in packet["evidence"]} or set(human["confirmed_checks"]) != set(CHECKS)):
        raise ValueError("No complete human research approval")
    for key, path in (("image", image_path), ("mask", mask_path)):
        if sha256(path) != packet["sources"][key]["sha256"] or decision["sources"][key]["sha256"] != packet["sources"][key]["sha256"]:
            raise ValueError("Approval does not belong to these image/mask bytes")
    return {"status": "human_attested_exploratory_approval", "decision_sha256": sha256(decision_path),
            "identity_authenticated": False, "clinical_acceptance": False}


def demo_packet(output, mode="agent"):
    out = fresh_output(output)
    grid = np.indices((16, 18, 16)).astype(float)
    ellipsoid = sum(((grid[i]-[8, 9, 8][i])/[5, 6, 5][i])**2 for i in range(3))
    data = (20+100*np.exp(-ellipsoid)+8*np.sin(grid[1])).astype(np.float32)
    mask = (ellipsoid < 1).astype(np.uint8)
    mask[1, 1, 1] = 1  # synthetic island: must not be auto-deleted
    affine = np.diag([1., 1.5, 2., 1.])
    affine[:3, 3] = [-8, -13.5, -16]
    for name, values in (("synthetic_T2", data), ("synthetic_mask", mask)):
        image = nib.Nifti1Image(values, affine)
        image.header.set_xyzt_units("mm")
        image.set_qform(affine, code=1)
        image.set_sform(affine, code=1)
        nib.save(image, out / f"{name}.nii.gz")
    prepare_packet(out / "synthetic_T2.nii.gz", out / "synthetic_mask.nii.gz", out / "packet", mode)
    write_json(out / "SYNTHETIC_ONLY.json", {"patient_data": False, "model_called": False,
               "clinical_acceptance": False, "note": "Software demonstration, not model performance evaluation"})
