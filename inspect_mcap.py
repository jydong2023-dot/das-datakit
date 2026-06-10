#!/usr/bin/env python3
"""Parse an MCAP file and print the first frame of each topic."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.mcaploader import McapLoader, ns_to_s


def _summarize_array(arr: np.ndarray, max_elements: int = 16) -> dict:
    info: dict = {
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
    }
    if arr.size == 0:
        info["values"] = []
        return info
    flat = arr.reshape(-1)
    if flat.size <= max_elements:
        info["values"] = flat.tolist()
    else:
        info["min"] = float(np.min(arr))
        info["max"] = float(np.max(arr))
        info["mean"] = float(np.mean(arr))
        info["preview"] = flat[:max_elements].tolist()
    return info


def _protobuf_to_dict(msg: Message) -> dict:
    return MessageToDict(msg, preserving_proto_field_name=True)


def _format_first_frame(topic: str, frame: dict) -> dict:
    result: dict = {
        "topic": topic,
        "log_time_ns": frame.get("log_time"),
        "publish_time_ns": frame.get("publish_time"),
    }

    proto_msg = frame.get("data")
    if proto_msg is not None and hasattr(proto_msg, "header"):
        header = proto_msg.header
        result["header"] = {
            "timestamp_ns": getattr(header, "timestamp", None),
            "sequence_num": getattr(header, "sequence_num", None),
            "frame_id": getattr(header, "frame_id", None),
        }

    if "decode_data" in frame and frame["decode_data"] is not None:
        dd = frame["decode_data"]
        if isinstance(dd, np.ndarray):
            result["decode_data"] = _summarize_array(dd)
        else:
            result["decode_data"] = dd
    elif proto_msg is not None:
        result["proto"] = _protobuf_to_dict(proto_msg)

    return result


def inspect_mcap(mcap_path: str, output_json: str | None = None) -> dict:
    bag = McapLoader(mcap_path)

    summary = {
        "mcap_file": str(Path(mcap_path).resolve()),
        "duration_s": round(ns_to_s(bag.msg_end_time - bag.msg_start_time), 3),
        "topic_count": len(bag.all_topic_names),
        "topics": {},
    }

    for topic in sorted(bag.all_topic_names):
        frames = bag.get_topic_data(topic)
        if not frames:
            summary["topics"][topic] = {
                "message_count": 0,
                "first_frame": None,
                "note": "empty or unsupported topic",
            }
            continue

        first = _format_first_frame(topic, frames[0])
        summary["topics"][topic] = {
            "schema": bag.topic_schemas.get(topic),
            "frequency_hz": bag.topic_frequency_info.get(topic),
            "message_count": len(frames),
            "first_frame": first,
        }

    if output_json:
        out_path = Path(output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"Saved JSON to {out_path.resolve()}")

    return summary


def _print_summary(summary: dict) -> None:
    print("=" * 72)
    print(f"MCAP: {summary['mcap_file']}")
    print(f"Duration: {summary['duration_s']} s | Topics: {summary['topic_count']}")
    print("=" * 72)

    for topic, info in summary["topics"].items():
        print(f"\n[{topic}]")
        print(f"  schema: {info.get('schema')}")
        print(f"  count: {info.get('message_count')} | freq: {info.get('frequency_hz')} Hz")

        first = info.get("first_frame")
        if first is None:
            print(f"  note: {info.get('note', 'no data')}")
            continue

        if "header" in first:
            print(f"  header: {json.dumps(first['header'], ensure_ascii=False)}")

        if "decode_data" in first:
            print(f"  decode_data: {json.dumps(first['decode_data'], ensure_ascii=False)}")
        elif "proto" in first:
            print(f"  proto: {json.dumps(first['proto'], ensure_ascii=False)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Print first frame of each MCAP topic.")
    parser.add_argument(
        "mcap_file",
        nargs="?",
        default="data/00001.mcap",
        help="Path to MCAP file (default: data/00001.mcap)",
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="Optional path to save full output as JSON",
    )
    args = parser.parse_args()
    bag = McapLoader(args.mcap_file)
    topic_data = bag.get_topic_data("/robot0/sensor/depth/compressed")
    print(topic_data[0]["decode_data"])
    
    summary = inspect_mcap(args.mcap_file, output_json=args.json)
    _print_summary(summary)


if __name__ == "__main__":
    main()
