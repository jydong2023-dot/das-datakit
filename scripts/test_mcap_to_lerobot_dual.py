from pathlib import Path

import numpy as np
import pytest

from mcap_to_lerobot_dual import (
    EpisodeData,
    REQUIRED_TOPICS,
    VIDEO_KEY_LEFT,
    VIDEO_KEY_RIGHT,
    add_episode_to_dataset,
    build_action_from_state,
    build_features,
    compute_topic_frequency,
    get_task_name,
    select_master_timeline,
    validate_required_topics,
)


def test_validate_required_topics_reports_missing_and_low_frequency():
    topics = {
        REQUIRED_TOPICS[0]: [(0, "a"), (1_000_000_000, "b")],
        REQUIRED_TOPICS[1]: [(0, "a"), (1_000_000_000, "b")],
    }

    errors = validate_required_topics(topics, min_hz=15.0)

    assert any("missing topic" in err for err in errors)
    assert any(REQUIRED_TOPICS[0] in err and "below 15.0 Hz" in err for err in errors)


def test_compute_topic_frequency_uses_timestamp_range():
    entries = [(0, None), (500_000_000, None), (1_000_000_000, None)]

    assert compute_topic_frequency(entries) == pytest.approx(3.0)


def test_select_master_timeline_uses_fewest_stream_in_overlap():
    topics = {
        "fast": [(0, None), (1, None), (2, None), (3, None)],
        "slow": [(0, None), (2, None), (3, None)],
        "short": [(1, None), (2, None)],
    }

    master_name, master_ts, filtered = select_master_timeline(topics, ["fast", "slow", "short"])

    assert master_name == "slow"
    assert master_ts == [2]
    assert {k: len(v) for k, v in filtered.items()} == {"fast": 2, "slow": 1, "short": 2}


def test_get_task_name_uses_parent_of_episode_directory():
    path = Path(
        "/home/djy/UMIData/das-datakit/data/"
        "Folding_Clothes_and_Zipper_Operations/fold_and_store_clothes/00001/00005.mcap"
    )

    assert get_task_name(path) == "fold_and_store_clothes"


def test_build_action_from_state_uses_current_frame_state():
    state = np.arange(16, dtype=np.float32)

    action = build_action_from_state(state)

    assert action.dtype == np.float32
    assert action is not state
    np.testing.assert_array_equal(action, state)


def test_build_features_includes_standard_episode_metadata():
    features = build_features((480, 640, 3))

    assert features["is_first"] == {"dtype": "bool", "shape": (1,), "names": None}
    assert features["is_last"] == {"dtype": "bool", "shape": (1,), "names": None}
    assert features["is_terminal"] == {"dtype": "bool", "shape": (1,), "names": None}
    assert features["subtask"] == {"dtype": "string", "shape": (1,), "names": None}
    assert features["subtask_objects"] == {"dtype": "string", "shape": (1,), "names": None}
    assert features["subtask_actors"] == {"dtype": "string", "shape": (1,), "names": None}


def test_add_episode_to_dataset_writes_standard_episode_metadata(monkeypatch):
    class RecordingDataset:
        def __init__(self) -> None:
            self.frames = []
            self.saved = False

        def add_frame(self, frame):
            self.frames.append(frame)

        def save_episode(self):
            self.saved = True

    left_images = [np.full((2, 2, 3), fill_value=i, dtype=np.uint8) for i in range(3)]
    right_images = [np.full((2, 2, 3), fill_value=i + 10, dtype=np.uint8) for i in range(3)]

    def fake_decode_h264_chunk_list(h264_data):
        return left_images if h264_data == [b"left"] else right_images

    monkeypatch.setattr("mcap_to_lerobot_dual.decode_h264_chunk_list", fake_decode_h264_chunk_list)
    dataset = RecordingDataset()
    episode = EpisodeData(
        mcap_path=Path("episode.mcap"),
        task="fold_and_store_clothes",
        states=np.arange(48, dtype=np.float32).reshape(3, 16),
        left_h264=[b"left"],
        right_h264=[b"right"],
        master_topic=REQUIRED_TOPICS[0],
    )

    n_frames = add_episode_to_dataset(dataset, episode)

    assert n_frames == 3
    assert dataset.saved is True
    for frame in dataset.frames:
        assert frame["is_first"].shape == (1,)
        assert frame["is_first"].dtype == np.bool_
        assert frame["is_last"].shape == (1,)
        assert frame["is_last"].dtype == np.bool_
        assert frame["is_terminal"].shape == (1,)
        assert frame["is_terminal"].dtype == np.bool_
    assert [frame["is_first"].item() for frame in dataset.frames] == [True, False, False]
    assert [frame["is_last"].item() for frame in dataset.frames] == [False, False, True]
    assert [frame["is_terminal"].item() for frame in dataset.frames] == [False, False, False]
    assert [frame["subtask"] for frame in dataset.frames] == ["", "", ""]
    assert [frame["subtask_objects"] for frame in dataset.frames] == ["[]", "[]", "[]"]
    assert [frame["subtask_actors"] for frame in dataset.frames] == ["[]", "[]", "[]"]
