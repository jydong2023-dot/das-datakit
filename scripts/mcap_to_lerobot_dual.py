#!/usr/bin/env python3
"""Convert DAS dual-robot MCAP recordings to a LeRobot v3.0 dataset.

Each valid MCAP file becomes one episode. Required streams are synchronized to
the topic with the fewest frames inside the common timestamp overlap.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROBOT0_CAMERA = "/robot0/sensor/camera0/compressed"
ROBOT0_EEF = "/robot0/vio/eef_pose"
ROBOT0_GRIPPER = "/robot0/sensor/magnetic_encoder"
ROBOT1_CAMERA = "/robot1/sensor/camera0/compressed"
ROBOT1_EEF = "/robot1/vio/eef_pose"
ROBOT1_GRIPPER = "/robot1/sensor/magnetic_encoder"

REQUIRED_TOPICS = [
    ROBOT0_CAMERA,
    ROBOT0_EEF,
    ROBOT0_GRIPPER,
    ROBOT1_CAMERA,
    ROBOT1_EEF,
    ROBOT1_GRIPPER,
]

VIDEO_KEY_LEFT = "observation.images.wrist_left"
VIDEO_KEY_RIGHT = "observation.images.wrist_right"
STATE_DIM = 16
CHUNKS_SIZE = 1000
DEFAULT_INPUT_DIR = Path(
    "/home/djy/UMIData/das-datakit/data/"
    "Folding_Clothes_and_Zipper_Operations/fold_and_store_clothes/00001"
)
DEFAULT_OUTPUT_DIR = Path(
    "/home/djy/UMIData/das-datakit/data/"
    "Folding_Clothes_and_Zipper_Operations/fold_and_store_clothes/lerobot_v3_00001"
)

STATE_NAMES = [
    *(f"robot0_{name}" for name in ("x", "y", "z", "qx", "qy", "qz", "qw", "gripper")),
    *(f"robot1_{name}" for name in ("x", "y", "z", "qx", "qy", "qz", "qw", "gripper")),
]


@dataclass
class EpisodeData:
    mcap_path: Path
    task: str
    states: np.ndarray
    left_h264: list[bytes]
    right_h264: list[bytes]
    master_topic: str


def resolve_mcap_files(input_dir: Path) -> list[Path]:
    return sorted(path for path in input_dir.rglob("*.mcap") if path.is_file())


def get_task_name(mcap_path: Path) -> str:
    """Use the task directory name, e.g. fold_and_store_clothes/00001/00001.mcap."""
    return mcap_path.parent.parent.name


def build_action_from_state(state: np.ndarray) -> np.ndarray:
    return np.asarray(state, dtype=np.float32).copy()


def read_mcap_messages(mcap_path: Path) -> dict[str, list[tuple[int, Any]]]:
    from mcap.reader import make_reader
    from mcap_protobuf.decoder import DecoderFactory

    topics: dict[str, list[tuple[int, Any]]] = defaultdict(list)
    with mcap_path.open("rb") as f:
        reader = make_reader(f, decoder_factories=[DecoderFactory()])
        for _schema, channel, message, decoded in reader.iter_decoded_messages():
            topics[channel.topic].append((int(message.log_time), decoded))
    for entries in topics.values():
        entries.sort(key=lambda item: item[0])
    return dict(topics)


def compute_topic_frequency(entries: list[tuple[int, Any]]) -> float:
    if not entries:
        return 0.0
    if len(entries) == 1:
        return 1.0
    duration_s = (entries[-1][0] - entries[0][0]) / 1e9
    if duration_s <= 0:
        return 0.0
    return len(entries) / duration_s


def validate_required_topics(
    topics: dict[str, list[tuple[int, Any]]],
    min_hz: float,
    required_topics: list[str] | None = None,
) -> list[str]:
    required_topics = REQUIRED_TOPICS if required_topics is None else required_topics
    errors: list[str] = []
    for topic in required_topics:
        entries = topics.get(topic, [])
        if not entries:
            errors.append(f"missing topic: {topic}")
            continue
        freq = compute_topic_frequency(entries)
        if freq < min_hz:
            errors.append(f"topic {topic} frequency {freq:.2f} Hz below {min_hz:.1f} Hz")
    return errors


def filter_entries_by_time(entries: list[tuple[int, Any]], start_ns: int, end_ns: int) -> list[tuple[int, Any]]:
    return [(ts, msg) for ts, msg in entries if start_ns <= ts <= end_ns]


def select_master_timeline(
    topics: dict[str, list[tuple[int, Any]]],
    required_topics: list[str],
) -> tuple[str, list[int], dict[str, list[tuple[int, Any]]]]:
    streams = {topic: topics.get(topic, []) for topic in required_topics}
    if any(not entries for entries in streams.values()):
        missing = [topic for topic, entries in streams.items() if not entries]
        raise ValueError(f"cannot select master timeline; empty topics: {missing}")

    start_ns = max(entries[0][0] for entries in streams.values())
    end_ns = min(entries[-1][0] for entries in streams.values())
    if end_ns < start_ns:
        raise ValueError("required topics have no overlapping timestamp range")

    filtered = {topic: filter_entries_by_time(entries, start_ns, end_ns) for topic, entries in streams.items()}
    empty_after_overlap = [topic for topic, entries in filtered.items() if not entries]
    if empty_after_overlap:
        raise ValueError(f"topics have no frames in overlap: {empty_after_overlap}")

    master_topic = min(required_topics, key=lambda topic: len(filtered[topic]))
    return master_topic, [ts for ts, _msg in filtered[master_topic]], filtered


def nearest_lookup(target_ts: list[int], source_entries: list[tuple[int, Any]]) -> list[Any]:
    if not source_entries:
        return [None] * len(target_ts)
    src_ts = np.asarray([ts for ts, _msg in source_entries], dtype=np.int64)
    src_vals = [msg for _ts, msg in source_entries]
    targets = np.asarray(target_ts, dtype=np.int64)
    idxs = np.searchsorted(src_ts, targets, side="left")

    out = []
    for target, idx in zip(targets, idxs, strict=True):
        if idx <= 0:
            out.append(src_vals[0])
        elif idx >= len(src_ts):
            out.append(src_vals[-1])
        elif abs(src_ts[idx] - target) <= abs(src_ts[idx - 1] - target):
            out.append(src_vals[idx])
        else:
            out.append(src_vals[idx - 1])
    return out


def extract_pose(msg: Any) -> list[float]:
    pose = msg.pose
    return [
        float(pose.position.x),
        float(pose.position.y),
        float(pose.position.z),
        float(pose.orientation.x),
        float(pose.orientation.y),
        float(pose.orientation.z),
        float(pose.orientation.w),
    ]


def extract_gripper(msg: Any) -> float:
    return float(msg.value)


def find_first_keyframe(frames: list[tuple[int, Any]]) -> int:
    for i, (_ts, msg) in enumerate(frames):
        data = bytes(msg.data)
        if len(data) < 5:
            continue
        for start_code in (b"\x00\x00\x00\x01", b"\x00\x00\x01"):
            for nal in (0x67, 0x65):
                if start_code + bytes([nal]) in data[:100]:
                    return i
    return 0


def _nal_unit_type(data: bytes) -> int | None:
    if data.startswith(b"\x00\x00\x00\x01"):
        start_offset = 4
    elif data.startswith(b"\x00\x00\x01"):
        start_offset = 3
    else:
        return None
    if start_offset >= len(data):
        return None
    return data[start_offset] & 0x1F


class H264PacketDecoder:
    """Decode MCAP H264 packets one-by-one with a persistent codec context."""

    def __init__(self) -> None:
        import av

        av.logging.set_level(av.logging.PANIC)
        self._codec = av.CodecContext.create("h264", "r")
        self._has_sps = False

    def decode(self, data: bytes) -> np.ndarray | None:
        import av

        if not data:
            return None

        nal_type = _nal_unit_type(data)
        # Match das-datakit parser behavior: wait for SPS first.
        if nal_type == 7:
            self._has_sps = True
        if not self._has_sps:
            return None

        try:
            packet = av.Packet(data)
            for frame in self._codec.decode(packet):
                rgb = frame.to_ndarray(format="rgb24")
                return np.ascontiguousarray(rgb, dtype=np.uint8)
        except av.AVError:
            return None
        return None


def decode_h264_chunk_list(h264_data_list: list[bytes]) -> list[np.ndarray | None]:
    decoder = H264PacketDecoder()
    return [decoder.decode(data) for data in h264_data_list]


def get_first_rgb_shape(h264_data_list: list[bytes]) -> tuple[int, int, int]:
    for frame in decode_h264_chunk_list(h264_data_list):
        if frame is not None:
            return tuple(int(v) for v in frame.shape)
    raise ValueError("video decode produced zero frames")


def build_features(image_shape: tuple[int, int, int]) -> dict[str, Any]:
    return {
        "is_first": {
            "dtype": "bool",
            "shape": (1,),
            "names": None,
        },
        "is_last": {
            "dtype": "bool",
            "shape": (1,),
            "names": None,
        },
        "is_terminal": {
            "dtype": "bool",
            "shape": (1,),
            "names": None,
        },
        "subtask": {
            "dtype": "string",
            "shape": (1,),
            "names": None,
        },
        "subtask_objects": {
            "dtype": "string",
            "shape": (1,),
            "names": None,
        },
        "subtask_actors": {
            "dtype": "string",
            "shape": (1,),
            "names": None,
        },
        "observation.state": {
            "dtype": "float32",
            "shape": (STATE_DIM,),
            "names": {"axes": STATE_NAMES},
        },
        "action": {
            "dtype": "float32",
            "shape": (STATE_DIM,),
            "names": {"axes": STATE_NAMES},
        },
        VIDEO_KEY_LEFT: {
            "dtype": "video",
            "shape": image_shape,
            "names": ["height", "width", "channels"],
        },
        VIDEO_KEY_RIGHT: {
            "dtype": "video",
            "shape": image_shape,
            "names": ["height", "width", "channels"],
        },
    }


def bool_feature(value: bool) -> np.ndarray:
    return np.asarray([value], dtype=np.bool_)


def collect_episode_data(mcap_path: Path, min_hz: float) -> tuple[EpisodeData | None, list[str]]:
    topics = read_mcap_messages(mcap_path)
    errors = validate_required_topics(topics, min_hz=min_hz)
    if errors:
        return None, errors

    # Start camera streams from their first decodable keyframe.
    topics = dict(topics)
    topics[ROBOT0_CAMERA] = topics[ROBOT0_CAMERA][find_first_keyframe(topics[ROBOT0_CAMERA]) :]
    topics[ROBOT1_CAMERA] = topics[ROBOT1_CAMERA][find_first_keyframe(topics[ROBOT1_CAMERA]) :]

    try:
        master_topic, master_ts, filtered = select_master_timeline(topics, REQUIRED_TOPICS)
    except ValueError as exc:
        return None, [str(exc)]

    robot0_pose = [extract_pose(msg) for msg in nearest_lookup(master_ts, filtered[ROBOT0_EEF])]
    robot0_grip = [extract_gripper(msg) for msg in nearest_lookup(master_ts, filtered[ROBOT0_GRIPPER])]
    robot1_pose = [extract_pose(msg) for msg in nearest_lookup(master_ts, filtered[ROBOT1_EEF])]
    robot1_grip = [extract_gripper(msg) for msg in nearest_lookup(master_ts, filtered[ROBOT1_GRIPPER])]
    states = np.asarray(
        [p0 + [g0] + p1 + [g1] for p0, g0, p1, g1 in zip(robot0_pose, robot0_grip, robot1_pose, robot1_grip, strict=True)],
        dtype=np.float32,
    )

    left_h264 = [bytes(msg.data) for msg in nearest_lookup(master_ts, filtered[ROBOT0_CAMERA])]
    right_h264 = [bytes(msg.data) for msg in nearest_lookup(master_ts, filtered[ROBOT1_CAMERA])]
    if not left_h264 or not right_h264 or len(states) == 0:
        return None, ["no aligned frames after synchronization"]

    return (
        EpisodeData(
            mcap_path=mcap_path,
            task=get_task_name(mcap_path),
            states=states,
            left_h264=left_h264,
            right_h264=right_h264,
            master_topic=master_topic,
        ),
        [],
    )


def add_episode_to_dataset(dataset: Any, episode: EpisodeData) -> int:
    left_frames = decode_h264_chunk_list(episode.left_h264)
    right_frames = decode_h264_chunk_list(episode.right_h264)
    valid_indices = [
        idx for idx, (l_img, r_img) in enumerate(zip(left_frames, right_frames, strict=True)) if l_img is not None and r_img is not None
    ]
    if not valid_indices:
        raise ValueError("video decode produced zero valid synchronized frames")

    n_frames = 0
    for i, idx in enumerate(valid_indices):
        next_idx = valid_indices[i + 1] if i + 1 < len(valid_indices) else idx
        state = np.asarray(episode.states[idx], dtype=np.float32)
        action_state = np.asarray(episode.states[next_idx], dtype=np.float32)
        left_image = left_frames[idx]
        right_image = right_frames[idx]
        assert left_image is not None and right_image is not None
        dataset.add_frame(
            {
                "task": episode.task,
                "is_first": bool_feature(i == 0),
                "is_last": bool_feature(i == len(valid_indices) - 1),
                "is_terminal": bool_feature(False),
                "subtask": "",
                "subtask_objects": "[]",
                "subtask_actors": "[]",
                VIDEO_KEY_LEFT: left_image,
                VIDEO_KEY_RIGHT: right_image,
                "observation.state": state,
                "action": build_action_from_state(action_state),
            }
        )
        n_frames += 1
    if n_frames <= 0:
        raise ValueError("video decode produced zero frames")
    dataset.save_episode()
    return n_frames


def convert_dataset(
    input_dir: Path,
    output_dir: Path,
    exceptions_json: Path,
    fps: int,
    min_hz: float,
    robot_type: str,
    repo_id: str,
    overwrite: bool,
) -> dict[str, Any]:
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    mcap_files = resolve_mcap_files(input_dir)
    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(f"output exists: {output_dir}. Use --overwrite to replace it.")
        shutil.rmtree(output_dir)
    if exceptions_json.parent != output_dir:
        exceptions_json.parent.mkdir(parents=True, exist_ok=True)

    dataset = None
    image_shape: tuple[int, int, int] | None = None
    valid_episodes = 0
    exceptions: list[dict[str, Any]] = []
    total_frames = 0

    for mcap_path in mcap_files:
        mcap_start = time.perf_counter()
        print(f"[{valid_episodes}] processing {mcap_path.name}")
        try:
            episode, errors = collect_episode_data(mcap_path=mcap_path, min_hz=min_hz)
        except Exception as exc:  # Keep batch conversion moving and report the file.
            episode, errors = None, [f"{type(exc).__name__}: {exc}"]

        if errors or episode is None:
            print(f"  skipped: {'; '.join(errors) if errors else 'no frames'}")
            print(f"  elapsed: {time.perf_counter() - mcap_start:.2f}s")
            exceptions.append({"name": mcap_path.name, "path": str(mcap_path), "reasons": errors or ["no frames"]})
            continue

        try:
            if dataset is None:
                image_shape = get_first_rgb_shape(episode.left_h264)
                dataset = LeRobotDataset.create(
                    repo_id=repo_id,
                    robot_type=robot_type,
                    fps=fps,
                    features=build_features(image_shape),
                    root=output_dir,
                    use_videos=True,
                )
            n_frames = add_episode_to_dataset(dataset, episode)
        except Exception as exc:
            print(f"  skipped: {type(exc).__name__}: {exc}")
            print(f"  elapsed: {time.perf_counter() - mcap_start:.2f}s")
            exceptions.append({"name": mcap_path.name, "path": str(mcap_path), "reasons": [f"{type(exc).__name__}: {exc}"]})
            continue

        valid_episodes += 1
        total_frames += n_frames
        print(f"  wrote episode {valid_episodes - 1:06d}: {n_frames} frames, task={episode.task}")
        print(f"  elapsed: {time.perf_counter() - mcap_start:.2f}s")

    exceptions_json.parent.mkdir(parents=True, exist_ok=True)
    exceptions_json.write_text(json.dumps(exceptions, indent=2, ensure_ascii=False), encoding="utf-8")
    if dataset is None or valid_episodes == 0:
        raise RuntimeError(f"no valid episodes produced. See {exceptions_json}")
    dataset.finalize()
    return {
        "valid_episodes": valid_episodes,
        "invalid_episodes": len(exceptions),
        "frames": total_frames,
        "output_dir": str(output_dir),
        "exceptions_json": str(exceptions_json),
        "image_shape": image_shape,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert DAS dual MCAP files to LeRobot v3.0 format.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--exceptions-json", type=Path, default=None)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--min-hz", type=float, default=15.0)
    parser.add_argument("--robot-type", default="das_dual_gripper")
    parser.add_argument("--repo-id", default="local/fold_and_store_clothes")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    exceptions_json = args.exceptions_json or (args.output_dir / "abnormal_mcaps.json")
    try:
        result = convert_dataset(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            exceptions_json=exceptions_json,
            fps=args.fps,
            min_hz=args.min_hz,
            robot_type=args.robot_type,
            repo_id=args.repo_id,
            overwrite=args.overwrite,
        )
    except KeyboardInterrupt:
        print("Interrupted by user. Partial output may exist.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
