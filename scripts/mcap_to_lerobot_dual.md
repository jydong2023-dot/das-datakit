# MCAP 转 LeRobot 3.0 数据集

本文档说明如何运行 `/home/UMIData/das-datakit/scripts/mcap_to_lerobot_dual.py`，将双机械臂 DAS MCAP 数据转换为 **LeRobot v3.0** 格式。

---

## 1. 环境准备

脚本依赖 **uv 虚拟环境** 中的 `lerobot`（不是 conda `emimic`）：

```bash
/home/djy/EgoVerse/emimic/bin/python -V
/home/EgoVerse/emimic/bin/python -c "import lerobot, mcap, av, pyarrow; print('OK')"
```

若缺少 MCAP 相关依赖，可用 `uv` 安装：

```bash
uv pip install --python /home/EgoVerse/emimic/bin/python \
  mcap mcap-protobuf-support pyarrow
```

> 上述 Python 路径为示例；需已安装 `lerobot` 的 Python 环境，请按本机实际路径替换。
---

## 2. 默认输入 / 输出

| 项 | 路径 |
|----|------|
| 输入目录 | `/home/UMIData/das-datakit/data/Folding_Clothes_and_Zipper_Operations/fold_and_store_clothes/00001` |
| 输出目录 | `/home/UMIData/das-datakit/data/Folding_Clothes_and_Zipper_Operations/fold_and_store_clothes/lerobot_v3_00001` |
| 异常记录 | `{output_dir}/abnormal_mcaps.json` |

输入目录下每个 `.mcap` 文件对应 **1 个 episode**。

---

## 3. 快速运行

使用默认路径（只转换 `fold_and_store_clothes/00001/` 下的 MCAP）：

```bash
cd /home/UMIData/das-datakit/scripts

/home/EgoVerse/emimic/bin/python mcap_to_lerobot_dual.py --overwrite
```

`--overwrite` 会在转换前删除已有输出目录；首次运行或重新转换时建议加上。

---

## 4. 自定义参数

```bash
/home/EgoVerse/emimic/bin/python mcap_to_lerobot_dual.py \
  --input-dir /path/to/your/mcap_folder \
  --output-dir /path/to/lerobot_output \
  --repo-id local/my_dataset \
  --fps 30 \
  --min-hz 15.0 \
  --robot-type das_dual_gripper \
  --overwrite
```

### 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--input-dir` | `.../fold_and_store_clothes/00001` | 包含 `.mcap` 的目录（会递归搜索） |
| `--output-dir` | `.../lerobot_v3_00001` | LeRobot v3.0 数据集输出目录 |
| `--exceptions-json` | `{output_dir}/abnormal_mcaps.json` | 异常 MCAP 及原因 |
| `--fps` | `30` | 数据集帧率 |
| `--min-hz` | `15.0` | 各 topic 最低频率，低于此值视为异常 |
| `--robot-type` | `das_dual_gripper` | 写入 metadata 的机器人类型 |
| `--repo-id` | `local/fold_and_store_clothes` | LeRobot 数据集 repo id |
| `--overwrite` | 关闭 | 若输出目录已存在则先删除再转换 |

查看完整帮助：

```bash
/home/EgoVerse/emimic/bin/python mcap_to_lerobot_dual.py --help
```

---

## 5. 数据要求

每条 MCAP 必须包含以下 **6 个 topic**，且频率 **≥ 15 Hz**：

| Topic | 映射 |
|-------|------|
| `/robot0/sensor/camera0/compressed` | `observation.images.wrist_left` |
| `/robot1/sensor/camera0/compressed` | `observation.images.wrist_right` |
| `/robot0/vio/eef_pose` | `observation.state`（前半） |
| `/robot0/sensor/magnetic_encoder` | `observation.state`（robot0 gripper） |
| `/robot1/vio/eef_pose` | `observation.state`（后半） |
| `/robot1/sensor/magnetic_encoder` | `observation.state`（robot1 gripper） |

- 缺少任意 topic，或任一 topic 低于 `--min-hz`：**跳过该 MCAP**，原因写入 `abnormal_mcaps.json`。
- **相机流**：从每个相机 topic 的 **首个可解码 H264 关键帧（SPS）** 起算，再参与对齐。
- **时间对齐**：在公共时间重叠区间内，以 **帧数最少** 的 topic 为主时间轴，其余 topic 最近邻对齐。
- **视频解码**：逐 packet 解码 H264（需先收到 SPS）；左右相机任一侧解码失败的帧会被丢弃，不参与写入。

---

## 6. LeRobot 字段说明

脚本在 `build_features()` 中注册以下字段，并在 `add_episode_to_dataset()` 中逐帧写入。

### Episode 元数据

| 字段 | dtype | shape | 默认值 / 规则 |
|------|-------|-------|---------------|
| `is_first` | bool | `(1,)` | 当前 episode **实际写入** 的第一帧为 `True`，其余为 `False` |
| `is_last` | bool | `(1,)` | 当前 episode **实际写入** 的最后一帧为 `True`，其余为 `False` |
| `is_terminal` | bool | `(1,)` | 固定为 `False` |
| `subtask` | string | `(1,)` | 固定为 `""` |
| `subtask_objects` | string | `(1,)` | 固定为 `"[]"` |
| `subtask_actors` | string | `(1,)` | 固定为 `"[]"` |

说明：

- `is_first` / `is_last` 基于 **H264 解码后仍保留的有效同步帧** 判断，而不是 MCAP 原始对齐帧序号。左右相机任一侧解码失败的帧会被丢弃，不参与写入，也不会被计为首/末帧。
- bool 字段写入时使用 `np.asarray([value], dtype=np.bool_)`（shape `(1,)`），以满足 LeRobot 对 `shape: (1,)` 特征的类型校验；不能直接传 Python `bool`。

