# 如何用 das-datakit 解析 MCAP 文件

本文档说明如何使用 `/home/djy/UMIData/das-datakit/` 下的代码解析 MCAP 文件，并以 `data/00001.mcap` 为例展示其中保存的数据。

---

## 1. 安装依赖

```bash
cd /home/djy/UMIData/das-datakit
pip install -r requirements.txt
pip install -e .
```

核心依赖：

| 包 | 用途 |
|----|------|
| `mcap` / `mcap_protobuf_support` | MCAP 读取与 Protobuf 解码 |
| `av` | H264 视频流解码（相机/深度） |
| `opencv-python` | 图像处理、视频导出 |
| `huecodec` | 深度图 HUE 编码解码（深度 topic 需要） |
| `numpy` / `protobuf` | 数值与消息序列化 |

---

## 2. 最快方式：官方 demo 脚本

```bash
cd /home/djy/UMIData/das-datakit
python mcap_decoder.py data/00001.mcap
```

脚本会：

1. 打印所有 topic 名称与 bag 统计信息
2. 解码 `/robot0/sensor/camera0/compressed` → 输出 `camera0_output.mp4`
3. 读取 `/robot0/vio/eef_pose` 位姿数据

核心代码见 `mcap_decoder.py`：

```python
from utils.mcaploader import McapLoader

bag = McapLoader(mcap_file)
print(bag.all_topic_names)
bag.load_topics(bag.all_topic_names, auto_sync=False)

camera0_img_data = bag.get_topic_data("/robot0/sensor/camera0/compressed")
vio_pose_data = bag.get_topic_data("/robot0/vio/eef_pose")
```

---

## 3. 推荐方式：使用 `McapLoader`

### 3.1 基本用法

```python
import sys
sys.path.insert(0, "/home/djy/UMIData/das-datakit")

from utils.mcaploader import McapLoader

bag = McapLoader("data/00001.mcap")

# 概览：topic 列表、时长、频率
print(bag.all_topic_names)
print(bag)

# 加载并解码所有 topic
bag.load_topics(bag.all_topic_names, auto_sync=False)

# 读取解码后的数据
camera = bag.get_topic_data("/robot0/sensor/camera0/compressed")
for frame in camera[:3]:
    img = frame["decode_data"]              # [H, W, 3] BGR uint8
    ts  = frame["data"].header.timestamp    # 纳秒时间戳

imu = bag.get_topic_data("/robot0/sensor/imu")
for frame in imu[:3]:
    data = frame["decode_data"]  # [6,]: 角速度 xyz + 线加速度 xyz

pose = bag.get_topic_data("/robot0/vio/eef_pose")
for frame in pose[:3]:
    data = frame["decode_data"]  # [7,]: x,y,z, qx,qy,qz,qw
```

### 3.2 单条消息结构

每条消息是一个 dict：

```python
{
    "data": <protobuf 原始消息>,
    "log_time": <int 纳秒>,
    "publish_time": <int 纳秒>,
    "decode_data": <numpy 数组>,  # auto_decompress 后才有
}
```

### 3.3 关键 API

| 方法 | 说明 |
|------|------|
| `McapLoader(path)` | 打开 MCAP，自动读取 topic 统计信息 |
| `bag.all_topic_names` | 所有 topic 名称 |
| `bag.load_topics(topics)` | 加载并可选自动解码 |
| `bag.get_topic_data(topic)` | 获取某 topic 全部帧（懒加载） |
| `bag.get_topic_frequency(topic)` | 估算频率 (Hz) |
| `bag.register_sync_relation_with_time(t1, t2)` | 按时间戳注册跨 topic 同步关系 |
| `bag.convert_depth_to_point_cloud(depth)` | 深度图 → 点云（需 depth topic） |

实现位于 `utils/mcaploader.py`，解码逻辑在 `utils/topic_parser.py`。

---

## 4. 支持的 Topic 与 decode_data 格式

README 中描述的完整 DAS 数据格式如下（具体 MCAP 文件可能只包含其中一部分）：

| Topic | decode_data 形状 | 含义 |
|-------|------------------|------|
| `/robot0/sensor/camera0/compressed` | `[H, W, 3]` uint8 | 中置鱼眼相机，BGR |
| `/robot0/sensor/camera1/compressed` | `[H, W, 3]` uint8 | 左立体相机 |
| `/robot0/sensor/camera2/compressed` | `[H, W, 3]` uint8 | 右立体相机 |
| `/robot0/sensor/depth/compressed` | `[H, W, 1]` float32 | 深度图（米） |
| `/robot0/sensor/imu` | `[6]` float | `[ωx, ωy, ωz, ax, ay, az]` |
| `/robot0/sensor/tactile_left/right` | `[N]` float | 触觉压力 |
| `/robot0/sensor/magnetic_encoder` | `[1]` float | 磁编码器（夹爪等） |
| `/robot0/vio/eef_pose` | `[7]` float | `[x,y,z, qx,qy,qz,qw]` |

---

## 5. `data/00001.mcap` 实际内容

### 5.1 文件概览

| 属性 | 值 |
|------|-----|
| 文件路径 | `data/00001.mcap` |
| 文件大小 | ~95 MB |
| 时长 | **97.3 秒** |
| Topic 数量 | **12 个** |
| 结构 | **双臂**（robot0 + robot1） |

时间范围（纳秒）：`1765432905975666000` ~ `1765433003283259000`

