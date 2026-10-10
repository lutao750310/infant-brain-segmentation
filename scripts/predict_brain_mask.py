"""Optional nnUNet v1 adapter. Research only; requires trusted external weights."""
from pathlib import Path
import argparse
import inspect
import json

from neonatal_seg_audit.core import geometry, load_image, require_same_grid, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--model-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Fresh directory, never overwrites")
    args = parser.parse_args()
    inp, model, out = args.input.resolve(), args.model_directory.resolve(), args.output.resolve()
    if out.exists():
        raise FileExistsError("Output directory must be new")
    reference, _ = load_image(inp)
    source_hash = sha256(inp)
    if not model.is_dir():
        raise FileNotFoundError("Provide the installed v1 trainer directory")
    for fold in range(5):
        for name in ("model_final_checkpoint.model", "model_final_checkpoint.model.pkl"):
            if not (model / f"fold_{fold}" / name).is_file():
                raise FileNotFoundError(f"Missing fold_{fold}/{name}")
    # Import only when inference is explicitly requested; --help needs no PyTorch.
    import numpy as np
    import torch
    from nnunet.inference.predict import predict_cases
    if "disable_postprocessing" not in inspect.signature(predict_cases).parameters:
        raise RuntimeError("Installed nnUNet API cannot explicitly disable postprocessing")
    np.random.seed(0)
    torch.manual_seed(0)
    out.mkdir(parents=True, exist_ok=False)
    destination = out / "brain_mask.nii.gz"
    config = {"folds": [0, 1, 2, 3, 4], "tta": True, "step_size": 0.5,
              "mixed_precision": True, "seed": 0, "disable_postprocessing": True}
    record = {"state": "started_not_clinically_accepted", "research_only": True,
              "input": {"path": str(inp), "sha256": source_hash}, "model_directory": str(model),
              "config": config, "clinical_acceptance": False,
              "weights_source_and_license_must_be_verified_by_operator": True}
    with (out / "run.json").open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
    predict_cases(str(model), [[str(inp)]], [str(destination)],
                  folds=config["folds"], save_npz=False, num_threads_preprocessing=2,
                  num_threads_nifti_save=2, do_tta=True, mixed_precision=True,
                  overwrite_existing=False, all_in_gpu=False, step_size=0.5,
                  checkpoint_name="model_final_checkpoint", disable_postprocessing=True)
    mask_image, mask = load_image(destination, labels=True)
    require_same_grid(reference, mask_image)
    if not np.isin(mask, [0, 1]).all() or not mask.any():
        raise ValueError("Mask must be binary and nonempty; no cleanup or resampling is applied")
    if sha256(inp) != source_hash:
        raise RuntimeError("Input changed during inference")
    record.update(state="inference_and_geometry_complete_requires_manual_qc",
                  input_unchanged=True, mask_sha256=sha256(destination),
                  mask_geometry=geometry(mask_image), clinical_acceptance=False)
    (out / "run_complete.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