### `observation.state`（16 维 float32）

按顺序拼接：

```
robot0_eef_pose(7) + robot0_magnetic_encoder(1)
+ robot1_eef_pose(7) + robot1_magnetic_encoder(1)
```

eef_pose 为 `[x, y, z, qx, qy, qz, qw]`。轴名依次为：

```
robot0_x, robot0_y, robot0_z, robot0_qx, robot0_qy, robot0_qz, robot0_qw, robot0_gripper,
robot1_x, robot1_y, robot1_z, robot1_qx, robot1_qy, robot1_qz, robot1_qw, robot1_gripper
```

### `action`

**下一帧的 `observation.state`**（在解码后仍保留的有效帧序列上取下一帧；**最后一帧**没有下一帧时，action 与当前帧 state 相同）。

这与 `add_episode_to_dataset()` 中的逻辑一致：`action[t] = state[t+1]`，末帧 `action[T] = state[T]`。

### `task`

从 MCAP 路径的 **倒数第二层目录名** 自动提取。例如：

```
.../fold_and_store_clothes/00001/00001.mcap  →  task = "fold_and_store_clothes"
```

---

## 7. 输出结构（LeRobot v3.0）

转换成功后，输出目录大致为：

```
lerobot_v3_00001/
├── meta/
│   ├── info.json
│   ├── tasks.jsonl
│   └── episodes/
├── data/
│   └── chunk-000/
├── videos/
│   └── ...
├── images/          # 转换过程中可能存在的中间 PNG
└── abnormal_mcaps.json
```

转换过程中，终端对每个 MCAP 会打印处理进度与耗时，例如：

```
[0] processing 00002.mcap
  wrote episode 000000: 1804 frames, task=fold_and_store_clothes
  elapsed: 412.35s
```

全部完成后打印 JSON 摘要，例如：

```json
{
  "valid_episodes": 5,
  "invalid_episodes": 0,
  "frames": 9004,
  "output_dir": ".../lerobot_v3_00001",
  "exceptions_json": ".../abnormal_mcaps.json",
  "image_shape": [1300, 1600, 3]
}
```

---

## 8. 验证数据集

用同一环境中的 `lerobot` 加载本地 v3 数据集：

```bash
/home/EgoVerse/emimic/bin/python - <<'PY'
from pathlib import Path
from lerobot.datasets.lerobot_dataset import LeRobotDataset

root = Path(
    "/home/UMIData/das-datakit/data/"
    "Folding_Clothes_and_Zipper_Operations/fold_and_store_clothes/lerobot_v3_00001"
)
ds = LeRobotDataset(repo_id="local/fold_and_store_clothes", root=root)
print("episodes:", ds.meta.total_episodes)
print("frames:", ds.meta.total_frames)
print("features:", list(ds.meta.features.keys()))
sample = ds[0]
print("task:", sample.get("task"))
print("state shape:", sample["observation.state"].shape)
print("is_first:", sample["is_first"], "is_last:", sample["is_last"])
print("is_terminal:", sample["is_terminal"])
print("subtask:", sample["subtask"])
print("subtask_objects:", sample["subtask_objects"])
print("subtask_actors:", sample["subtask_actors"])
PY
```

---

## 9. 单元测试

```bash
cd /home/UMIData/das-datakit/scripts
PYTHONPATH=scripts /home/EgoVerse/emimic/bin/python -m pytest -q test_mcap_to_lerobot_dual.py
```

测试覆盖 topic 校验、时间轴对齐、task 提取、action 构造，以及 episode 元数据字段（`is_first` / `is_last` / `is_terminal` / `subtask` 等）的写入格式。

---

## 10. 常见问题

### 输出目录已存在

```
FileExistsError: output exists: ...
```

加上 `--overwrite`，或改用新的 `--output-dir`。

### 转换很慢

每个 episode 需要解码 H264 相机流并写入 LeRobot（含视频编码），5 个 MCAP 可能需要 **数十分钟**，属正常现象。

### 终端出现 `non-existing PPS 0 referenced`

脚本已将 PyAV 日志设为 `PANIC`，并采用 **逐 packet、SPS 门控** 的 H264 解码器；若仍偶见解码警告，一般 **可忽略**。只要最终 `valid_episodes > 0` 且能正常加载数据集即可。

### 全部 MCAP 被跳过

查看 `abnormal_mcaps.json`，常见原因：

- 缺少 `/robot1/vio/eef_pose` 等必需 topic
- 某 topic 频率低于 15 Hz
- 各 topic 时间重叠区间内无有效帧
- 视频解码后 0 帧（或左右相机无法同步解码的有效帧）

### `is_first` / `is_last` / `is_terminal` 类型报错

若出现类似：

```
ValueError: The feature 'is_first' is not a 'np.ndarray'. Expected type is 'bool', but type '<class 'bool'>' provided instead.
```

说明 bool 字段需以 `shape (1,)` 的 `np.ndarray` 写入。当前脚本已通过 `bool_feature()` 处理；若自行修改写入逻辑，请保持该格式。

---

## 11. 示例：只转换 fold_and_store_clothes/00001

```bash
/home/EgoVerse/emimic/bin/python \
  /home/UMIData/das-datakit/scripts/mcap_to_lerobot_dual.py \
  --input-dir /home/UMIData/das-datakit/data/Folding_Clothes_and_Zipper_Operations/fold_and_store_clothes/00001 \
  --output-dir /home/UMIData/das-datakit/data/Folding_Clothes_and_Zipper_Operations/fold_and_store_clothes/lerobot_v3_00001 \
  --repo-id local/fold_and_store_clothes \
  --overwrite
```
