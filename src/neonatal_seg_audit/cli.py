"""Portable CLI. New output directories only; all input files remain untouched."""
from pathlib import Path
import argparse
import csv
import html
import json
import os
import platform
import shlex
import sys

import nibabel as nib
import numpy as np
import scipy

from . import __version__
from .core import (agreement, geometry, label_mask, load_image, mask_stats,
                   regional_volumes, require_same_grid, sha256)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def new_output(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def resolve(base, value):
    p = Path(value).expanduser()
    return (base / p).resolve() if not p.is_absolute() else p.resolve()


def compare(config_path, output):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    base = config_path.parent
    names = list(config["candidates"])
    if not names or any(n in ("reference", "image") for n in names):
        raise ValueError("Provide at least one candidate; reference and image are reserved names")
    paths = {"reference": resolve(base, config["reference"])}
    paths.update({name: resolve(base, spec["path"]) for name, spec in config["candidates"].items()})
    if config.get("image"):
        paths["image"] = resolve(base, config["image"])
    hashes = {name: sha256(p) for name, p in paths.items()}
    images, arrays = {}, {}
    for name, p in paths.items():
        images[name], arrays[name] = load_image(p, labels=name != "image", assume_mm=config.get("assume_mm", False))
    ref = images["reference"]
    for im in images.values():
        require_same_grid(ref, im)
    regions = config["regions"]
    if not regions:
        raise ValueError("No regions configured")
    masks = {"reference": {roi: label_mask(arrays["reference"], ids) for roi, ids in regions.items()}}
    for name, spec in config["candidates"].items():
        mapping = spec.get("regions", regions)
        if set(mapping) != set(regions):
            raise ValueError("Candidate region names must match the reference region names")
        masks[name] = {roi: label_mask(arrays[name], mapping[roi]) for roi in regions}
    report = {
        "package_version": __version__,
        "reference_status": config.get("reference_status", "uncertain; not adjudicated ground truth"),
        "clinical_acceptance": False,
        "metric_definition": "Native-grid overlap; volume from abs(det(affine))/1000. Boundary voxel centres "
            "use 6-neighbour erosion and physical RAS distances; HD95 is max of the two directed percentiles.",
        "runtime": {"python": platform.python_version(), "numpy": np.__version__, "nibabel": nib.__version__, "scipy": scipy.__version__},
        "sources": {name: {"path": str(p), "sha256": hashes[name], "geometry": geometry(images[name])} for name, p in paths.items()},
        "config_sha256": sha256(config_path),
        "config": config,
        "stats": {name: {roi: mask_stats(m, ref.affine) for roi, m in mm.items()} for name, mm in masks.items()},
        "comparisons": {},
    }
    rows = []
    for name in names:
        report["comparisons"][name] = {}
        for roi in regions:
            result = agreement(masks[name][roi], masks["reference"][roi], ref.affine)
            report["comparisons"][name][roi] = result
            rows.append({"candidate": name, "region": roi,
                         "reference_mL": report["stats"]["reference"][roi]["volume_mL"],
                         "candidate_mL": report["stats"][name][roi]["volume_mL"],
                         "Dice_agreement_not_accuracy": result["Dice_agreement_not_accuracy"],
                         "centroid_distance_mm_not_anatomical_error": result["centroid_distance_mm_not_anatomical_error"],
                         "symmetric_mean_boundary_distance_mm": result["symmetric_mean_boundary_distance_mm"],
                         "HD95_max_directed_percentiles_mm": result["HD95_max_directed_percentiles_mm"]})
    out = new_output(output)
    write_json(out / "comparison.json", report)
    with (out / "comparison.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    figure_names = []
    if "image" in arrays:
        figure_names = render_figures(out, images["image"], arrays["image"], masks, regions)
    content = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Segmentation agreement</title><style>body{font:16px sans-serif;max-width:1200px;margin:30px auto}td,th{border:1px solid #bbb;padding:8px}table{border-collapse:collapse}img{width:100%}@media print{img{break-inside:avoid}}</style><h1>Native-grid segmentation agreement</h1>'
    content += "<p>" + html.escape(report["reference_status"]) + "</p><p>Research only. Agreement is not accuracy; centroid differences are not confirmed anatomical displacement. No labels were cleaned or resampled.</p>"
    content += "<table><tr>" + "".join("<th>" + html.escape(k) + "</th>" for k in rows[0]) + "</tr>"
    for row in rows:
        content += "<tr>" + "".join("<td>" + html.escape(str(v)) + "</td>" for v in row.values()) + "</tr>"
    content += "</table>"
    for name in figure_names:
        content += f'<p><img src="{name}" alt="full native-axis view and ROI detail"></p>'
    (out / "report.html").write_text(content + "</html>\n", encoding="utf-8")
    for name, p in paths.items():
        if sha256(p) != hashes[name]:
            raise RuntimeError("Input changed during comparison; discard output and investigate")
    write_json(out / "input_integrity.json", {"all_inputs_unchanged": True, "sha256": hashes})
    return report


def render_figures(out, image, data, masks, regions):
    """Native IJK planes, not assumed anatomical planes; same crop for all overlays."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.patches import Rectangle
    names = list(masks)
    union = np.logical_or.reduce([m for mm in masks.values() for m in mm.values()])
    if not union.any():
        return []
    points = np.argwhere(union)
    lo = np.maximum(points.min(0) - 5, 0)
    hi = np.minimum(points.max(0) + 6, data.shape)
    center = np.rint(points.mean(0)).astype(int)
    finite = data[np.isfinite(data)]
    positive = finite[finite > 0]
    vmin, vmax = np.percentile(positive if positive.size else finite, [1, 99])
    colors = ["#ffad42", "#2ee1ff", "#ff4db8", "#6cf07b", "#b7a5ff", "#ffffff"]
    written = []
    for axis in range(3):
        idx = int(center[axis])
        other = [i for i in range(3) if i != axis]
        rows = [None] + names
        fig, axes = plt.subplots(len(rows), 2, figsize=(10, 3 * len(rows)), squeeze=False)
        plane = np.take(data, idx, axis=axis).T
        y0, x0 = int(lo[other[1]]), int(lo[other[0]])
        y1, x1 = int(hi[other[1]]), int(hi[other[0]])
        for row, name in enumerate(rows):
            for col in range(2):
                ax = axes[row, col]
                ax.imshow(plane, cmap="gray", origin="lower", vmin=vmin, vmax=vmax)
                if name:
                    for rindex, roi in enumerate(regions):
                        m = np.take(masks[name][roi], idx, axis=axis).T
                        if m.any() and not m.all():
                            ax.contour(m.astype(float), levels=[.5], colors=[colors[rindex % len(colors)]], linewidths=.9)
                if col == 0:
                    ax.add_patch(Rectangle((x0-.5, y0-.5), x1-x0, y1-y0, fill=False, edgecolor="yellow"))
                else:
                    ax.set_xlim(x0-.5, x1-.5)
                    ax.set_ylim(y0-.5, y1-.5)
                ax.set_title(f'{name or "Image only"}: {"full" if col == 0 else "detail"}')
                ax.set_xlabel(f"native index axis {other[0]}")
                ax.set_ylabel(f"native index axis {other[1]}")
        ras = nib.affines.apply_affine(image.affine, center)
        color_key = "; ".join(f"{roi}={colors[i % len(colors)]}" for i, roi in enumerate(regions))
        fig.suptitle(f"Native plane axis {axis}, index {idx}; center RAS {np.round(ras, 2)} mm\n{color_key}\nDisplay only: index planes may be oblique, not anatomical coronal/axial/sagittal.", fontsize=10)
        fig.tight_layout(rect=(0, 0, 1, .94))
        name = f"qc_native_axis_{axis}.png"
        fig.savefig(out / name, dpi=130)
        plt.close(fig)
        written.append(name)
    return written


def demo(output):
    out = new_output(output)
    grid = np.indices((40, 48, 40)).astype(float)
    ellipsoid = sum(((grid[i] - [20, 24, 20][i]) / [17, 20, 17][i]) ** 2 for i in range(3))
    scalar = np.where(ellipsoid < 1, 100 + 20 * np.cos(grid[1] / 3), 0).astype(np.float32)
    ref = np.zeros(scalar.shape, np.uint16)
    for label, x in ((1, 13), (2, 27)):
        roi = ((grid[0]-x)/3)**2 + ((grid[1]-22)/7)**2 + ((grid[2]-16)/3)**2 < 1
        ref[roi] = label
    a = np.roll(ref, 1, axis=1)
    b = ref.copy()
    b[:, 17:19, :] = 0
    affine = np.diag([1., 1., 1., 1.])
    affine[:3, 3] = [-20, -24, -20]
    for name, data in (("synthetic_image", scalar), ("reference", ref), ("candidate_A", a), ("candidate_B", b)):
        im = nib.Nifti1Image(data, affine)
        im.header.set_xyzt_units("mm")
        im.set_qform(affine, code=1)
        im.set_sform(affine, code=1)
        nib.save(im, out / f"{name}.nii.gz")
    cfg = {"image": "synthetic_image.nii.gz", "reference": "reference.nii.gz",
           "reference_status": "SYNTHETIC example; no patient data; not a clinical validation",
           "regions": {"Left": [1], "Right": [2]},
           "candidates": {"A": {"path": "candidate_A.nii.gz"}, "B": {"path": "candidate_B.nii.gz"}}}
    write_json(out / "config.json", cfg)
    compare(out / "config.json", out / "comparison")


def drawem_plan(image, mask, workdir, age, threads, image_digest, output, qc_decision=None):
    if not np.isfinite(age) or not 28 <= age <= 44:
        raise ValueError("PMA must be continuously within 28..44 weeks; never silently clamp")
    if threads < 1:
        raise ValueError("threads must be positive")
    import re
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
        raise ValueError("Supply a pinned Docker sha256 digest")
    im, _ = load_image(image)
    mm, data = load_image(mask, labels=True)
    require_same_grid(im, mm)
    if not np.isin(data, [0, 1]).all() or not data.any():
        raise ValueError("Brain mask must be non-empty binary 0/1")
    work = Path(workdir).resolve()
    if work.exists() and any(work.iterdir()):
        raise ValueError("Work directory must be fresh: never reuse old masked N4/priors/transforms")
    gate = {"status": "not_provided_unapproved_plan", "clinical_acceptance": False}
    if qc_decision:
        from .mask_review import verify_decision
        gate = verify_decision(qc_decision, image, mask)
    # Frozen historical entry point; verify the pinned image locally before executing.
    shell = (". /etc/fsl/fsl.sh; . /usr/src/structural-pipeline/parameters/path.sh; "
             f"timeout --signal=TERM --kill-after=30s 14400 mirtk neonatal-segmentation "
             f"/input/case.nii.gz {int(np.rint(age))} -d /work -t {threads} -c 0 -p 0 -v 1")
    cmd = ["docker", "run", "--rm", "--network", "none", "--cpus", str(threads), "--memory", "32g",
           "--user", f"{os.getuid()}:{os.getgid()}", "-e", f"OMP_NUM_THREADS={threads}",
           "-e", f"ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS={threads}",
           "-v", f"{Path(image).resolve()}:/input/case.nii.gz:ro", "-v", f"{work}:/work",
           "--workdir", "/work", "--entrypoint", "/bin/bash", image_digest, "-c", shell]
    plan = {"dry_run_only": True, "executed": False, "clinical_acceptance": False,
            "mask_qc_gate": gate, "ready_for_exploratory_drawem": bool(qc_decision),
            "pma_weeks_continuous": age, "pma_template_rounded": int(np.rint(age)),
            "input": {"path": str(Path(image).resolve()), "sha256": sha256(image)},
            "mask": {"path": str(Path(mask).resolve()), "sha256": sha256(mask)},
            "fresh_workdir": str(work), "template_command_not_executed": shlex.join(cmd),
            "required_before_execution": [
                "Clinician/scientific QC gate: brain coverage, eyes/scalp exclusion, brainstem/cerebellum, CSF compatibility.",
                "Verify FSL/path.sh and mirtk neonatal-segmentation in the pinned image; this environment is not universal.",
                "Copy the approved mask byte-for-byte to fresh workdir/segmentations/case_brain_mask.nii.gz BEFORE preprocessing.",
                "Confirm upstream preprocess skips BET when this mask exists and derives fresh N4/priors/registrations.",
                "Do not replace formal cohort results or resume any queue automatically."]}
    out = new_output(output)
    write_json(out / "drawem_plan.json", plan)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("compare")
    p.add_argument("--config", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("demo")
    p.add_argument("--output", required=True)
    p = sub.add_parser("volumes")
    p.add_argument("--labels", required=True)
    p.add_argument("--assume-mm", action="store_true")
    p.add_argument("--output", required=True)
    p = sub.add_parser("plan-drawem")
    for flag in ("image", "mask", "workdir", "image-digest", "output"):
        p.add_argument("--" + flag, required=True)
    p.add_argument("--pma", type=float, required=True)
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--mask-qc-decision", help="Validated human technical decision; without it the dry plan is explicitly unapproved")
    p = sub.add_parser("mask-qc")
    for flag in ("image", "mask", "output"):
        p.add_argument("--"+flag, required=True)
    p.add_argument("--mode", choices=("human", "agent"), default="agent")
    p.add_argument("--planes-per-page", type=int, default=5)
    p = sub.add_parser("record-mask-review")
    for flag in ("packet", "response", "output"):
        p.add_argument("--"+flag, required=True)
    p = sub.add_parser("decide-mask-qc")
    for flag in ("packet", "review", "decision", "output"):
        p.add_argument("--"+flag, required=True)
    p = sub.add_parser("mask-qc-demo")
    p.add_argument("--output", required=True)
    p.add_argument("--mode", choices=("human", "agent"), default="agent")
    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            compare(args.config, args.output)
        elif args.command == "demo":
            demo(args.output)
        elif args.command == "volumes":
            before = sha256(args.labels)
            im, data = load_image(args.labels, labels=True, assume_mm=args.assume_mm)
            result = regional_volumes(im, data)
            result["source"] = {"path": str(Path(args.labels).resolve()), "sha256": before}
            out = new_output(args.output)
            write_json(out / "volumes.json", result)
            with (out / "volumes.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(result["regions"][0]))
                writer.writeheader()
                writer.writerows(result["regions"])
            if before != sha256(args.labels):
                raise RuntimeError("Input changed during volume calculation")
        elif args.command == "plan-drawem":
            drawem_plan(args.image, args.mask, args.workdir, args.pma, args.threads, args.image_digest, args.output, args.mask_qc_decision)
        elif args.command == "mask-qc":
            from .mask_review import prepare_packet
            prepare_packet(args.image, args.mask, args.output, args.mode, args.planes_per_page)
        elif args.command == "record-mask-review":
            from .mask_review import record_review
            record_review(args.packet, args.response, args.output)
        elif args.command == "decide-mask-qc":
            from .mask_review import decide_review
            decide_review(args.packet, args.review, args.decision, args.output)
        elif args.command == "mask-qc-demo":
            from .mask_review import demo_packet
            demo_packet(args.output, args.mode)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(f"Completed {args.command}: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