### 5.2 robot0（主臂，带 VIO）

| Topic | 频率 | 消息数 | Schema | 数据内容 |
|-------|------|--------|--------|----------|
| `/robot0/sensor/camera0/compressed` | 30.0 Hz | 2918 | CompressedImage | 鱼眼相机 H264→**1300×1600×3 BGR** |
| `/robot0/sensor/camera0/camera_info` | — | 1 | CameraCalibration | 内参 K，标定分辨率 640×480 |
| `/robot0/sensor/imu` | 198.7 Hz | 19335 | IMUMeasurement | **6 维** IMU |
| `/robot0/sensor/magnetic_encoder` | 50.0 Hz | 4864 | MagneticEncoderMeasurement | **1 维** 磁编码器 |
| `/robot0/vio/eef_pose` | 29.4 Hz | 2857 | PoseInFrame | **7 维** 末端位姿 |
| `/robot0/sim/robot_info` | 29.4 Hz | 2857 | RobotInfo | 仿真机器人状态 |
| `/robot0/system_info` | 0.8 Hz | 81 | SystemInfo | CPU/内存/版本等 |

**首帧样例：**

- IMU: `[-0.052, 0.008, -0.031, 0.080, 0.472, 0.859]`（角速度 + 线加速度）
- 磁编码器: `[0.102]`
- VIO 位姿: `[-0.139, 0.197, 0.021, -0.065, 0.518, 0.002, 0.853]`（xyz + 四元数）

### 5.3 robot1（从臂）

| Topic | 频率 | 消息数 | Schema | 数据内容 |
|-------|------|--------|--------|----------|
| `/robot1/sensor/camera0/compressed` | 30.0 Hz | 2918 | CompressedImage | **1300×1600×3 BGR** |
| `/robot1/sensor/camera0/camera_info` | — | 1 | CameraCalibration | 相机内参 |
| `/robot1/sensor/imu` | 198.3 Hz | 19297 | IMUMeasurement | **6 维** IMU |
| `/robot1/sensor/magnetic_encoder` | 50.0 Hz | 4865 | MagneticEncoderMeasurement | **1 维** 磁编码器 |
| `/robot1/system_info` | 0.8 Hz | 81 | SystemInfo | 系统信息 |

### 5.4 本文件未包含的 Topic

以下 topic 在 README 中有说明，但 **`00001.mcap` 中不存在**：

- 左右立体相机 `/robot0/sensor/camera1/compressed`、`camera2/compressed`
- 深度 `/robot0/sensor/depth/compressed`、`/robot0/sensor/depth/stereo_calibration`
- 触觉 `/robot0/sensor/tactile_left`、`tactile_right`
- robot1 的 VIO 位姿 `/robot1/vio/eef_pose`

---

## 6. 其他常用脚本

```bash
# 转为 H5（默认：camera0 + vio pose + action）
python mcap_to_h5.py --mcap-file data/00001.mcap

# 指定输出宽度
python mcap_to_h5.py --mcap-file data/00001.mcap --img-new-width 640

# 导出更多传感器到 H5
python mcap_to_h5.py --mcap-file data/00001.mcap --imu --stereo-camera --tactile

# 转为 MP4 + CSV（自动检测单臂/双臂）
python scripts/convert_mcap_to_mp4.py --mcap-file data/00001.mcap

# 深度点云导出（需 depth topic，本文件不适用）
python scripts/export_depth_pointcloud_ply.py data/00001.mcap
```

---

## 7. 一键 inspect 脚本

将以下内容保存为 `inspect_mcap.py`：

```python
#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/djy/UMIData/das-datakit")
from utils.mcaploader import McapLoader

bag = McapLoader(sys.argv[1])
print(bag)
for topic in sorted(bag.all_topic_names):
    data = bag.get_topic_data(topic)
    n = len(data) if data else 0
    sample = data[0] if n else None
    extra = ""
    if sample and sample.get("decode_data") is not None:
        dd = sample["decode_data"]
        extra = f" decode={getattr(dd, 'shape', dd)}"
    print(f"  {topic}: {n} msgs{extra}")
```

运行：

```bash
python inspect_mcap.py data/00001.mcap
```

---

## 8. 代码架构简述

```
das-datakit/
├── mcap_decoder.py          # 快速 demo：解码相机 + 位姿
├── mcap_to_h5.py            # MCAP → HDF5
├── utils/
│   ├── mcaploader.py        # 核心：McapLoader 类
│   └── topic_parser.py      # H264/IMU/pose 等解码器
├── pb2/                     # Protobuf 生成代码
└── proto/                   # .proto 定义
```

**数据流：**

```
MCAP 文件
  → NonSeekingReader (mcap)
  → parse_topic_data (Protobuf 反序列化)
  → _auto_decompress (topic_parser: H264→图像, 等)
  → decode_data (numpy)
```

---

## 9. 总结

`data/00001.mcap` 是一段约 **97 秒的双臂 DAS 采集数据**，包含：

- 两路 **30 Hz** 鱼眼相机（1300×1600 BGR）
- 两路 **~200 Hz** IMU
- 两路 **50 Hz** 磁编码器
- robot0 额外有 **VIO 末端位姿** 与 **仿真 robot_info**

解析入口：`utils/mcaploader.py` 的 `McapLoader`，或 `python mcap_decoder.py data/00001.mcap`。
