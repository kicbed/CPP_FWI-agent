"""Command-line entry point for the standalone Deepwave worker."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import numpy as np
import torch

from .acquisition import build_acquisition
from .artifacts import (
    build_manifest,
    build_partial_failure_manifest,
    prepare_run_dir,
    save_npy,
    validate_job_id,
    write_json,
)
from .config import configured_run_root, load_config
from .deepwave_2d import (
    forward_model,
    rough_first_arrival_check,
    small_model_gradient_check,
    validate_device,
)
from .inversion import InversionCheckpointState, run_inversion
from .job_state import JobState
from .metrics import calculate_metrics, environment_info
from .model_io import load_model, make_initial_model
from .plots import generate_all_plots
from worker_launch_control import (
    WorkerCancellationRequested,
    WorkerWallTimeExceeded,
)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolved_config_dict(config, metadata, geometry) -> dict[str, Any]:
    value = config.model_dump(mode="json")
    value.update(
        {
            "model_shape": list(metadata["shape"]),
            "axis_order": list(metadata["axis_order"]),
            "grid_spacing_deepwave_order_m": [config.dz_m, config.dx_m],
            "courant_number_2d_user_dt": config.courant_number,
            "courant_formula": "vmax*dt*sqrt(1/dz^2+1/dx^2)",
            "deepwave_time_handling": (
                "Deepwave scalar internally uses CFL-required time substeps while "
                "input/output remain sampled at dt_s."
            ),
            "synthetic_observed_data": True,
            "inverse_crime_validation": True,
            "gradient_check_required": config.preset == "fwi_demo",
            "initial_model_generation": (
                "Gaussian smoothing of slowness (1/v), followed by velocity "
                "bounds and configured top-row preservation."
            ),
            "pml_frequency_hz": config.source_frequency_hz,
            "source_locations_grid_zx": geometry.source_locations.tolist(),
            "receiver_locations_grid_zx": geometry.receiver_locations.tolist(),
            "source_x_m": geometry.source_x_m.tolist(),
            "receiver_x_m": geometry.receiver_x_m.tolist(),
            "plot_cell_extent_m": {
                "x": metadata["x_cell_extent_m"],
                "z": metadata["z_cell_extent_m"],
            },
        }
    )
    if "sha256" in metadata:
        value["validated_model_sha256"] = metadata["sha256"]
    if "source_sha256" in metadata:
        value["declared_source_mat_sha256"] = metadata["source_sha256"]
    return value


def validate_only(config_path: str) -> dict[str, Any]:
    _, config = load_config(config_path)
    validate_device(config.device)
    loaded = load_model(config)
    geometry = build_acquisition(config, loaded.velocity.shape)
    return {
        "type": "fwi_config_validation",
        "valid": True,
        "resolved": _resolved_config_dict(config, loaded.metadata, geometry),
        "environment": environment_info(config),
    }


def _write_loss_csv(path: Path, losses: list[float], frequency_hz: float) -> None:
    if path.parent.is_symlink() or path.parent.resolve(strict=True) != path.parent:
        raise ValueError("loss.csv parent cannot be a symbolic link")
    fd, temp_name = tempfile.mkstemp(prefix=".loss.csv.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["iteration", "frequency_hz", "loss"])
            for iteration, loss in enumerate(losses):
                writer.writerow(
                    [iteration, f"{frequency_hz:.12g}", f"{loss:.17g}"]
                )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _save_arrays(
    run_dir: Path,
    true_velocity: np.ndarray,
    inverted_velocity: np.ndarray,
    observed: np.ndarray,
    predicted: np.ndarray,
) -> None:
    values = {
        "models/inverted.npy": inverted_velocity,
        "models/error.npy": inverted_velocity - true_velocity,
        "data/predicted.npy": predicted,
        "data/residual.npy": predicted - observed,
    }
    for relative, value in values.items():
        if not np.isfinite(value).all():
            raise FloatingPointError(f"refusing to save non-finite artifact: {relative}")
        save_npy(run_dir / relative, value.astype(np.float32, copy=False))


def _check_cancel(cancel_check: Callable[[], None] | None) -> None:
    if cancel_check is not None:
        cancel_check()


def run_worker(
    command: str,
    config_path: str,
    requested_run_dir: str | None,
    *,
    managed_launch: bool = False,
    cancel_check: Callable[[], None] | None = None,
    checkpoint_barrier: (
        Callable[[Path, Any, JobState, InversionCheckpointState], None] | None
    ) = None,
) -> dict[str, Any]:
    raw, config = load_config(config_path)
    if command == "forward":
        config = config.model_copy(update={"iterations": 0})
    elif command == "invert" and config.iterations < 1:
        raise ValueError("invert requires an inversion preset or iterations >= 1")
    if requested_run_dir is not None:
        requested_path = Path(requested_run_dir)
        resolved_requested = requested_path.resolve(strict=False)
        if resolved_requested.exists() and Path(config_path).resolve(
            strict=True
        ) != (resolved_requested / "config.original.json").resolve(strict=True):
            raise ValueError(
                "an existing queued run must use its own config.original.json"
            )
    run_dir, job_id = prepare_run_dir(
        requested_run_dir,
        config.job_id,
        managed_launch=managed_launch,
    )
    config = config.model_copy(update={"job_id": job_id})
    state = JobState(run_dir, job_id)
    start = time.perf_counter()
    completed_losses: list[float] = []
    numerical_failure_reason: str | None = None
    try:
        _check_cancel(cancel_check)
        write_json(run_dir / "config.original.json", raw)
        state.update(
            "running",
            "validate_model",
            0,
            config.iterations,
            "Validating model sidecar, checksum, and acquisition geometry",
        )
        write_json(run_dir / "environment.json", environment_info(config))
        write_json(
            run_dir / "config.resolved.json",
            {
                **config.model_dump(mode="json"),
                "courant_number_2d_user_dt": config.courant_number,
                "resolution_stage": "before_model_validation",
            },
        )
        _write_loss_csv(run_dir / "loss.csv", [], config.source_frequency_hz)
        validate_device(config.device)
        _check_cancel(cancel_check)
        loaded = load_model(config)
        true_velocity = loaded.velocity
        initial_velocity = make_initial_model(true_velocity, config)
        geometry = build_acquisition(config, true_velocity.shape)
        resolved = _resolved_config_dict(config, loaded.metadata, geometry)
        write_json(run_dir / "config.resolved.json", resolved)
        save_npy(run_dir / "models/true.npy", true_velocity)
        save_npy(run_dir / "models/initial.npy", initial_velocity)
        _check_cancel(cancel_check)

        if config.device == "cuda":
            torch.cuda.reset_peak_memory_stats(0)
        state.update(
            "running",
            "generate_observed",
            0,
            config.iterations,
            "Generating synthetic observed data with the true model",
        )
        observed_result = forward_model(true_velocity, config, geometry)
        _check_cancel(cancel_check)
        observed = observed_result.receiver_amplitudes
        save_npy(run_dir / "data/observed.npy", observed)
        first_arrival = (
            rough_first_arrival_check(observed, config, geometry)
            if config.model_id == "homogeneous_48_96"
            else None
        )
        if first_arrival is not None and not first_arrival["passed"]:
            raise FloatingPointError("homogeneous rough first-arrival check failed")
        gradient_check: dict[str, Any] | None = None

        if command == "forward":
            state.update(
                "running",
                "forward_initial",
                0,
                0,
                "Generating comparison data with the initial model",
            )
            predicted = forward_model(initial_velocity, config, geometry).receiver_amplitudes
            residual = predicted - observed
            observed_energy = float(np.sum(observed.astype(np.float64) ** 2))
            loss = float(np.sum(residual.astype(np.float64) ** 2) / observed_energy)
            if not np.isfinite(loss):
                raise FloatingPointError("forward comparison loss is NaN or Inf")
            losses = [loss]
            inverted = np.array(initial_velocity, copy=True)
            gradient_clips: list[float] = []
            model_update_relative_l2 = 0.0
            state.append_iteration(0, config.source_frequency_hz, loss)
            completed_losses.append(loss)
            _write_loss_csv(
                run_dir / "loss.csv", completed_losses, config.source_frequency_hz
            )
        else:
            if config.preset == "fwi_demo":
                state.update(
                    "running",
                    "gradient_check",
                    0,
                    config.iterations,
                    "Running a small-model directional derivative check before demo FWI",
                )
                gradient_check = small_model_gradient_check(config.device)
                _check_cancel(cancel_check)
                state.append_progress(
                    {
                        "event": "gradient_check",
                        "job_id": job_id,
                        **gradient_check,
                    }
                )
                if not gradient_check["passed"]:
                    raise FloatingPointError(
                        "small-model directional derivative check failed"
                    )
            state.update(
                "running",
                "invert",
                0,
                config.iterations,
                "Running bounded L2 waveform inversion",
            )

            def progress(iteration: int, loss: float, clip: float | None) -> None:
                completed_losses.append(loss)
                _write_loss_csv(
                    run_dir / "loss.csv",
                    completed_losses,
                    config.source_frequency_hz,
                )
                state.append_iteration(iteration, config.source_frequency_hz, loss)
                state.update(
                    "running",
                    "invert",
                    min(iteration, config.iterations),
                    config.iterations,
                    f"FWI model state {iteration}/{config.iterations}; loss={loss:.8g}",
                    loss=loss,
                    gradient_clip_value=clip,
                )

            inversion = run_inversion(
                initial_velocity,
                observed,
                config,
                geometry,
                progress=progress,
                cancel_check=cancel_check,
                checkpoint=(
                    None
                    if checkpoint_barrier is None
                    else lambda checkpoint: checkpoint_barrier(
                        run_dir, config, state, checkpoint
                    )
                ),
            )
            inverted = inversion.inverted_velocity
            predicted = inversion.predicted_data
            residual = inversion.residual_data
            losses = inversion.losses
            gradient_clips = inversion.gradient_clip_values
            model_update_relative_l2 = inversion.model_update_relative_l2
            # A demo that does not reduce its objective is a numerical failure,
            # not an inversion success. The two-update smoke only asserts that
            # the differentiable update chain is finite and changes the model.
            if config.preset == "fwi_demo" and losses[-1] >= losses[0]:
                numerical_failure_reason = (
                    "fwi_demo final loss did not decrease below initial loss"
                )

        if config.device == "cuda":
            torch.cuda.synchronize(0)
        _check_cancel(cancel_check)
        _save_arrays(
            run_dir,
            true_velocity,
            inverted,
            observed,
            predicted,
        )
        _write_loss_csv(run_dir / "loss.csv", losses, config.source_frequency_hz)
        state.update(
            "running",
            "plot",
            config.iterations,
            config.iterations,
            "Generating and decoding result PNG artifacts",
        )
        figures, plot_details = generate_all_plots(
            run_dir=run_dir,
            true_velocity=true_velocity,
            initial_velocity=initial_velocity,
            inverted_velocity=inverted,
            observed=observed,
            predicted=predicted,
            residual=residual,
            losses=losses,
            metadata=loaded.metadata,
            config=config,
            geometry=geometry,
        )
        _check_cancel(cancel_check)
        elapsed = time.perf_counter() - start
        metrics = calculate_metrics(
            config=config,
            true_velocity=true_velocity,
            initial_velocity=initial_velocity,
            inverted_velocity=inverted,
            observed_data=observed,
            predicted_data=predicted,
            losses=losses,
            elapsed_seconds=elapsed,
            model_update_relative_l2=model_update_relative_l2,
            gradient_clip_values=gradient_clips,
            first_arrival_check=first_arrival,
            gradient_check=gradient_check,
        )
        if metrics["nan_count"] or metrics["inf_count"]:
            raise FloatingPointError("structured metrics found NaN or Inf artifacts")
        write_json(run_dir / "metrics.json", metrics)
        manifest = build_manifest(
            run_dir=run_dir,
            job_id=job_id,
            model_id=config.model_id,
            metrics=metrics,
            figures=figures,
            plot_details=plot_details,
            command=command,
            status="failed" if numerical_failure_reason else "succeeded",
            failure_reason=numerical_failure_reason,
        )
        write_json(run_dir / "manifest.json", manifest)
        if numerical_failure_reason:
            raise FloatingPointError(numerical_failure_reason)
        state.update(
            "succeeded",
            "complete",
            config.iterations,
            config.iterations,
            "All numerical and artifact validation checks passed",
            manifest_url=f"/fwi-artifacts/{job_id}/manifest.json",
        )
        return manifest
    except WorkerCancellationRequested:
        previous = state.read() or {}
        state.update(
            "cancelled",
            "cancelled",
            int(previous.get("iteration", 0)),
            config.iterations,
            "FWI Worker cancellation acknowledged",
        )
        raise
    except WorkerWallTimeExceeded:
        previous = state.read() or {}
        state.update(
            "failed",
            "failed",
            int(previous.get("iteration", 0)),
            config.iterations,
            "FWI Worker wall time exceeded",
            failure_code="WALL_TIME_EXCEEDED",
        )
        raise
    except Exception as error:
        state.append_log(traceback.format_exc().rstrip())
        try:
            metrics_path = run_dir / "metrics.json"
            if not metrics_path.exists():
                environment = environment_info(config)
                initial_completed_loss = (
                    float(completed_losses[0]) if completed_losses else None
                )
                final_completed_loss = (
                    float(completed_losses[-1]) if completed_losses else None
                )
                partial_metrics: dict[str, Any] = {
                    "status": "failed",
                    "partial": True,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "elapsed_seconds": time.perf_counter() - start,
                    "iterations": config.iterations,
                    "completed_model_states": max(0, len(completed_losses)),
                    "initial_loss": initial_completed_loss,
                    "final_loss": final_completed_loss,
                    "loss_reduction_fraction": (
                        (initial_completed_loss - final_completed_loss)
                        / initial_completed_loss
                        if initial_completed_loss is not None
                        and final_completed_loss is not None
                        and initial_completed_loss > 0
                        else None
                    ),
                    "initial_model_relative_l2": None,
                    "final_model_relative_l2": None,
                    "observed_predicted_relative_l2": None,
                    "nan_count": None,
                    "inf_count": None,
                    "torch_version": environment["torch_version"],
                    "deepwave_version": environment["deepwave_version"],
                    "device": config.device,
                    "device_name": environment["device_name"],
                    "peak_gpu_memory_mb": (
                        float(torch.cuda.max_memory_allocated(0) / (1024**2))
                        if config.device == "cuda" and torch.cuda.is_available()
                        else 0.0
                    ),
                }
                write_json(metrics_path, partial_metrics)
            else:
                partial_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            manifest_path = run_dir / "manifest.json"
            if not manifest_path.exists():
                write_json(
                    manifest_path,
                    build_partial_failure_manifest(
                        job_id=job_id,
                        model_id=config.model_id,
                        command=command,
                        metrics=partial_metrics,
                        error=error,
                    ),
                )
            state.update(
                "failed",
                "failed",
                int((state.read() or {}).get("iteration", 0)),
                config.iterations,
                f"{type(error).__name__}: {error}",
            )
        except Exception:
            state.append_log("Unable to update failed status:\n" + traceback.format_exc())
        raise


def read_status(run_dir_value: str) -> dict[str, Any]:
    requested = Path(run_dir_value)
    if not requested.is_absolute():
        raise ValueError("--run-dir must be absolute")
    root = configured_run_root().resolve(strict=True)
    run_dir = requested.resolve(strict=True)
    if run_dir == root or not _is_relative_to(run_dir, root):
        raise ValueError("run directory is outside FWI_RUN_ROOT")
    validate_job_id(run_dir.name)
    status_path = (run_dir / "status.json").resolve(strict=True)
    if not _is_relative_to(status_path, run_dir):
        raise ValueError("status path escaped run directory")
    value = json.loads(status_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("job_id") != run_dir.name:
        raise ValueError("status file does not match the requested job")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fwi_worker",
        description="Experimental Deepwave 2-D constant-density acoustic FWI worker",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--config", required=True)
    for name in ("forward", "invert"):
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("--config", required=True)
        command_parser.add_argument("--run-dir")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--run-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            result = validate_only(args.config)
        elif args.command == "status":
            result = read_status(args.run_dir)
        else:
            result = run_worker(args.command, args.config, args.run_dir)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(f"fwi_worker: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
